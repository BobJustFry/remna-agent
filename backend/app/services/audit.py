"""Проверки состояния парка: только чтение, ничего не меняют.

Каждая проверка выросла из реальной поломки, а не придумана впрок — что именно
случилось, написано в `why` у каждой записи реестра. Раздел задуман так, чтобы
поломку было видно до того, как о ней скажет пользователь.

Ничего не запускается само: фонового цикла здесь нет, проверки выполняются
только по явной команде из панели.
"""
from __future__ import annotations

import concurrent.futures
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from cryptography import x509
from cryptography.x509.oid import NameOID

from app.services.remnawave_api import RemnawaveApiError, rw_api

# Домены, за которыми стоит чужой CDN. Если такой перестал резолвиться —
# ресурс снят или аккаунт заблокирован, и хост отдаёт клиентам ошибку.
CDN_SUFFIXES = (".a.trbcdn.net", ".selcdn.net", ".gcdn.co", ".b-cdn.net", ".trbcdn.net")

# Сколько суток до конца сертификата считаем поводом для тревоги.
CERT_WARN_DAYS = 21
CERT_CRIT_DAYS = 7

# Хосты-баннеры («ПРИОБРЕТИТЕ ПОДПИСКУ» и подобные) — это текст в подписке, а не
# сервер. Их адресом ставят публичный резолвер, на котором заведомо нет нашего
# сервиса: проверять там нечего.
PLACEHOLDER_ADDRS = {"1.1.1.1", "8.8.8.8", "9.9.9.9", "77.88.8.8", "0.0.0.0", "127.0.0.1"}

NET_TIMEOUT = 6.0
WORKERS = 8

Severity = str  # "crit" | "warn" | "info"


@dataclass
class Finding:
    severity: Severity
    target: str
    title: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"severity": self.severity, "target": self.target,
                "title": self.title, "detail": self.detail}


@dataclass
class CheckResult:
    ran_at: str | None = None
    duration_ms: int = 0
    error: str | None = None
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ran_at": self.ran_at, "duration_ms": self.duration_ms,
                "error": self.error, "checked": self.checked,
                "findings": [f.as_dict() for f in self.findings],
                "crit": sum(1 for f in self.findings if f.severity == "crit"),
                "warn": sum(1 for f in self.findings if f.severity == "warn")}


_lock = threading.Lock()
_results: dict[str, CheckResult] = {}
_running: set[str] = set()


# --------------------------------------------------------------- общие помощники

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unwrap(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("configProfiles", "hosts", "nodes", "response"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


# Ключу панели права выдаются по разделам: на ноды и хосты может быть доступ,
# а на конфиг-профили — нет. Тогда часть проверок работать всё равно должна.
NO_PROFILES_HINT = (
    "Ключ панели в .env рагента не имеет доступа к конфиг-профилям (панель отвечает 403). "
    "Выдайте этому ключу право на Config Profiles в Remnawave — или пропишите ключ, "
    "у которого оно уже есть. Проверки по хостам и CDN работают и без него."
)


def _panel(profiles_required: bool = True) -> tuple[list, list, list]:
    """Хосты, профили и ноды одним заходом — чтобы проверки не дёргали панель по разу.

    Профили лежат за отдельным правом. Проверке, которой они не нужны, отказ в них
    мешать не должен, поэтому запрашивать их обязательно она не просит.
    """
    hosts = _unwrap(rw_api("/api/hosts"))
    try:
        profiles = _unwrap(rw_api("/api/config-profiles"))
    except RemnawaveApiError as exc:
        if "403" not in str(exc.message):
            raise
        if profiles_required:
            raise RemnawaveApiError(NO_PROFILES_HINT) from exc
        profiles = []
    nodes = _unwrap(rw_api("/api/nodes"))
    return hosts, profiles, nodes


def _inbound_index(profiles: list) -> dict[str, dict]:
    """uuid инбаунда -> сведения о нём и его профиле."""
    out: dict[str, dict] = {}
    for prof in profiles:
        raw = {ib.get("tag"): ib for ib in ((prof.get("config") or {}).get("inbounds") or [])}
        for meta in prof.get("inbounds") or []:
            ib = raw.get(meta.get("tag"))
            if ib is None:
                continue
            out[meta["uuid"]] = {
                "tag": meta.get("tag"), "profile": prof.get("name"),
                "profile_uuid": prof.get("uuid"), "inbound": ib,
                "nodes": [n.get("name") for n in (prof.get("nodes") or [])],
            }
    return out


def _host_inbound_uuid(host: dict) -> str | None:
    return (host.get("inbound") or {}).get("configProfileInboundUuid")


def _in_parallel(items: list, fn: Callable) -> list:
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(fn, items))


def _resolve(name: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos})


