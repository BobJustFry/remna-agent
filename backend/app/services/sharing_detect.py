"""Детектор шаринга по данным Xray с нод (GetStatsOnlineIpList через API панели).

Не путать с ss ESTAB / proxy_peers: xHTTP держит пачку TCP на один IP.
Считаем разные исходные адреса одного пользователя, а не число сокетов.

Вердикт — не порог, а оценка: веса и нормы живут в sharing_score, норма
считается от лимита устройств пользователя. Здесь только сбор признаков:
адреса и операторы с нод, карточки пользователей, HWID кандидатов. В панель
ходим только читать.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import asn_lookup, sharing_score
from app.services.remnawave_api import RemnawaveApiError, rw_api, rw_nodes
from app.services.sharing_score import Features, Verdict

log = logging.getLogger("remna.sharing")

# Живой туннель: lastSeen не старше этого.
WIN_5 = timedelta(minutes=5)
WIN_15 = timedelta(minutes=15)

# Старые жёсткие пороги. Вердикт их больше не выносит, но они остались как
# справочная «норма при общем лимите 3» — её показывает интерфейс в шапке и
# по ней читаются старые записи в журнале.
BAN_IPS_5M = 8
BAN_IPS_15M = 12
BAN_NETS_CONCURRENT = 5

# «Одновременно» — в пределах этого окна вокруг любого наблюдения. Роуминг по
# вышкам даёт адреса последовательно, ферма — сразу пачкой.
CONCURRENCY_WINDOW = timedelta(seconds=60)

SKIP_PREFIX = "hop-"

YANDEX_CLOUD = [
    ipaddress.ip_network("188.72.110.0/23"),
    ipaddress.ip_network("84.201.0.0/16"),
    ipaddress.ip_network("51.250.0.0/16"),
    ipaddress.ip_network("178.154.192.0/18"),
]

_lock = threading.Lock()
_snapshot: dict[str, Any] = {
    "scanned_at": None,
    "error": None,
    "scanning": False,
    "online_users": 0,
    "flagged": 0,
    "suspects": [],
    "by_agent_id": {},
    "peers_by_agent_id": {},
    "thresholds": {
        "ips_5m": BAN_IPS_5M,
        "nets_concurrent": BAN_NETS_CONCURRENT,
        "ips_15m": BAN_IPS_15M,
    },
}


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "scanned_at": _snapshot.get("scanned_at"),
            "error": _snapshot.get("error"),
            "scanning": _snapshot.get("scanning"),
            "online_users": _snapshot.get("online_users"),
            "flagged": _snapshot.get("flagged"),
            "suspects": list(_snapshot.get("suspects") or []),
            "by_agent_id": dict(_snapshot.get("by_agent_id") or {}),
            "peers_by_agent_id": dict(_snapshot.get("peers_by_agent_id") or {}),
            "thresholds": dict(_snapshot.get("thresholds") or {}),
        }


def mark_error(message: str) -> None:
    with _lock:
        _snapshot["error"] = message
        _snapshot["scanning"] = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm_host(s: str) -> str:
    return (s or "").strip().lower()


def _canon_node_name(s: str) -> str:
    n = _norm_host(s)
    if n.startswith("usa-"):
        return "us-" + n[4:]
    return n


def _is_loop_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback


def _slash16(ip: str) -> str | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return f"{parts[0]}.{parts[1]}.0.0/16"


def in_yandex_cloud(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in YANDEX_CLOUD)


def _tb(n: Any) -> str:
    try:
        b = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if b >= 10**12:
        return f"{b / 1e12:.2f} ТБ"
    if b >= 10**9:
        return f"{b / 1e9:.2f} ГБ"
    if b >= 10**6:
        return f"{b / 1e6:.1f} МБ"
    return f"{b} Б"


def _remain(expire: Any) -> str:
    dt = _parse_ts(expire)
    if not dt:
        return "—"
    days = (dt - _now()).total_seconds() / 86400
    if days < 0:
        return f"истекла {abs(days):.1f} дн. назад ({dt.date()})"
    return f"{days:.1f} дн. (до {dt.date()})"


def _try_api(path: str, method: str = "GET", body: dict | None = None) -> Any:
    try:
        return rw_api(path, method, body)
    except RemnawaveApiError as exc:
        return {"_error": str(exc)}


def _job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("_error"):
        return None
    jid = payload.get("jobId") or payload.get("id")
    return str(jid) if jid else None


def _poll_jobs(jobs: list[tuple[str, str, str]]) -> dict[str, dict]:
    pending = {jid: (uuid, name) for uuid, name, jid in jobs}
    results: dict[str, dict] = {}
    t0 = time.time()
    while pending and time.time() - t0 < 240:
        time.sleep(1.2)
        done = []
        for jid, (uuid, name) in list(pending.items()):
            r = _try_api(f"/api/connections/by-node/{jid}")
            if not isinstance(r, dict) or r.get("_error"):
                r = _try_api(f"/api/connections/by-node/result/{jid}")
            if not isinstance(r, dict):
                continue
            if r.get("isFailed"):
                results[uuid] = {"_nodeName": name, "users": []}
                done.append(jid)
                continue
            if r.get("isCompleted"):
                result = r.get("result") or {}
                if not isinstance(result, dict):
                    result = {}
                result["_nodeName"] = name
                result["_nodeUuid"] = uuid
                results[uuid] = result
                done.append(jid)
        for jid in done:
            pending.pop(jid, None)
    for jid, (uuid, name) in pending.items():
        results[uuid] = {"_nodeName": name, "users": []}
    return results


def _unwrap_user(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get("_error"):
        return {}
    if payload.get("id") is not None:
        return payload
    inner = payload.get("user") or payload.get("response")
    return inner if isinstance(inner, dict) else {}


# ── карточки пользователей: лимит устройств и трафик ─────────────────────────
#
# Нормы считаются от лимита устройств, значит лимит нужен для КАЖДОГО онлайна,
# а не только для попавшихся. Спрашивать карточку на каждого в каждом скане —
# это 300 запросов раз в две минуты, поэтому весь список читается целиком
# страницами и держится в памяти: 4700 пользователей забираются за восемь
# секунд, этого хватает на полчаса.

_users: dict[str, Any] = {"at": 0.0, "by_id": {}, "median": None,
                          "fallback_limit": None, "error": None}


def _fetch_fallback_limit() -> int | None:
    ss = _try_api("/api/subscription-settings")
    if not isinstance(ss, dict) or ss.get("_error"):
        return None
    hw = ss.get("hwidSettings")
    if not isinstance(hw, dict):
        return None
    try:
        return int(hw.get("fallbackDeviceLimit"))
    except (TypeError, ValueError):
        return None


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) // 2


def refresh_users(cfg: dict, force: bool = False) -> dict:
    """Перечитать карточки всех пользователей, если кэш протух. Только чтение."""
    ttl = max(60.0, float(cfg.get("users_refresh_min") or 30) * 60)
    if not force and _users["by_id"] and time.time() - _users["at"] < ttl:
        return _users
    by_id: dict[int, dict] = {}
    start, total, error = 0, 1, None
    while start < total:
        page = _try_api(f"/api/users?size=500&start={start}")
        if not isinstance(page, dict) or page.get("_error"):
            error = str(page.get("_error") if isinstance(page, dict) else page)
            break
        rows = page.get("users") or []
        try:
            total = int(page.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        for u in rows:
            if not isinstance(u, dict):
                continue
            try:
                uid = int(u.get("id"))
            except (TypeError, ValueError):
                continue
            ut = u.get("userTraffic") or {}
            try:
                used = int(ut.get("usedTrafficBytes") or 0)
            except (TypeError, ValueError):
                used = 0
            by_id[uid] = {
                "username": str(u.get("username") or ""),
                "limit": u.get("hwidDeviceLimit"),
                "used": used,
                "status": str(u.get("status") or ""),
                "tag": u.get("tag"),
            }
        if not rows:
            break
        start += len(rows)
    if by_id:
        used = [v["used"] for v in by_id.values() if v["status"] == "ACTIVE" and v["used"] > 0]
        _users.update(at=time.time(), by_id=by_id, median=_median(used), error=error,
                      fallback_limit=_fetch_fallback_limit())
        log.info("sharing: карточек пользователей %s, медиана трафика %s",
                 len(by_id), _tb(_users["median"]))
    else:
        _users["error"] = error or "панель не отдала список пользователей"
    return _users


def users_cache() -> dict:
    return _users


# ── сбор признаков ───────────────────────────────────────────────────────────

@dataclass
class _UserStat:
    uid: int
    rows: list[dict] = field(default_factory=list)
    ips_5m: int = 0
    ips_15m: int = 0
    s16_5m: int = 0          # операторов за 5 мин (имя оставлено для совместимости)
    conc_ips: int = 0        # максимум одновременных IP
    conc_nets: int = 0       # максимум одновременных операторов
    conc_countries: list[str] = field(default_factory=list)
    own_ips: int = 0         # отсеяно адресов собственных нод
    nodes: list[str] = field(default_factory=list)
    # Панель держит пользователя в списке онлайна и через сутки после последнего
    # пакета: из 323 «онлайн» у 80 метка старше шести часов. Для суточного
    # профиля такой человек онлайном не считается, иначе провал на сон исчезнет
    # у всех разом.
    fresh: bool = False
    reasons: list[str] = field(default_factory=list)
    username: str = ""
    verdict: Verdict | None = None
    features: Features | None = None


_flagged_stats: dict[int, _UserStat] = {}
# Докуда дошёл обход парка на HWID: продолжаем с этого места в следующем скане.
_sweep: dict[str, int] = {"at": 0}


def _rotate(items: list, take: int) -> list:
    """Следующий кусок круга. Пустой список и нулевой кусок — ничего не берём."""
    if take <= 0 or not items:
        return []
    start = _sweep["at"] % len(items)
    _sweep["at"] = (start + take) % len(items)
    doubled = items + items
    return doubled[start:start + min(take, len(items))]


def _networks(ips) -> set[str]:
    """Операторы этих адресов. Падение резолва деградирует до /16, не до нуля."""
    try:
        return asn_lookup.networks_of(ips)
    except Exception:  # noqa: BLE001
        log.warning("ASN lookup failed, fallback to /16", exc_info=True)
        return {n for n in (_slash16(ip) for ip in ips) if n}


def _countries(ips) -> set[str]:
    try:
        return asn_lookup.countries_of(ips)
    except Exception:  # noqa: BLE001
        return set()


def _concurrency(rows: list[dict]) -> tuple[int, int, list[str]]:
    """Максимум адресов и операторов, живших в одном окне CONCURRENCY_WINDOW,
    и страны регистрации сетей самой «широкой» такой группы.

    Человек, который переезжает с вышки на вышку, набирает адреса
    последовательно — в любом окне их один-два. Ферма даёт пачку сразу.
    """
    stamped = []
    for r in rows:
        ts = _parse_ts(r["lastSeen"])
        if ts:
            stamped.append((r["ip"], ts))
    if not stamped:
        return 0, 0, []
    window = CONCURRENCY_WINDOW.total_seconds()
    best_ips = best_nets = 0
    best_countries: set[str] = set()
    for _, anchor in stamped:
        group = {ip for ip, ts in stamped if abs((ts - anchor).total_seconds()) <= window}
        if len(group) > best_ips:
            best_ips = len(group)
        nets = len(_networks(group))
        if nets > best_nets:
            best_nets = nets
            best_countries = _countries(group)
    return best_ips, best_nets, sorted(best_countries)


def _platform(dev: dict) -> str:
    """Платформа устройства, как её назвало приложение. Пусто — не представилось."""
    name = str(dev.get("platform") or "").strip()
    if name:
        return name
    model = str(dev.get("deviceModel") or "").strip()
    return model or ""


def hwid_features(user_id: int, cfg: dict, now: datetime | None = None) -> tuple[list[str], int, int]:
    """(платформы, активных HWID, всего HWID) по /api/hwid/devices/{id}.

    Активные — те, чей updatedAt попал в окно hwid_active_min. Переустановка
    даёт новую запись, но старая перестаёт обновляться, поэтому в окно они
    вдвоём не попадут; два живых устройства — попадут.
    """
    now = now or _now()
    payload = _try_api(f"/api/hwid/devices/{user_id}")
    if not isinstance(payload, dict) or payload.get("_error"):
        return [], 0, 0
    devices = payload.get("devices") or []
    window = timedelta(minutes=max(5, int(cfg.get("hwid_active_min") or 60)))
    platforms: set[str] = set()
    active = 0
    for d in devices:
        if not isinstance(d, dict):
            continue
        ts = _parse_ts(d.get("updatedAt"))
        if not ts or now - ts > window:
            continue
        active += 1
        name = _platform(d)
        if name:
            platforms.add(name)
    return sorted(platforms), active, len(devices)


def _collect_stats(
    node_results: dict[str, dict], own_ips: set[str] | None = None
) -> dict[int, _UserStat]:
    by_user: dict[int, _UserStat] = {}
    now = _now()
    for uuid, payload in node_results.items():
        nname = payload.get("_nodeName") or uuid[:8]
        addr = payload.get("_address") or ""
        for row in payload.get("users") or []:
            if not isinstance(row, dict):
                continue
            try:
                uid = int(row.get("userId"))
            except (TypeError, ValueError):
                continue
            st = by_user.setdefault(uid, _UserStat(uid=uid))
            for item in row.get("ips") or []:
                ip = item.get("ip") if isinstance(item, dict) else item
                seen = item.get("lastSeen") if isinstance(item, dict) else None
                if not ip:
                    continue
                ip = str(ip).replace("::ffff:", "")
                st.rows.append({
                    "ip": ip,
                    "lastSeen": seen,
                    "node": nname,
                    "node_uuid": uuid,
                    "address": addr,
                })
    for st in by_user.values():
        # Адреса собственных нод — это хопы и фетчи через туннель, а не сети клиента.
        kept = []
        for r in st.rows:
            if own_ips and r["ip"] in own_ips:
                st.own_ips += 1
                continue
            kept.append(r)
        st.rows = kept

        def fresh(delta: timedelta) -> list[dict]:
            out = []
            for r in st.rows:
                ts = _parse_ts(r["lastSeen"])
                if ts and now - ts <= delta:
                    out.append(r)
            return out

        r5, r15 = fresh(WIN_5), fresh(WIN_15)
        i5 = {r["ip"] for r in r5}
        i15 = {r["ip"] for r in r15}
        st.ips_5m = len(i5)
        st.ips_15m = len(i15)
        st.s16_5m = len(_networks(i5))
        st.conc_ips, st.conc_nets, st.conc_countries = _concurrency(st.rows)
        st.fresh = bool(r15)
        st.nodes = sorted({r["node"] for r in st.rows})
    return by_user


def _features(st: _UserStat, cfg: dict, card: dict, median: int | None,
              profile: tuple[float, int] | None) -> Features:
    limit = card.get("limit") if card else None
    ratio = None
    if median and median > 0 and card and card.get("used"):
        ratio = round(card["used"] / median, 2)
    night_pct, night_days = profile if profile else (None, 0)
    return Features(
        user_id=st.uid,
        username=st.username or (card.get("username") if card else "") or "",
        raw_limit=limit,
        ips_5m=st.ips_5m,
        ips_15m=st.ips_15m,
        conc_ips=st.conc_ips,
        conc_nets=st.conc_nets,
        conc_countries=st.conc_countries,
        night_busy_pct=night_pct,
        night_days=night_days,
        traffic_ratio=ratio,
    )


def _hit(st: _UserStat, on_node: int = 0) -> dict:
    """Строка для интерфейса. Старые поля на месте — вид нод их уже читает."""
    v = st.verdict
    f = st.features
    return {
        "user_id": st.uid,
        "username": st.username,
        "ips_5m": st.ips_5m,
        "ips_15m": st.ips_15m,
        "s16_5m": st.s16_5m,
        "conc_ips": st.conc_ips,
        "conc_nets": st.conc_nets,
        "own_ips": st.own_ips,
        "ips_on_node": on_node,
        "reasons": st.reasons,
        "rw_nodes": st.nodes,
        "score": v.score if v else 0,
        "level": v.level if v else sharing_score.GREEN,
        "device_limit": v.device_limit if v else 0,
        "unlimited": bool(v.unlimited) if v else False,
        "parts": [{"key": p.key, "points": p.points, "why": p.why} for p in (v.parts if v else [])],
        "countries": list(st.conc_countries),
        "platforms": list(f.platforms or []) if f else [],
        "hwid_active": (f.hwid_active if f else None),
        "hwid_total": (f.hwid_total if f else None),
        "night_busy_pct": (f.night_busy_pct if f else None),
        "traffic_ratio": (f.traffic_ratio if f else None),
    }


def scan_once(
    agent_nodes: list[tuple[str, str, str]],
    cfg: dict | None = None,
    profiles: dict[int, tuple[float, int]] | None = None,
    disabled_hosts: set[str] | None = None,
    all_own_hosts: set[str] | None = None,
    is_current=None,
) -> dict[str, Any]:
    """agent_nodes: (agent_id, name, host). Sync — вызывать из to_thread.

    cfg — настройки оценки (sharing_score.DEFAULTS, перекрытые из app_settings),
    profiles — {user_id: (занятость самого тихого окна суток, суток наблюдений)}.
    """
    cfg = {**sharing_score.DEFAULTS, **(cfg or {})}
    profiles = profiles or {}
    disabled_hosts = disabled_hosts or set()
    try:
        rw = rw_nodes()
    except RemnawaveApiError as exc:
        err = str(exc)
        mark_error(err)
        return snapshot()

    cache = refresh_users(cfg)
    if cache.get("fallback_limit"):
        # Общий лимит берём из панели, а не из настроек: он там и живёт.
        cfg = {**cfg, "fallback_device_limit": cache["fallback_limit"]}
    cards: dict[int, dict] = cache.get("by_id") or {}
    median: int | None = cache.get("median")

    jobs: list[tuple[str, str, str]] = []
    addr_by_uuid: dict[str, str] = {}
    for n in rw:
        if is_current is not None and not is_current():
            return {"discarded": True}
        if not isinstance(n, dict) or n.get("isDisabled"):
            continue
        uuid = n.get("uuid")
        if not uuid:
            continue
        name = n.get("name") or uuid
        addr_by_uuid[str(uuid)] = str(n.get("address") or "")
        if str(n.get("address") or "") in disabled_hosts:
            continue
        job = _try_api(f"/api/connections/by-node/{uuid}", "POST", {})
        jid = _job_id(job)
        if not jid:
            log.warning("sharing job skip %s: %s", name, job)
            continue
        jobs.append((str(uuid), str(name), jid))

    node_results = _poll_jobs(jobs)
    for uuid, payload in node_results.items():
        payload["_address"] = addr_by_uuid.get(uuid, "")

    # Our own nodes appear as client IPs on hops and on subscription fetches made
    # through the tunnel. They are not the subscriber's networks.
    own_ips = {a for a in addr_by_uuid.values() if a}
    own_ips.update(h for _, _, h in agent_nodes if h)
    own_ips.update(all_own_hosts or set())
    stats = _collect_stats(node_results, own_ips)

    # Служебные пользователи-хопы шарингом не считаются: это наши же туннели.
    for uid in list(stats):
        name = str((cards.get(uid) or {}).get("username") or "")
        if name.lower().startswith(SKIP_PREFIX):
            stats.pop(uid, None)

    # Первый проход — по тому, что видно без лишних запросов в панель.
    for st in stats.values():
        card = cards.get(st.uid) or {}
        st.username = str(card.get("username") or "")
        st.features = _features(st, cfg, card, median, profiles.get(st.uid))
        st.verdict = sharing_score.score(cfg, st.features)

    # Второй проход — HWID. Платформы и живые устройства стоят запроса на
    # человека, поэтому за скан спрашиваем не больше max_candidates.
    #
    # Сначала те, кто уже набрал половину жёлтой зоны. Но одними ими
    # ограничиться нельзя: в чистом парке по адресам никто столько не набирает,
    # и признак «три платформы при лимите 1» не сработал бы никогда. Поэтому
    # остаток места отдаётся всем остальным по кругу: при 330 онлайн и 60 местах
    # каждый проверяется примерно раз в десять минут.
    limit_n = max(0, int(cfg.get("max_candidates") or 60))
    floor = float(cfg.get("amber") or 35) / 2
    hot = sorted(
        (st for st in stats.values() if st.verdict and st.verdict.score >= floor),
        key=lambda s: -(s.verdict.score if s.verdict else 0),
    )[:limit_n]
    hot_ids = {st.uid for st in hot}
    rest = sorted((st for st in stats.values() if st.fresh and st.uid not in hot_ids),
                  key=lambda s: s.uid)
    candidates = hot + _rotate(rest, max(0, limit_n - len(hot)))
    now_hwid = _now()
    for st in candidates:
        platforms, active, total = hwid_features(st.uid, cfg, now_hwid)
        if not total:
            continue
        st.features.platforms = platforms
        st.features.hwid_active = active
        st.features.hwid_total = total
        st.verdict = sharing_score.score(cfg, st.features)

    for st in stats.values():
        st.reasons = st.verdict.reasons if st.verdict else []

    red = [st for st in stats.values()
           if st.verdict and st.verdict.level == sharing_score.RED]
    suspects = sorted(
        (st for st in stats.values()
         if st.verdict and st.verdict.level in (sharing_score.RED, sharing_score.AMBER)),
        key=lambda s: -(s.verdict.score if s.verdict else 0),
    )

    with _lock:
        _flagged_stats.clear()
        _flagged_stats.update({st.uid: st for st in suspects})

    host_to_agent: dict[str, str] = {}
    name_to_agent: dict[str, str] = {}
    for aid, aname, host in agent_nodes:
        host_to_agent[_norm_host(host)] = aid
        name_to_agent[_canon_node_name(aname)] = aid

    def _agent_id(addr: str, nname: str) -> str | None:
        h = _norm_host(addr)
        if h in host_to_agent:
            return host_to_agent[h]
        return name_to_agent.get(_canon_node_name(nname))

    peer_ips: dict[str, set[str]] = defaultdict(set)
    peer_users: dict[str, int] = defaultdict(int)
    now_peers = _now()
    for uuid, payload in node_results.items():
        aid = _agent_id(str(payload.get("_address") or ""), str(payload.get("_nodeName") or ""))
        if not aid:
            continue
        peer_ips.setdefault(aid, set())
        n_users = 0
        for row in payload.get("users") or []:
            if not isinstance(row, dict):
                continue
            n_users += 1
            for item in row.get("ips") or []:
                ip = item.get("ip") if isinstance(item, dict) else item
                if not ip:
                    continue
                ip = str(ip).replace("::ffff:", "")
                if _is_loop_ip(ip):
                    continue
                ts = _parse_ts(item.get("lastSeen") if isinstance(item, dict) else None)
                if ts and now_peers - ts > WIN_15:
                    continue
                peer_ips[aid].add(ip)
        peer_users[aid] += n_users
    peers_by_agent = {
        aid: {"ips": len(ips), "users": peer_users[aid]}
        for aid, ips in peer_ips.items()
    }

    # На ноде показываем только красных: жёлтый — повод посмотреть на странице
    # шаринга, а не значок тревоги у каждой второй ноды.
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for st in red:
        seen_agents: set[str] = set()
        for r in st.rows:
            ts = _parse_ts(r["lastSeen"])
            if not ts or _now() - ts > WIN_15:
                continue
            aid = _agent_id(str(r.get("address") or ""), str(r.get("node") or ""))
            if not aid or aid in seen_agents:
                continue
            seen_agents.add(aid)
            on_node = {
                x["ip"]
                for x in st.rows
                if _norm_host(str(x.get("address") or "")) == _norm_host(str(r.get("address") or ""))
            }
            by_agent[aid].append(_hit(st, len(on_node)))

    out = {
        "scanned_at": _now().isoformat(),
        "error": None,
        "scanning": False,
        "online_users": len(stats),
        "flagged": len(red),
        "suspects": [_hit(st) for st in suspects],
        "by_agent_id": dict(by_agent),
        "peers_by_agent_id": peers_by_agent,
        "thresholds": {
            "ips_5m": BAN_IPS_5M,
            "nets_concurrent": BAN_NETS_CONCURRENT,
            "ips_15m": BAN_IPS_15M,
            "red": int(cfg.get("red") or 0),
            "amber": int(cfg.get("amber") or 0),
            "fallback_device_limit": int(cfg.get("fallback_device_limit") or 0),
        },
    }
    with _lock:
        _snapshot.update(out)
    unmatched = [
        str(p.get("_nodeName") or u[:8])
        for u, p in node_results.items()
        if not _agent_id(str(p.get("_address") or ""), str(p.get("_nodeName") or ""))
    ]
    log.info(
        "sharing scan: online=%s red=%s amber=%s share_nodes=%s peer_nodes=%s unmatched=%s",
        len(stats),
        len(red),
        len(suspects) - len(red),
        len(by_agent),
        len(peers_by_agent),
        unmatched[:12],
    )
    # online_ids наружу, но не в снимок: по ним копится суточный профиль.
    return {**out, "online_ids": sorted(st.uid for st in stats.values() if st.fresh),
            "levels": {st.uid: (st.verdict.level if st.verdict else sharing_score.GREEN,
                                st.verdict.score if st.verdict else 0,
                                st.username)
                       for st in suspects}}


def verdicts() -> dict[int, _UserStat]:
    with _lock:
        return dict(_flagged_stats)


# ── досье ────────────────────────────────────────────────────────────────────

def _fmt_user_header(u: dict) -> list[str]:
    ut = u.get("userTraffic") or {}
    squads = u.get("activeInternalSquads") or []
    squad_names = ", ".join(
        s.get("name") or "" for s in squads if isinstance(s, dict)
    )
    return [
        f"ID: {u.get('id')}",
        f"username: {u.get('username')}",
        f"status: {u.get('status')}",
        f"telegramId: {u.get('telegramId')}",
        f"email: {u.get('email')}",
        f"tag: {u.get('tag')}",
        f"description: {u.get('description')}",
        f"createdAt: {u.get('createdAt')}",
        f"expireAt: {u.get('expireAt')}",
        f"подписка: {_remain(u.get('expireAt'))}",
        f"hwidDeviceLimit: {u.get('hwidDeviceLimit')}",
        f"trafficLimit: {_tb(u.get('trafficLimitBytes'))} ({u.get('trafficLimitStrategy')})",
        f"usedTraffic: {_tb(ut.get('usedTrafficBytes'))}",
        f"lifetimeTraffic: {_tb(ut.get('lifetimeUsedTrafficBytes'))}",
        f"firstConnectedAt: {ut.get('firstConnectedAt')}",
        f"onlineAt: {ut.get('onlineAt')}",
        f"squads: {squad_names or '—'}",
        f"updatedAt: {u.get('updatedAt')}",
    ]


def build_dossier(user_id: int) -> str:
    """Текст досье как в remnawave/SHARING/{id}.txt."""
    card = _unwrap_user(_try_api(f"/api/users/{user_id}"))
    if not card:
        return f"# SHARING dossier  user {user_id}\nне найден в Remnawave\n"

    hwid = _try_api(f"/api/hwid/devices/{user_id}")
    hist = _try_api(f"/api/users/{user_id}/subscription-request-history?size=1000&start=0")
    devices = hwid.get("devices") or [] if isinstance(hwid, dict) else []
    recs = hist.get("records") or [] if isinstance(hist, dict) else []
    sub_ips = sorted({r.get("requestIp") for r in recs if isinstance(r, dict) and r.get("requestIp")})
    yc = sum(1 for ip in sub_ips if in_yandex_cloud(str(ip)))

    snap = snapshot()
    hits = [u for u in (snap.get("suspects") or []) if u.get("user_id") == user_id]
    if not hits:
        for users in (snap.get("by_agent_id") or {}).values():
            hits += [u for u in users if u.get("user_id") == user_id]
    hit = hits[0] if hits else {}
    reasons = hit.get("reasons") or []
    ips_5m = hit.get("ips_5m", 0)
    ips_15m = hit.get("ips_15m", 0)
    s16_5m = hit.get("s16_5m", 0)
    conc_ips = hit.get("conc_ips", 0)
    conc_nets = hit.get("conc_nets", 0)
    own_ips = hit.get("own_ips", 0)
    rw_nodes_ = hit.get("rw_nodes") or []
    with _lock:
        st = _flagged_stats.get(user_id)

    score_value = hit.get("score", 0)
    level = hit.get("level", sharing_score.GREEN)
    verdict_line = {
        sharing_score.RED: f"ШАРИНГ: да, оценка {score_value} из 100 — красная зона",
        sharing_score.AMBER: f"ШАРИНГ: под вопросом, оценка {score_value} из 100 — жёлтая зона",
    }.get(level, "ШАРИНГ: нет в последнем скане (карточка всё равно собрана)")

    lines = [
        f"# SHARING dossier  user {user_id}",
        f"# snapshot {_now().isoformat()}",
        "",
        "## Пользователь",
        *_fmt_user_header(card),
        "",
        "## Решение",
        verdict_line,
    ]
    if hit.get("device_limit"):
        lines.append(
            f"норма считалась от лимита устройств {hit['device_limit']}"
            + (" (у пользователя безлимит)" if hit.get("unlimited") else "")
        )
    for p in hit.get("parts") or []:
        lines.append(f"- +{p.get('points')}  {p.get('why')}")
    if not hit.get("parts"):
        for w in reasons:
            lines.append(f"- {w}")
    lines += [
        "",
        "## Доказательства шаринга",
        f"ноды: {', '.join(rw_nodes_) if rw_nodes_ else '—'}",
        f"уникальных клиентских IP  5 мин: {ips_5m}",
        f"уникальных клиентских IP 15 мин: {ips_15m}",
        f"операторов за 5 мин: {s16_5m}  (один человек обычно 1–3)",
        f"ОДНОВРЕМЕННО адресов: {conc_ips}, операторов: {conc_nets}  "
        f"(окно {int(CONCURRENCY_WINDOW.total_seconds())} с — вот это и отличает "
        f"шаринг от смены адреса)",
        f"страны регистрации сетей в окне: {', '.join(hit.get('countries') or []) or '—'}"
        "  (регистрация сети, не геолокация — признак слабый)",
        f"отсеяно адресов собственных нод: {own_ips}",
    ]
    if hit.get("platforms"):
        lines.append(f"платформы одновременно: {', '.join(hit['platforms'])}")
    if hit.get("hwid_active") is not None:
        lines.append(f"HWID активных сейчас: {hit['hwid_active']} из {hit.get('hwid_total')}")
    if hit.get("night_busy_pct") is not None:
        lines.append(
            f"самое тихое окно суток: онлайн {hit['night_busy_pct']:.0f}% сканов "
            "(у одного человека тут провал на сон)"
        )
    if hit.get("traffic_ratio") is not None:
        lines.append(f"трафик к медиане парка: ×{hit['traffic_ratio']}")
    lines.append(f"HWID записей: {len(devices)}  лимит: {card.get('hwidDeviceLimit')}")
    for d in devices:
        if not isinstance(d, dict):
            continue
        lines.append(
            f"  hwid={d.get('hwid')} platform={d.get('platform')} "
            f"os={d.get('osVersion')} model={d.get('deviceModel')} "
            f"ua={d.get('userAgent')} ip={d.get('requestIp')} "
            f"updated={d.get('updatedAt')}"
        )
    if len(devices) <= 1 and conc_ips >= 4:
        lines.append(
            "  HWID не бьётся с числом адресов: либо клон x-hwid, либо лимит не смотрит туннель."
        )
    lines.append(f"запросов подписки в history: {len(recs)}, уникальных IP фетча: {len(sub_ips)}")
    lines.append(f"из них Yandex.Cloud: {yc}")
    if sub_ips:
        lines.append("IP фетча подписки:")
        for ip in sub_ips:
            mark = "  [Yandex.Cloud]" if in_yandex_cloud(str(ip)) else ""
            lines.append(f"  {ip}{mark}")
    buckets: dict[str, list[str]] = defaultdict(list)
    for ip in sub_ips:
        parts = str(ip).split(".")
        if len(parts) == 4:
            buckets[".".join(parts[:3]) + ".0/24"].append(str(ip))
    fat = {k: v for k, v in buckets.items() if len(v) >= 4}
    if fat:
        lines.append("кластеры фетча (≥4 IP в одном /24) — типичная VPC/локалка, не домашний роутер:")
        for net, ips in sorted(fat.items(), key=lambda x: -len(x[1])):
            lines.append(f"  {net}: {len(ips)}  {', '.join(ips)}")
    lines.append("")
    lines.append("клиентские IP туннеля за 5 мин (источник входа на ноду, не сайты):")
    if st is not None:
        by_node: dict[str, list[str]] = defaultdict(list)
        seen_ip: set[str] = set()
        now = _now()
        for r in st.rows:
            ts = _parse_ts(r["lastSeen"])
            if not ts or now - ts > WIN_5:
                continue
            if r["ip"] in seen_ip:
                continue
            seen_ip.add(r["ip"])
            by_node[r["node"]].append(f"{r['ip']}  lastSeen={r['lastSeen']}")
        for node, items in sorted(by_node.items()):
            lines.append(f"  [{node}] {len(items)}")
            for it in items:
                lines.append(f"    {it}")
    else:
        lines.append("  (нет снимка сессий — дождитесь скана)")
    lines.append("")
    lines.append(
        "Вердикт — оценка 0..100: адреса и операторы одновременно, платформы и HWID, "
        "ночная пауза и трафик; нормы считаются от лимита устройств пользователя. "
        f"Красная зона от {snap.get('thresholds', {}).get('red', 60)}, "
        f"жёлтая от {snap.get('thresholds', {}).get('amber', 35)}."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_node_dossier(user_ids: list[int]) -> str:
    parts = [build_dossier(uid) for uid in user_ids]
    return "\n\n".join(parts) if parts else "на этой ноде шаринг в последнем скане не найден\n"