@dataclass
class CertInfo:
    days_left: int
    names: list[str]


def _cert_info(host: str, port: int, server_name: str) -> CertInfo | None:
    """Сертификат, который реально отдаёт узел. None — соединение не встало.

    Проверку доверия снимаем намеренно: нас интересует срок, а не валидность,
    и самоподписанный сертификат тоже надо увидеть, а не молча пропустить.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=NET_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=server_name) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError, ValueError):
        return None
    if not der:
        return None
    try:
        cert = x509.load_der_x509_certificate(der)
        expires = cert.not_valid_after_utc
        try:
            alt = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            names = list(alt.value.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            names = []
        if not names:
            names = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                     if isinstance(a.value, str)]
    except Exception:  # noqa: BLE001
        return None
    days = int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    return CertInfo(days_left=days, names=names)


# --------------------------------------------------------------- сами проверки

def check_cdn_alive() -> CheckResult:
    """Живы ли CDN-фронты, за которыми стоят хосты."""
    res = CheckResult()
    hosts, _profiles, _ = _panel(profiles_required=False)   # профили этой проверке не нужны

    targets: list[tuple[str, str]] = []   # (домен, к какому хосту относится)
    seen: set[tuple[str, str]] = set()
    for h in hosts:
        if h.get("isDisabled"):
            continue
        remark = h.get("remark") or "?"
        for value in (h.get("address"), h.get("sni"), h.get("host")):
            v = (value or "").strip().lower()
            if v and any(v.endswith(sfx) for sfx in CDN_SUFFIXES) and (v, remark) not in seen:
                seen.add((v, remark))
                targets.append((v, remark))
    res.checked = len(targets)

    def probe(item: tuple[str, str]) -> Finding | None:
        domain, remark = item
        ips = _resolve(domain)
        if not ips:
            return Finding("crit", remark,
                           f"CDN-домен {domain} не резолвится",
                           "Так выглядит снятый ресурс или заблокированный аккаунт: "
                           "техдомен исчезает из DNS, а хост продолжает отдавать клиентам ошибку. "
                           "Проверить статус аккаунта у провайдера и переехать на свободный.")
        return None

    for f in _in_parallel(targets, probe):
        if f:
            res.findings.append(f)

    # Отдельно: адрес хоста — запинённый IP эджа, а сам техдомен уже уехал на другие узлы.
    for h in hosts:
        if h.get("isDisabled"):
            continue
        addr = (h.get("address") or "").strip()
        sni = (h.get("sni") or "").strip().lower()
        if not sni or not any(sni.endswith(s) for s in CDN_SUFFIXES):
            continue
        if not addr or addr in PLACEHOLDER_ADDRS or not addr.replace(".", "").isdigit():
            continue
        live = _resolve(sni)
        if live and addr not in live:
            res.findings.append(Finding(
                "warn", h.get("remark") or "?",
                f"Запинённый эдж {addr} больше не в пуле {sni}",
                "Узлы CDN ротируются. Адрес остался на старом эдже, а домен уже отдаёт "
                f"другие: {', '.join(live[:4])}. Стоит перемерить и сменить IP."))
    res.ran_at = _now()
    return res


def check_certs(extra_domains: list[str] | None = None) -> CheckResult:
    """Сроки сертификатов там, где сертификат наш и его надо продлевать руками."""
    res = CheckResult()
    hosts, profiles, _ = _panel()
    idx = _inbound_index(profiles)

    targets: list[tuple[str, int, str, str]] = []  # (куда, порт, SNI, подпись)
    for h in hosts:
        if h.get("isDisabled"):
            continue
        info = idx.get(_host_inbound_uuid(h) or "")
        if not info:
            continue
        stream = (info["inbound"].get("streamSettings") or {})
        # REALITY одалживает сертификат чужого сайта — его срок не наша забота.
        if stream.get("security") == "reality":
            continue
        # Hysteria и прочий QUIC живут поверх UDP: TCP-рукопожатием их сертификат
        # не прочитать, и попытка дала бы ложную тревогу.
        if stream.get("network") in ("hysteria", "quic", "kcp"):
            continue
        # Остаются те, где клиент видит настоящий TLS: фронты CDN и наши домены.
        # У инбаунда при этом обычно `security: none` — шифрование даёт сам фронт,
        # поэтому отбирать по инбаунду нельзя, только исключать REALITY и UDP.
        addr = (h.get("address") or "").strip()
        sni = (h.get("sni") or h.get("host") or addr).strip()
        if not addr or not sni or addr in PLACEHOLDER_ADDRS:
            continue
        targets.append((addr, int(h.get("port") or 443), sni, h.get("remark") or addr))

    for d in extra_domains or []:
        d = d.strip()
        if d:
            targets.append((d, 443, d, d))
    res.checked = len(targets)

    def probe(item):
        addr, port, sni, label = item
        cert = _cert_info(addr, port, sni)
        if cert is None:
            # Недоступность зависит от того, откуда смотрим: у клиента может быть
            # VPN, у площадки — фильтр. Это повод посмотреть, а не бить тревогу;
            # умерший CDN однозначно ловится проверкой резолва, а не этой.
            return Finding("info", label, f"Сертификат не удалось прочитать с {addr}:{port}",
                           f"SNI {sni}. Узел не ответил по TCP — проверьте, доступен ли он "
                           "с той машины, где крутится рагент.")
        days = cert.days_left
        detail = f"{sni} → {', '.join(cert.names[:3]) or '?'}, осталось {days} дн."
        if days <= CERT_CRIT_DAYS:
            return Finding("crit", label, f"Сертификат истекает через {days} дн.", detail)
        if days <= CERT_WARN_DAYS:
            return Finding("warn", label, f"Сертификат истекает через {days} дн.", detail)
        return None

    for f in _in_parallel(targets, probe):
        if f:
            res.findings.append(f)
    res.ran_at = _now()
    return res


def check_config_drift() -> CheckResult:
    """Расхождения внутри панели, которые не видно глазом, но которые ломают клиентов."""
    res = CheckResult()
    hosts, profiles, _nodes = _panel()
    idx = _inbound_index(profiles)
    res.checked = len(hosts) + sum(len(p.get("inbounds") or []) for p in profiles)

    used_inbounds: set[str] = set()
    for h in hosts:
        u = _host_inbound_uuid(h)
        if u:
            used_inbounds.add(u)
        # Выключенный хост клиентам не выдаётся: его расхождения — не поломка,
        # и поднимать из-за них тревогу значит топить настоящие находки.
        if h.get("isDisabled"):
            continue
        info = idx.get(u or "")
        remark = h.get("remark") or "?"
        if info is None:
            res.findings.append(Finding(
                "crit", remark,
                "У хоста не задан инбаунд" if not u else "Хост ссылается на несуществующий инбаунд",
                "Хост включён и выдаётся клиентам, но подключиться по нему нельзя."))
            continue
        stream = info["inbound"].get("streamSettings") or {}
        reality = stream.get("realitySettings") or {}
        server_names = reality.get("serverNames") or []
        first = server_names[0] if server_names else None

        # Хвост от смены dest: Host-заголовок xhttp остался от прежнего домена.
        # Само по себе это НЕ поломка. Сервер xhttp сверяет Host с настройкой
        # инбаунда (splithttp/hub.go: IsValidHTTPHost), а панель отдаёт клиенту
        # тот же самый Host из записи хоста — обе стороны согласованы, туннель
        # работает. Внутрь REALITY-шифрования этот заголовок никто снаружи не
        # видит, так что и отпечатка не создаёт. Чинить такое на живом парке
        # опасно: править надо инбаунд и хост разом, а клиент со старой
        # подпиской после этого получит отказ, пока не обновится.
        xh = (stream.get("xhttpSettings") or {}).get("host")
        host_host = (h.get("host") or "").strip()
        if xh and first and xh != first:
            agreed = not host_host or host_host == xh
            res.findings.append(Finding(
                "info" if agreed else "crit", remark,
                "Host у xhttp остался от прежнего dest" if agreed
                else "Host у хоста и у инбаунда разошлись",
                f"xhttpSettings.host = {xh}, serverNames[0] = {first}"
                + (f", у хоста host = {host_host}" if host_host else "")
                + (". Клиент и сервер согласованы, туннель работает — это след "
                   "прежней смены dest. Менять только вместе с записью хоста и "
                   "с оглядкой на тех, кто не обновил подписку."
                   if agreed else
                   ". Сервер сверяет Host и отвергнет запрос — хост не работает."),
            ))

        # Явный sni у хоста перекрывает профиль — после смены dest клиенты уедут на старое имя.
        h_sni = (h.get("sni") or "").strip()
        if h_sni and first and h_sni != first:
            res.findings.append(Finding(
                "crit", remark, "SNI хоста перекрывает профиль",
                f"У хоста sni = {h_sni}, в инбаунде serverNames[0] = {first}. "
                "Панель берёт значение хоста, и смена dest до клиентов не доедет."))

        if not (h.get("nodes") or []):
            res.findings.append(Finding(
                "info", remark, "Хост не привязан ни к одной ноде",
                "Клиентам обычно работает — панель не заполнила вычисляемую связь. "
                "Ломает инструменты, которые ищут хост по имени ноды."))

    # Сироты собираем в одну запись: их бывают десятки, и отдельными строками
    # они топят настоящие находки.
    orphans = [
        f"{prof.get('name')}/{meta.get('tag')}"
        for prof in profiles
        for meta in (prof.get("inbounds") or [])
        if meta["uuid"] not in used_inbounds
    ]
    if orphans:
        res.findings.append(Finding(
            "info", f"{len(orphans)} шт.", "Инбаунды, на которые не смотрит ни один хост",
            "Через них трафик не идёт — обычно мусор от прежних схем: "
            + ", ".join(sorted(orphans))))

    # Протухшее поле dest рядом с target и повторяющиеся dest у разных нод.
    by_dest: dict[str, list[str]] = {}
    for prof in profiles:
        for ib in (prof.get("config") or {}).get("inbounds") or []:
            reality = ((ib.get("streamSettings") or {}).get("realitySettings") or {})
            if not reality:
                continue
            target = (reality.get("target") or "").split(":")[0]
            dest = (reality.get("dest") or "").split(":")[0]
            label = f"{prof.get('name')}/{ib.get('tag')}"
            if dest and target and dest != target:
                res.findings.append(Finding(
                    "info", label, "В realitySettings разные dest и target",
                    f"dest = {dest}, target = {target}. Xray берёт target, "
                    "dest игнорируется — но лежит и путает."))
            if target:
                by_dest.setdefault(target, []).append(label)
    for dest, where in by_dest.items():
        if len(where) > 1 and not dest.startswith("127."):
            res.findings.append(Finding(
                "warn", dest, f"Один dest на {len(where)} инбаундах",
                "Узлы становятся неотличимы по отпечатку и палятся вместе: "
                + ", ".join(where[:4])))
    res.ran_at = _now()
    return res


def check_transports() -> CheckResult:
    """Разрез парка по транспортам: где ещё голый RAW."""
    res = CheckResult()
    hosts, profiles, nodes = _panel()
    idx = _inbound_index(profiles)
    node_addr = {(n.get("name") or ""): (n.get("address") or "") for n in nodes}

    raw_hosts = []
    for h in hosts:
        if h.get("isDisabled"):
            continue
        info = idx.get(_host_inbound_uuid(h) or "")
        if not info:
            continue
        stream = info["inbound"].get("streamSettings") or {}
        if stream.get("network") not in ("raw", "tcp"):
            continue
        raw_hosts.append((h, info, stream))
    res.checked = len([h for h in hosts if not h.get("isDisabled")])

    for h, info, stream in raw_hosts:
        addr = (h.get("address") or "").strip()
        nodes_of = info["nodes"]
        direct = any(node_addr.get(n) == addr for n in nodes_of)
        where = "клиент идёт прямо на ноду" if direct else "клиент идёт на фронт"
        sev = "warn" if direct else "info"
        res.findings.append(Finding(
            sev, h.get("remark") or "?", f"Транспорт RAW, {where}",
            f"Профиль {info['profile']}/{info['tag']}, ноды: {', '.join(nodes_of) or '—'}. "
            "На части сетей TSPU душит RAW после хендшейка: туннель встаёт, трафик не идёт. "
            "Прямые хосты обычно переводят на xhttp packet-up."))
    res.ran_at = _now()
    return res


# --------------------------------------------------------------- реестр

CHECKS: dict[str, dict] = {
    "cdn_alive": {
        "title": "CDN-фронты живы",
        "what": "Резолвятся ли техдомены CDN и не уехал ли запинённый эдж из пула.",
        "why": "08.09.2026 аккаунт CDNvideo под «LTE • DE • VI» заблокировали, техдомен "
               "пропал из DNS, а хост ещё сутки отдавал клиентам 403 — заметили случайно.",
        "run": check_cdn_alive,
    },
    "certs": {
        "title": "Сроки сертификатов",
        "what": "Сколько осталось у сертификатов на хостах с настоящим TLS и у доменов из списка ниже.",
        "why": "Сертификат подписки на CDN продлевается вручную — автопродления у провайдера нет. "
               "Пропустить срок значит положить выдачу подписок всем сразу.",
        "run": check_certs,
    },
    "config_drift": {
        "title": "Расхождения в панели",
        "what": "Забытый Host у xhttp, SNI хоста поверх профиля, хосты без ноды, "
                "инбаунды-сироты, повторяющиеся dest.",
        "why": "После смены dest у четырёх хостов остался прежний Host-заголовок, а у DE "
               "явный SNI хоста перекрыл профиль — смена до клиентов не доехала.",
        "run": check_config_drift,
    },
    "transports": {
        "title": "Транспорты хостов",
        "what": "Какие включённые хосты остались на RAW и идут ли они напрямую или через фронт.",
        "why": "На рабочей сети RAW душится TSPU: PROXY PING зелёный, туннель встаёт, "
               "интернета нет. Прямые хосты пришлось переводить на xhttp packet-up.",
        "run": check_transports,
    },
}


def snapshot() -> dict:
    with _lock:
        return {
            "checks": [
                {"id": cid, "title": meta["title"], "what": meta["what"], "why": meta["why"],
                 "running": cid in _running,
                 "result": _results[cid].as_dict() if cid in _results else None}
                for cid, meta in CHECKS.items()
            ],
            "any_running": bool(_running),
        }


def run(check_id: str, **kwargs) -> CheckResult:
    """Выполнить одну проверку. Исключения наружу не выпускаем — они становятся результатом."""
    meta = CHECKS.get(check_id)
    if meta is None:
        raise KeyError(check_id)
    with _lock:
        if check_id in _running:
            return _results.get(check_id, CheckResult())
        _running.add(check_id)
    started = time.monotonic()
    try:
        result = meta["run"](**kwargs)
    except RemnawaveApiError as exc:
        result = CheckResult(error=str(exc.message), ran_at=_now())
    except Exception as exc:  # noqa: BLE001
        result = CheckResult(error=f"{type(exc).__name__}: {exc}", ran_at=_now())
    result.duration_ms = int((time.monotonic() - started) * 1000)
    with _lock:
        _results[check_id] = result
        _running.discard(check_id)
    return result
