"""Сторож доноров ИИ: жив ли донор, и (только в боевом режиме) переключение на живой.

Донор — это нода/сервер с SOCKS-входом AI-CASCADE-IN (порт 10443, логин:пароль),
через который другие ноды выпускают трафик ИИ и TikTok. В профилях панели это
socks-аутбаунды (ai-claude, ai-gemini, ai-gemini-fi2, ai-out, ai-fi…), а какие
сервисы через них идут, решают правила маршрутизации по доменам. Один и тот же
аутбаунд часто несёт несколько сервисов (ai-gemini: Gemini + ElevenLabs + TikTok),
поэтому единица переключения — «привязка» (профиль, тег аутбаунда), и новый
донор обязан быть жив для ВСЕХ её сервисов.

Цикл раз в 20 минут:
  1. Читаем панель (только чтение): ноды, профили → реестр доноров и привязок.
     Панель молчит — цикл пропускаем, решений по старым данным не принимаем.
  2. Каждому донору — ровно один лёгкий запрос на сервис, с отпечатком Chrome
     (curl_cffi), со случайными паузами, без залпов. Провал перепроверяем один
     раз через короткую паузу; только второй провал — подтверждённый.
  3. Решения (чистая функция decide): основной донор привязки подтверждённо
     упал → первый живой кандидат. Возврат на исходного — только после
     back_after_cycles живых циклов подряд и не чаще раза в back_min_gap_min.
     Дневной потолок PATCH-ей. Кандидат без подтверждения жизни — никогда.
  4. dry_run (по умолчанию): только журнал + Телеграм. Боевой режим: бэкап
     профиля → PATCH по одному профилю → сверка → ноды профиля снова в сети →
     Телеграм. Любой сбой — остановка автоматики до ручного снятия.
  5. Раз в несколько часов — фаервол доноров по SSH (только чтение): какие ноды,
     пользующиеся донором, не входят в его allowlist. Только отчёт.

Чистая логика (discover / health / decide / apply_changes / parse_firewall) не
ходит ни в сеть, ни в базу — на ней тесты. Логины доноров живут только в памяти
цикла; в состоянии — отпечатки.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import ipaddress
import logging
import random
import re
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import subwatch
from app.services.donor_probe import ALIVE_RAW, FAIL_RAW, Creds, Hop
from app.services.telegram import send_message

log = logging.getLogger("remna.donorwatch")

CONFIG_KEY = "donor_watch_config"
STATE_KEY = "donor_watch_state"
LOG_KEY = "donor_watch_log"
BACKUP_KEY = "donor_watch_backups"
LOG_KEEP = 400
BACKUPS_KEEP = 30

SERVICES = ("claude", "gemini", "elevenlabs", "tiktok", "openai")
SERVICE_NAMES = {"claude": "Claude", "gemini": "Gemini", "elevenlabs": "ElevenLabs",
                 "tiktok": "TikTok", "openai": "OpenAI"}

# Сводное состояние донора по сервису за цикл
ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"
RETIRED = "retired"

DEFAULTS: dict = {
    "enabled": True,
    "dry_run": True,                 # боевой режим включает только владелец
    "notify": True,
    "chat_id": "",                   # пусто — чат следилки за подпиской
    "interval_min": 20,              # владелец: не чаще раза в 20 минут
    "spread_s": 240,                 # пробы цикла размазаны по этому окну
    "gap_min_s": 4,                  # пауза между запросами одного донора
    "gap_max_s": 12,
    "recheck_delay_s": 90,           # перепроверка провала
    "req_timeout_s": 20,
    "transport": "ssh",              # ssh — через ноду из allowlist; direct — с рагента
    "donor_ports": [10443],
    "donor_inbound_tags": ["AI-CASCADE-IN"],
    "services": list(SERVICES),
    # Реестр доноров (кандидаты). node — имя ноды в панели (для смены IP и фаервола).
    # retired — адрес мёртв навсегда; successor — кто его заменил.
    "donors": [
        {"name": "fi-2", "address": "151.241.229.200", "port": 10443, "node": "fi-2"},
        {"name": "fi-2 (старый)", "address": "194.180.189.133", "port": 10443, "retired": True,
         "successor": "fi-2"},
        {"name": "de-cdn-yandex", "address": "95.179.166.217", "port": 10443, "node": "de-cdn-yandex"},
        {"name": "Париж", "address": "151.247.196.156", "port": 10443},
        {"name": "fi-cdn-yandex", "address": "37.27.20.242", "port": 10443, "node": "fi-cdn-yandex"},
        {"name": "fi-timeweb", "address": "66.151.34.41", "port": 10443},
        {"name": "fi-AI", "address": "95.81.79.45", "port": 10443, "node": "fi-AI"},
    ],
    # Порядок кандидатов по сервису (имена доноров). Пусто — по частоте использования.
    "priority": {},
    "probe_hops": {},                # {имя донора: [имена нод-прыжков]}
    "probe_hops_only": False,        # True — прыжки только из probe_hops, без автоподбора
    "probe_hops_exclude": [],        # ноды, через которые не щупать никогда (нода владельца и т.п.)
    "egress_overrides": {},          # {имя ноды: исходящий IP}, если выход не равен адресу
    "back_after_cycles": 3,
    "back_min_gap_min": 60,
    "quarantine_min": 60,            # упавший донор не цель столько минут
    "max_patches_24h": 24,           # на парк; профили без нод не считаются
    "max_switches_binding_24h": 3,
    "post_check_s": 180,
    "cycle_budget_s": 900,           # цикл целиком; не уложились — остаток в следующем цикле
    "allow_unverified_allowlist": False,
    "auto_candidates": False,        # найденные в панели, но не внесённые в реестр
    "allowlist_every_h": 6,
    "remind_hours": 6,
    "utc_offset": 5,                 # Екатеринбург
}

LIMITS: dict[str, tuple[float, float]] = {
    "interval_min": (20, 240),       # чаще 20 минут — нельзя
    "spread_s": (0, 900), "gap_min_s": (1, 120), "gap_max_s": (1, 300),
    "recheck_delay_s": (20, 600), "req_timeout_s": (5, 60),
    "back_after_cycles": (1, 50), "back_min_gap_min": (20, 1440), "quarantine_min": (0, 1440),
    "max_patches_24h": (0, 50), "max_switches_binding_24h": (1, 20),
    "post_check_s": (60, 900), "cycle_budget_s": (300, 3600),
    "allowlist_every_h": (1, 168), "remind_hours": (0, 168),
    "utc_offset": (-12, 14),
}

_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def clock(dt: datetime, cfg: dict) -> str:
    return (dt + timedelta(hours=int(cfg.get("utc_offset", 5)))).strftime("%d.%m %H:%M")


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def clean_config(cfg: dict) -> dict:
    out = {**copy.deepcopy(DEFAULTS), **(cfg or {})}
    for key, (lo, hi) in LIMITS.items():
        try:
            out[key] = int(max(lo, min(hi, float(out.get(key, DEFAULTS[key])))))
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    if out["gap_max_s"] < out["gap_min_s"]:
        out["gap_max_s"] = out["gap_min_s"]
    # цикл не длиннее интервала: иначе следующий упрётся в блокировку
    out["cycle_budget_s"] = min(out["cycle_budget_s"], out["interval_min"] * 60 - 60)
    hx = out.get("probe_hops_exclude")
    out["probe_hops_exclude"] = [str(x).strip() for x in (hx if isinstance(hx, list) else []) if str(x).strip()]
    for key in ("enabled", "dry_run", "notify", "allow_unverified_allowlist", "auto_candidates",
                "probe_hops_only"):
        out[key] = bool(out.get(key, DEFAULTS[key]))
    out["transport"] = out.get("transport") if out.get("transport") in ("ssh", "direct") else "ssh"
    out["chat_id"] = str(out.get("chat_id") or "").strip()
    ports = out.get("donor_ports")
    out["donor_ports"] = sorted({int(p) for p in (ports if isinstance(ports, list) else [])
                                 if str(p).isdigit() and 0 < int(p) < 65536}) or [10443]
    tags = out.get("donor_inbound_tags")
    out["donor_inbound_tags"] = [str(t).strip() for t in (tags if isinstance(tags, list) else [])
                                 if str(t).strip()] or ["AI-CASCADE-IN"]
    svcs = out.get("services")
    out["services"] = [s for s in SERVICES if isinstance(svcs, list) and s in svcs] or list(SERVICES)
    donors = []
    seen = set()
    for d in out.get("donors") if isinstance(out.get("donors"), list) else []:
        if not isinstance(d, dict):
            continue
        addr = str(d.get("address") or "").strip()
        name = str(d.get("name") or addr).strip()
        try:
            port = int(d.get("port") or 10443)
        except (TypeError, ValueError):
            continue
        if not addr or not (0 < port < 65536) or (addr, port) in seen:
            continue
        seen.add((addr, port))
        item = {"name": name, "address": addr, "port": port}
        for k in ("node", "host", "successor"):
            if str(d.get(k) or "").strip():
                item[k] = str(d[k]).strip()
        if d.get("retired"):
            item["retired"] = True
        if d.get("disabled"):
            item["disabled"] = True
        if isinstance(d.get("services"), list):
            item["services"] = [s for s in SERVICES if s in d["services"]]
        donors.append(item)
    out["donors"] = donors
    for key in ("priority", "probe_hops"):
        raw = out.get(key) if isinstance(out.get(key), dict) else {}
        out[key] = {str(k): [str(x).strip() for x in v if str(x).strip()]
                    for k, v in raw.items() if isinstance(v, list)}
    raw = out.get("egress_overrides") if isinstance(out.get("egress_overrides"), dict) else {}
    out["egress_overrides"] = {str(k): str(v).strip() for k, v in raw.items() if _is_ip(str(v).strip())}
    return {k: out[k] for k in DEFAULTS}


# ── реестр: что видно в панели ──────────────────────────────────────────────

_SVC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("claude", re.compile(r"(^|\.)(claude\.ai|claude\.com|anthropic\.com|claudeusercontent\.com)$|^anthropic$|^claude$")),
    ("gemini", re.compile(r"(^|\.)(ai\.google\.dev|gemini\.google\.com|aistudio\.google\.com|"
                          r"generativelanguage\.googleapis\.com)$|alkali(core|makersuite)-pa|^gemini$|bard")),
    ("elevenlabs", re.compile(r"(^|\.)elevenlabs\.(io|com|cloud)$|^elevenlabs$")),
    ("tiktok", re.compile(r"(^|\.)(tiktok\.com|tiktokv\.com|tiktokv\.us|tiktokv\.eu|tiktokcdn\.com|"
                          r"tiktokcdn-us\.com|byteoversea\.com|ttwstatic\.com)$|^tiktok$")),
    ("openai", re.compile(r"(^|\.)(openai\.com|chatgpt\.com|oaistatic\.com|oaiusercontent\.com)$|^openai$")),
]


def classify_domain(entry: str) -> str | None:
    d = str(entry or "").strip().lower()
    for pref in ("domain:", "full:", "geosite:", "keyword:", "regexp:", "dotless:"):
        if d.startswith(pref):
            d = d[len(pref):]
            break
    d = d.split("@")[0]
    for svc, rx in _SVC_PATTERNS:
        if rx.search(d):
            return svc
    return None


def _fp(user: str, pw: str) -> str:
    return hashlib.sha256(f"{user}\x00{pw}".encode()).hexdigest()[:10]


@dataclass
class Binding:
    id: str                      # "<uuid профиля>:<тег>"
    profile_uuid: str
    profile_name: str
    tag: str
    address: str
    port: int
    cred_fp: str
    services: list[str]
    node_names: list[str]
    node_ips: list[str]

    @property
    def donor_key(self) -> str:
        return f"{self.address}:{self.port}"


@dataclass
class Donor:
    key: str
    name: str
    address: str
    port: int
    node: str | None = None
    retired: bool = False
    successor: str | None = None          # ключ донора-преемника
    in_registry: bool = False
    disabled: bool = False
    services: list[str] = field(default_factory=lambda: list(SERVICES))
    used_by: list[str] = field(default_factory=list)       # id привязок
    primary_for: list[str] = field(default_factory=list)


@dataclass
class Registry:
    bindings: list[Binding] = field(default_factory=list)
    donors: dict[str, Donor] = field(default_factory=dict)
    creds: dict[str, list[Creds]] = field(default_factory=dict)   # только в памяти
    issues: list[dict] = field(default_factory=list)              # {code, key, text}
    node_ip: dict[str, str] = field(default_factory=dict)         # имя ноды → IP
    nodes_by_profile: dict[str, list[dict]] = field(default_factory=dict)


def _servers(ob: dict) -> list[dict]:
    st = ob.get("settings") or {}
    srv = st.get("servers")
    if isinstance(srv, list):
        return [s for s in srv if isinstance(s, dict)]
    if st.get("address"):
        return [st]
    return []


def _users(server: dict) -> list[Creds]:
    out = []
    for u in server.get("users") or []:
        if isinstance(u, dict) and u.get("user") is not None:
            out.append(Creds(str(u.get("user") or ""), str(u.get("pass") or "")))
    if not out and server.get("user") is not None:
        out.append(Creds(str(server.get("user") or ""), str(server.get("pass") or "")))
    return out


def discover(profiles: list[dict], nodes: list[dict], cfg: dict, resolve=lambda h: h) -> Registry:
    """Профили и ноды панели → привязки, доноры, логины (в памяти). Без сети."""
    reg = Registry()
    ports = set(cfg["donor_ports"])
    reg_by_addr = {(d["address"], d["port"]): d for d in cfg["donors"]}
    reg_addrs = {a for a, _ in reg_by_addr}

    # ноды панели
    node_by_name: dict[str, dict] = {}
    for n in nodes:
        name = str(n.get("name") or "")
        addr = str(n.get("address") or "").strip()
        ip = addr if _is_ip(addr) else (resolve(addr) or "")
        ip = cfg["egress_overrides"].get(name, ip)
        if name:
            node_by_name[name] = n
            if ip:
                reg.node_ip[name] = ip
        prof = ((n.get("configProfile") or {}).get("activeConfigProfileUuid")) or ""
        if prof:
            reg.nodes_by_profile.setdefault(prof, []).append(
                {"name": name, "uuid": n.get("uuid"), "ip": ip,
                 "connected": n.get("isConnected") is True, "disabled": n.get("isDisabled") is True,
                 "online": n.get("usersOnline") if isinstance(n.get("usersOnline"), int) else 0})

    cred_count: dict[str, dict[str, tuple[Creds, int]]] = {}

    def add_creds(key: str, c: Creds, weight: int = 1) -> None:
        bucket = cred_count.setdefault(key, {})
        fp = _fp(c.user, c.password)
        old = bucket.get(fp)
        bucket[fp] = (c, (old[1] if old else 0) + weight)

    def ensure_donor(addr: str, port: int) -> Donor:
        key = f"{addr}:{port}"
        if key in reg.donors:
            return reg.donors[key]
        r = reg_by_addr.get((addr, port))
        node_name = None
        if r and r.get("node"):
            node_name = r["node"]
        else:
            for nm, ip in reg.node_ip.items():
                if ip == addr:
                    node_name = nm
                    break
        d = Donor(key=key, name=(r or {}).get("name") or node_name or addr, address=addr, port=port,
                  node=node_name, retired=bool((r or {}).get("retired")), in_registry=bool(r),
                  disabled=bool((r or {}).get("disabled")),
                  services=list((r or {}).get("services") or SERVICES))
        reg.donors[key] = d
        return d

    for d in cfg["donors"]:
        ensure_donor(d["address"], d["port"])

    for p in profiles:
        puid = str(p.get("uuid") or "")
        pname = str(p.get("name") or puid[:8])
        conf = p.get("config") or {}
        p_nodes = reg.nodes_by_profile.get(puid, [])
        # доноры-входы: AI-CASCADE-IN на нодах этого профиля
        for ib in conf.get("inbounds") or []:
            if not isinstance(ib, dict) or ib.get("tag") not in cfg["donor_inbound_tags"]:
                continue
            try:
                iport = int(ib.get("port") or 0)
            except (TypeError, ValueError):
                continue
            accounts = [Creds(str(a.get("user") or ""), str(a.get("pass") or ""))
                        for a in ((ib.get("settings") or {}).get("accounts") or []) if isinstance(a, dict)]
            for pn in p_nodes:
                if pn["ip"]:
                    don = ensure_donor(pn["ip"], iport)
                    if not don.node:
                        don.node = pn["name"]
                    for c in accounts:
                        add_creds(don.key, c, weight=100)     # вход донора — главный источник
        # привязки
        rules = (conf.get("routing") or {}).get("rules") or []
        tag_svcs: dict[str, set[str]] = {}
        for ru in rules:
            if not isinstance(ru, dict) or not ru.get("outboundTag"):
                continue
            svcs = {classify_domain(x) for x in (ru.get("domain") or [])} - {None}
            tag_svcs.setdefault(ru["outboundTag"], set()).update(s for s in svcs if s in cfg["services"])
        for ob in conf.get("outbounds") or []:
            if not isinstance(ob, dict) or ob.get("protocol") not in ("socks", "http"):
                continue
            srv = _servers(ob)
            if len(srv) != 1:
                continue                       # несколько серверов — не наш формат, не трогаем
            s = srv[0]
            addr = str(s.get("address") or "").strip()
            try:
                port = int(s.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if not addr or (port not in ports and addr not in reg_addrs):
                continue
            tag = str(ob.get("tag") or "")
            users = _users(s)
            don = ensure_donor(addr, port)
            for c in users:
                add_creds(don.key, c)
            b = Binding(
                id=f"{puid}:{tag}", profile_uuid=puid, profile_name=pname, tag=tag, address=addr, port=port,
                cred_fp=_fp(users[0].user, users[0].password) if users else "",
                services=sorted(tag_svcs.get(tag, set()), key=SERVICES.index),
                node_names=[pn["name"] for pn in p_nodes], node_ips=[pn["ip"] for pn in p_nodes if pn["ip"]],
            )
            reg.bindings.append(b)
            don.used_by.append(b.id)

    # логины: чаще встречающиеся — первыми
    for key, bucket in cred_count.items():
        reg.creds[key] = [c for c, _n in sorted(bucket.values(), key=lambda x: -x[1])]

    # преемники и смена IP
    by_name: dict[str, list[Donor]] = {}
    for d in reg.donors.values():
        by_name.setdefault(d.name, []).append(d)
    for r in cfg["donors"]:
        don = reg.donors[f"{r['address']}:{r['port']}"]
        if r.get("successor"):
            succ = next((x for x in cfg["donors"] if x["name"] == r["successor"] and not x.get("retired")), None)
            if succ:
                don.successor = f"{succ['address']}:{succ['port']}"
        # адрес в DNS-имени
        if r.get("host"):
            ip = resolve(r["host"]) or ""
            if not ip:
                reg.issues.append({"code": "host_unresolved", "key": don.key,
                                   "text": f"{don.name}: имя {r['host']} не резолвится"})
            elif ip != don.address:
                reg.issues.append({"code": "ip_changed", "key": don.key,
                                   "text": f"{don.name}: {r['host']} теперь {ip}, в реестре {don.address}"})
        # адрес ноды в панели
        if r.get("node") and not r.get("retired"):
            pn = node_by_name.get(r["node"])
            if pn is None:
                reg.issues.append({"code": "node_missing", "key": don.key,
                                   "text": f"{don.name}: ноды {r['node']} нет в панели"})
            else:
                ip = reg.node_ip.get(r["node"], "")
                if ip and ip != don.address:
                    newd = ensure_donor(ip, don.port)
                    newd.name, newd.node, newd.in_registry = don.name, r["node"], True
                    don.successor = newd.key
                    if don.key in reg.creds and newd.key not in reg.creds:
                        reg.creds[newd.key] = list(reg.creds[don.key])
                    reg.issues.append({"code": "ip_changed", "key": don.key,
                                       "text": f"{don.name}: у ноды {r['node']} в панели адрес {ip}, "
                                               f"в реестре {don.address} — обновите реестр"})

    retired_keys = {k for k, d in reg.donors.items() if d.retired}
    for b in reg.bindings:
        if b.donor_key in retired_keys:
            d = reg.donors[b.donor_key]
            reg.issues.append({"code": "retired_in_use", "key": b.donor_key,
                               "text": f"{b.profile_name}/{b.tag} смотрит на списанный адрес "
                                       f"{d.address} ({d.name})"})
        if not b.services:
            reg.issues.append({"code": "no_services", "key": b.id,
                               "text": f"{b.profile_name}/{b.tag}: правил на ИИ/TikTok нет — не следим"})

    # основной донор по сервису: из priority или самый используемый
    for svc in SERVICES:
        names = cfg["priority"].get(svc) or []
        prim = next((d for nm in names for d in reg.donors.values() if d.name == nm and not d.retired), None)
        if prim is None:
            counts: dict[str, int] = {}
            for b in reg.bindings:
                if svc in b.services and b.donor_key not in retired_keys:
                    counts[b.donor_key] = counts.get(b.donor_key, 0) + 1
            if counts:
                prim = reg.donors[max(counts, key=lambda k: (counts[k], k))]
        if prim:
            prim.primary_for.append(svc)
    return reg


def probe_plan(reg: Registry, cfg: dict) -> dict[str, list[str]]:
    """{ключ донора: сервисы}. Только доноры в деле или в реестре, без списанных."""
    needed: set[str] = set()
    for b in reg.bindings:
        needed.update(b.services)
    plan: dict[str, list[str]] = {}
    for key, d in reg.donors.items():
        if d.retired or d.disabled or key not in reg.creds:
            continue
        own = {s for b in reg.bindings if b.donor_key == key for s in b.services}
        if not (d.used_by or d.in_registry or cfg["auto_candidates"]):
            continue
        svcs = own | (needed & set(d.services))
        svcs &= set(cfg["services"])
        if svcs:
            plan[key] = [s for s in SERVICES if s in svcs]
    return plan


# ── здоровье ────────────────────────────────────────────────────────────────

def health_of(first: tuple[str, str], recheck: tuple[str, str] | None) -> str:
    """Провал засчитывается, только если перепроверка тоже провалилась."""
    raw = first[0]
    if raw in ALIVE_RAW:
        return ALIVE
    if raw in FAIL_RAW:
        if recheck is None:
            return UNKNOWN
        if recheck[0] in FAIL_RAW:
            return DEAD
        if recheck[0] in ALIVE_RAW:
            return ALIVE        # мигнул — жив, но живым циклом подряд это не считается (см. update_streaks)
        return UNKNOWN
    return UNKNOWN


def update_streaks(state: dict, results: dict[str, dict[str, dict]], now: datetime) -> None:
    """results: {ключ: {сервис: {health, raw, note, flapped}}} → state['donors'] с сериями."""
    donors = state.setdefault("donors", {})
    for key, per in results.items():
        st = donors.setdefault(key, {"svc": {}})
        for svc, r in per.items():
            s = st["svc"].setdefault(svc, {"alive_streak": 0, "fail_streak": 0})
            s.update({"health": r["health"], "raw": r["raw"], "note": r.get("note", ""), "at": _iso(now)})
            if r["health"] == ALIVE and not r.get("flapped"):
                s["alive_streak"] = int(s.get("alive_streak", 0)) + 1
                s["fail_streak"] = 0
            elif r["health"] == DEAD:
                s["fail_streak"] = int(s.get("fail_streak", 0)) + 1
                s["alive_streak"] = 0
                s["last_dead_at"] = _iso(now)
            else:
                s["alive_streak"] = 0      # «не знаем» или мигание рвут серию живых циклов


def cycle_health(state: dict, key: str, svc: str, cycle_at: str | None) -> str:
    s = ((state.get("donors") or {}).get(key) or {}).get("svc", {}).get(svc)
    if not s or s.get("at") != cycle_at:
        return UNKNOWN           # в этом цикле не меряли — значит не знаем
    return s.get("health") or UNKNOWN


# ── фаервол донора ──────────────────────────────────────────────────────────

def parse_firewall(text: str, port: int) -> tuple[set[str] | None, bool]:
    """(разрешённые источники, открыт_всем). None — правил на порт не найдено.

    Понимает iptables-save (в т.ч. цепочки ufw, multiport, ipset src) и nft.
    """
    p = str(port)
    ipt, _, rest = text.partition("@@IPSET")
    ipset_txt, _, nft = rest.partition("@@NFT")
    sets: dict[str, set[str]] = {}
    cur = None
    in_members = False
    for line in ipset_txt.splitlines():
        m = re.match(r"Name:\s*(\S+)", line)
        if m:
            cur, in_members = m.group(1), False
            sets[cur] = set()
            continue
        if line.startswith("Members:"):
            in_members = True
            continue
        if in_members and cur and line.strip():
            sets[cur].add(line.split()[0])
    allowed: set[str] = set()
    found = False
    open_all = False

    def port_match(line: str) -> bool:
        if re.search(rf"--dport {p}(\s|$)", line):
            return True
        m = re.search(r"--dports (\S+)", line)
        if m:
            for part in m.group(1).split(","):
                if ":" in part:
                    lo, hi = part.split(":")
                    if lo.isdigit() and hi.isdigit() and int(lo) <= port <= int(hi):
                        return True
                elif part == p:
                    return True
        return False

    for line in ipt.splitlines():
        if not line.startswith("-A ") or not port_match(line):
            continue
        found = True
        if "-j ACCEPT" not in line:
            continue
        src = re.search(r"\s-s (\S+)", line)
        mset = re.search(r"--match-set (\S+) src", line)
        if src:
            allowed.add(src.group(1))
        elif mset:
            allowed |= sets.get(mset.group(1), set())
        elif " ! -s " not in line:
            open_all = True
    # nft: «... dport 10443 ... ip saddr { a, b } ... accept»
    nft_sets: dict[str, set[str]] = {}
    for m in re.finditer(r"set (\S+) \{(.*?)\n\s*\}", nft, re.S):
        el = re.search(r"elements = \{(.*?)\}", m.group(2), re.S)
        if el:
            nft_sets[m.group(1)] = {x.strip() for x in el.group(1).replace("\n", " ").split(",") if x.strip()}
    for line in nft.splitlines():
        if not re.search(rf"dport (\{{[^}}]*\b{p}\b[^}}]*\}}|{p})(\s|$)", line):
            continue
        found = True
        if "accept" not in line:
            continue
        m = re.search(r"ip saddr (\{[^}]*\}|@\S+|\S+)", line)
        if not m:
            open_all = True
            continue
        v = m.group(1)
        if v.startswith("@"):
            allowed |= nft_sets.get(v[1:], set())
        elif v.startswith("{"):
            allowed |= {x.strip() for x in v.strip("{}").split(",") if x.strip()}
        else:
            allowed.add(v)
    if not found:
        return None, False
    return allowed, open_all


def ip_allowed(ip: str, allowed: set[str]) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for x in allowed:
        try:
            if a in ipaddress.ip_network(x, strict=False):
                return True
        except ValueError:
            continue
    return False


def allowlist_gaps(reg: Registry, state: dict) -> dict[str, list[str]]:
    """{ключ донора: ноды, которые им пользуются, но не в его allowlist}."""
    out: dict[str, list[str]] = {}
    for key, d in reg.donors.items():
        fw = ((state.get("donors") or {}).get(key) or {}).get("allowlist") or {}
        if fw.get("ips") is None or fw.get("open"):
            continue
        allowed = set(fw["ips"])
        miss = set()
        for b in reg.bindings:
            if b.donor_key != key:
                continue
            for name in b.node_names:
                ip = reg.node_ip.get(name)
                if name == d.node or ip == d.address:
                    continue  # нода сама является донором — себе доступ не нужен
                if ip and not ip_allowed(ip, allowed):
                    miss.add(name)
        if miss:
            out[key] = sorted(miss)
    return out


# ── решения ─────────────────────────────────────────────────────────────────

@dataclass
class Action:
    kind: str            # switch | switch_back | no_candidate | cap | halted | hold
    binding: str
    profile_uuid: str
    tag: str
    src: str             # ключ донора сейчас
    dst: str | None
    reason: str


def _allowlist_ok(reg: Registry, state: dict, b: Binding, key: str, cfg: dict) -> tuple[bool, str]:
    fw = ((state.get("donors") or {}).get(key) or {}).get("allowlist") or {}
    if fw.get("open"):
        return True, "порт открыт всем"
    if fw.get("ips") is not None:
        miss = [n for n in b.node_names if reg.node_ip.get(n) and not ip_allowed(reg.node_ip[n], set(fw["ips"]))]
        return (not miss), ("ноды не в allowlist: " + ", ".join(miss)) if miss else "allowlist сверен"
    # Список не сверен: годится, только если этот же профиль уже ходит на донора
    if any(x.profile_uuid == b.profile_uuid and x.donor_key == key for x in reg.bindings):
        return True, "профиль уже ходит на этого донора"
    if (((state.get("bindings") or {}).get(b.id) or {}).get("home")) == key:
        return True, "родной донор привязки — с этих нод уже работал"
    if cfg["allow_unverified_allowlist"]:
        return True, "allowlist не сверен (разрешено настройкой)"
    return False, "allowlist не сверен"


def candidates(reg: Registry, state: dict, b: Binding, cfg: dict, now: datetime, cycle_at: str) -> list[tuple[str, str]]:
    """Подходящие доноры для привязки по порядку: [(ключ, почему подходит)]."""
    bst = (state.get("bindings") or {}).get(b.id) or {}
    order: list[str] = []
    home = bst.get("home")
    if home:
        order.append(home)
    cur = reg.donors.get(b.donor_key)
    if cur and cur.successor:
        order.append(cur.successor)
    for svc in b.services:
        for nm in cfg["priority"].get(svc) or []:
            order += [k for k, d in reg.donors.items() if d.name == nm]
    usage: dict[str, int] = {}
    for x in reg.bindings:
        if set(b.services) & set(x.services):
            usage[x.donor_key] = usage.get(x.donor_key, 0) + 1
    order += sorted(usage, key=lambda k: (-usage[k], k))
    order += sorted(reg.donors)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    quarantine = timedelta(minutes=cfg["quarantine_min"])
    for key in order:
        if key in seen or key == b.donor_key:
            continue
        seen.add(key)
        d = reg.donors.get(key)
        if d is None or d.retired or d.disabled or key not in reg.creds:
            continue
        if not (d.in_registry or d.used_by or cfg["auto_candidates"]):
            continue
        if d.address in b.node_ips or (d.node and d.node in b.node_names):
            continue                                   # донор на нодах этого же профиля — петля
        if any(cycle_health(state, key, s, cycle_at) != ALIVE for s in b.services):
            continue                                   # не подтверждён живым — никогда
        dst = (state.get("donors") or {}).get(key) or {}
        recent_dead = False
        for s in b.services:
            ld = _parse((dst.get("svc") or {}).get(s, {}).get("last_dead_at"))
            if ld and now - ld < quarantine:
                recent_dead = True
        if recent_dead:
            continue
        ok, why = _allowlist_ok(reg, state, b, key, cfg)
        if not ok:
            continue
        out.append((key, why))
    return out


def _switches_24h(state: dict, bid: str | None, now: datetime) -> int:
    since = now - timedelta(hours=24)
    rows = state.get("switches") or []
    return sum(1 for r in rows if (bid is None or r.get("binding") == bid) and (_parse(r.get("at")) or now) > since)


def patches_24h(state: dict, now: datetime) -> int:
    since = now - timedelta(hours=24)
    return sum(1 for r in state.get("patches") or [] if (_parse(r.get("at")) or now) > since)


def profile_in_use(reg: Registry, puid: str) -> bool:
    """На профиле есть хоть одна включённая нода. Пустые профили не переключаем: PATCH впустую."""
    return any(not n["disabled"] for n in reg.nodes_by_profile.get(puid, []))


def profile_rank(reg: Registry, puid: str) -> tuple:
    """Порядок обработки профилей: меньше пользователей в сети — раньше (сбой заденет меньше людей)."""
    nodes = reg.nodes_by_profile.get(puid, [])
    name = next((b.profile_name for b in reg.bindings if b.profile_uuid == puid), "")
    return (sum(int(n.get("online") or 0) for n in nodes), name, puid)


def decide(reg: Registry, state: dict, cfg: dict, now: datetime, cycle_at: str) -> list[Action]:
    acts: list[Action] = []
    patches_left = cfg["max_patches_24h"] - patches_24h(state, now)
    profiles_planned: set[str] = set()
    for b in sorted(reg.bindings, key=lambda x: (profile_rank(reg, x.profile_uuid), x.tag)):
        if not b.services or not profile_in_use(reg, b.profile_uuid):
            continue
        bst = (state.get("bindings") or {}).get(b.id) or {}
        cur = reg.donors.get(b.donor_key)
        if cur is None:
            continue
        healths = {s: (RETIRED if cur.retired else cycle_health(state, b.donor_key, s, cycle_at)) for s in b.services}
        failed = [s for s, h in healths.items() if h in (DEAD, RETIRED)]
        kind = None
        target = None
        reason = ""
        if failed:
            cands = candidates(reg, state, b, cfg, now, cycle_at)
            names = ", ".join(SERVICE_NAMES[s] for s in failed)
            if not cands:
                acts.append(Action("no_candidate", b.id, b.profile_uuid, b.tag, b.donor_key, None,
                                   f"{cur.name} не работает ({names}), живого кандидата нет"))
                continue
            kind, (target, why) = "switch", cands[0]
            reason = f"{cur.name} не работает ({names}) → {reg.donors[target].name} ({why})"
            if _switches_24h(state, b.id, now) >= cfg["max_switches_binding_24h"]:
                acts.append(Action("cap", b.id, b.profile_uuid, b.tag, b.donor_key, target,
                                   f"привязка переключалась {cfg['max_switches_binding_24h']} раз за сутки — "
                                   f"дальше вручную ({reason})"))
                continue
        else:
            home = bst.get("home")
            if not home or home == b.donor_key or home not in reg.donors:
                continue
            hd = reg.donors[home]
            if hd.retired:
                continue
            streak_ok = all(
                ((state.get("donors") or {}).get(home) or {}).get("svc", {}).get(s, {}).get("alive_streak", 0)
                >= cfg["back_after_cycles"] and cycle_health(state, home, s, cycle_at) == ALIVE
                for s in b.services)
            if not streak_ok:
                continue
            last = _parse(bst.get("switched_at"))
            if last and now - last < timedelta(minutes=cfg["back_min_gap_min"]):
                acts.append(Action("hold", b.id, b.profile_uuid, b.tag, b.donor_key, home,
                                   f"{hd.name} снова жив, возврат не раньше чем через "
                                   f"{cfg['back_min_gap_min']} мин после прошлого переключения"))
                continue
            if home not in reg.creds or (hd.address in b.node_ips) or (hd.node and hd.node in b.node_names):
                continue
            ok, why = _allowlist_ok(reg, state, b, home, cfg)
            if not ok:
                continue
            kind, target = "switch_back", home
            reason = (f"{hd.name} жив {cfg['back_after_cycles']} цикла подряд — возврат с "
                      f"{cur.name}")
        if state.get("halted"):
            acts.append(Action("halted", b.id, b.profile_uuid, b.tag, b.donor_key, target,
                               f"автоматика остановлена ({state['halted'].get('reason')}); надо: {reason}"))
            continue
        if b.profile_uuid not in profiles_planned and patches_left <= 0:
            acts.append(Action("cap", b.id, b.profile_uuid, b.tag, b.donor_key, target,
                               f"дневной потолок PATCH ({cfg['max_patches_24h']}) исчерпан; надо: {reason}"))
            continue
        if b.profile_uuid not in profiles_planned:
            profiles_planned.add(b.profile_uuid)
            patches_left -= 1
        acts.append(Action(kind, b.id, b.profile_uuid, b.tag, b.donor_key, target, reason))
    return acts


class Conflict(Exception):
    """Профиль поменяли мимо нас — не трогаем."""


def apply_changes(config: dict, changes: list[tuple[str, str, int, str, int, Creds]]) -> dict:
    """Новая копия конфига: у аутбаунда tag адрес/порт/логин меняются на новые.

    changes: [(tag, old_addr, old_port, new_addr, new_port, creds)]. Если аутбаунд
    уже не на старом адресе — Conflict (сравнение-и-замена).
    """
    new = copy.deepcopy(config)
    obs = {o.get("tag"): o for o in new.get("outbounds") or [] if isinstance(o, dict)}
    for tag, oa, op, na, np_, creds in changes:
        ob = obs.get(tag)
        if ob is None:
            raise Conflict(f"аутбаунда {tag} больше нет")
        srv = _servers(ob)
        if len(srv) != 1:
            raise Conflict(f"у {tag} не один сервер")
        s = srv[0]
        if str(s.get("address")) != oa or int(s.get("port") or 0) != op:
            raise Conflict(f"{tag} уже смотрит на {s.get('address')}:{s.get('port')}")
        s["address"] = na
        s["port"] = np_
        if isinstance(s.get("users"), list) and s["users"]:
            u0 = s["users"][0]
            u0["user"], u0["pass"] = creds.user, creds.password
            del s["users"][1:]
        elif "user" in s:
            s["user"], s["pass"] = creds.user, creds.password
        else:
            s["users"] = [{"user": creds.user, "pass": creds.password}]
    return new


def outbound_target(config: dict, tag: str) -> str | None:
    for o in config.get("outbounds") or []:
        if isinstance(o, dict) and o.get("tag") == tag:
            srv = _servers(o)
            if len(srv) == 1:
                return f"{srv[0].get('address')}:{srv[0].get('port')}"
    return None


def sync_bindings(reg: Registry, state: dict, now: datetime) -> list[str]:
    """Свести state['bindings'] с панелью. Чужие правки — новая «родина». Возвращает заметки."""
    notes: list[str] = []
    bs = state.setdefault("bindings", {})
    live = {b.id: b for b in reg.bindings}
    for bid in list(bs):
        if bid not in live:
            bs.pop(bid)
    for bid, b in live.items():
        st = bs.get(bid)
        if st is None:
            home = b.donor_key
            d = reg.donors.get(home)
            bs[bid] = {"home": home, "current": home, "since": _iso(now)}
            if d and d.retired and d.successor:
                bs[bid]["home"] = d.successor
            continue
        if st.get("current") != b.donor_key:
            # мы так не переключали → правил человек или другой агент
            notes.append(f"{b.profile_name}/{b.tag}: адрес сменён вне сторожа "
                         f"({st.get('current')} → {b.donor_key}) — считаю его исходным")
            st["home"] = b.donor_key
            st["current"] = b.donor_key
            st.pop("switched_at", None)
    return notes


# ── база ────────────────────────────────────────────────────────────────────

async def get_config(db: AsyncSession) -> dict:
    return clean_config(await subwatch._json_setting(db, CONFIG_KEY, {}))


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await subwatch._save_json(db, CONFIG_KEY, clean_config(cfg))


async def get_state(db: AsyncSession) -> dict:
    st = await subwatch._json_setting(db, STATE_KEY, {})
    for k, v in (("donors", {}), ("bindings", {}), ("switches", []), ("patches", []), ("alerts", {})):
        if not isinstance(st.get(k), type(v)):
            st[k] = v
    return st


async def save_state(db: AsyncSession, st: dict) -> None:
    now = _now()
    st["switches"] = [r for r in st.get("switches", []) if (_parse(r.get("at")) or now) > now - timedelta(days=8)]
    st["patches"] = [r for r in st.get("patches", []) if (_parse(r.get("at")) or now) > now - timedelta(days=8)]
    await subwatch._save_json(db, STATE_KEY, st)


async def get_log(db: AsyncSession) -> list[dict]:
    rows = (await subwatch._json_setting(db, LOG_KEY, {"rows": []})).get("rows")
    return rows if isinstance(rows, list) else []


_log_lock = asyncio.Lock()


async def append_log(db: AsyncSession, rows: list[dict]) -> None:
    """Дописать журнал. Чтение-запись-commit под замком: цикл и ручки API пишут из разных сессий."""
    if rows:
        async with _log_lock:
            await subwatch._save_json(db, LOG_KEY, {"rows": ((await get_log(db)) + rows)[-LOG_KEEP:]})
            await db.commit()


def last_cycle_at(st: dict) -> datetime | None:
    """Когда начинался последний цикл: старт пишется до проб, поэтому упавший цикл тоже считается."""
    marks = [_parse((st.get("last_cycle") or {}).get("at")), _parse(st.get("cycle_started_at"))]
    marks = [m for m in marks if m]
    return max(marks) if marks else None


def _row(now: datetime, kind: str, text: str, cfg: dict, **extra) -> dict:
    return {"ts": _iso(now), "kind": kind, "text": text, "dry_run": bool(cfg.get("dry_run")), **extra}


async def _backup(db: AsyncSession, profile: dict, reason: str) -> str:
    """Профиль целиком (в нём логины доноров) — только шифрованным в app_settings."""
    import json

    from app.services.crypto import encrypt_secret

    bid = _now().strftime("%Y%m%d-%H%M%S-") + uuidlib.uuid4().hex[:6]
    data = await subwatch._json_setting(db, BACKUP_KEY, {"rows": []})
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    rows.append({"id": bid, "at": _iso(_now()), "profile_uuid": profile.get("uuid"),
                 "profile_name": profile.get("name"), "reason": reason,
                 "blob": encrypt_secret(json.dumps(profile, ensure_ascii=False))})
    await subwatch._save_json(db, BACKUP_KEY, {"rows": rows[-BACKUPS_KEEP:]})
    await db.commit()
    return bid


async def list_backups(db: AsyncSession) -> list[dict]:
    """Список бэкапов без содержимого (в нём логины доноров)."""
    data = await subwatch._json_setting(db, BACKUP_KEY, {"rows": []})
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    return [{k: r.get(k) for k in ("id", "at", "profile_uuid", "profile_name", "reason")} for r in rows][::-1]


async def load_backup(db: AsyncSession, backup_id: str) -> dict | None:
    """Профиль из бэкапа (расшифрованный) или None."""
    import json

    from app.services.crypto import decrypt_secret

    data = await subwatch._json_setting(db, BACKUP_KEY, {"rows": []})
    for r in data.get("rows") if isinstance(data.get("rows"), list) else []:
        if r.get("id") == backup_id:
            return {"meta": {k: r.get(k) for k in ("id", "at", "profile_uuid", "profile_name", "reason")},
                    "profile": json.loads(decrypt_secret(r["blob"]))}
    return None


def outbound_diff(cur: dict, old: dict) -> list[dict]:
    """Чем аутбаунды-доноры текущего конфига отличаются от бэкапа. Без логинов — только факт смены."""
    def targets(conf: dict) -> dict[str, tuple[str | None, str]]:
        out = {}
        for o in conf.get("outbounds") or []:
            if isinstance(o, dict) and o.get("protocol") in ("socks", "http"):
                srv = _servers(o)
                if len(srv) == 1:
                    us = _users(srv[0])
                    out[str(o.get("tag"))] = (outbound_target(conf, o.get("tag")),
                                              _fp(us[0].user, us[0].password) if us else "")
        return out
    a, b = targets(cur), targets(old)
    diff = []
    for tag in sorted(set(a) | set(b)):
        if a.get(tag) != b.get(tag):
            diff.append({"tag": tag, "now": (a.get(tag) or (None,))[0], "backup": (b.get(tag) or (None,))[0],
                         "creds_change": bool(a.get(tag) and b.get(tag) and a[tag][1] != b[tag][1])})
    return diff


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    return cfg.get("chat_id") or ((await subwatch.get_config(db)).get("chat_id") or "").strip()


async def bot_ready(db: AsyncSession, cfg: dict) -> bool:
    return bool(await subwatch.bot_token(db)) and bool(await effective_chat(db, cfg))


async def notify(db: AsyncSession, cfg: dict, text: str) -> bool:
    if not cfg.get("notify"):
        return False
    try:
        token = await subwatch.bot_token(db)
        chat = await effective_chat(db, cfg)
        if not token or not chat:
            return False
        pre = "🧪 [сухой прогон] " if cfg.get("dry_run") else ""
        err = await asyncio.to_thread(send_message, token, chat, "🧩 <b>Доноры ИИ</b>: " + pre + html.escape(text))
        if err:
            log.warning("donorwatch: уведомление не ушло: %s", err)
            return False
        return True
    except Exception:  # noqa: BLE001
        log.exception("donorwatch: уведомление упало")
        return False


async def alert(db: AsyncSession, st: dict, cfg: dict, key: str, text: str | None, now: datetime,
                rows: list[dict]) -> None:
    """Уведомления на переломах: новое/изменилось — сразу, держится — раз в remind_hours, ушло — «ок»."""
    alerts = st.setdefault("alerts", {})
    old = alerts.get(key)
    if text is None:
        if old:
            alerts.pop(key, None)
            msg = f"🟢 снято: {old['text']}"
            rows.append(_row(now, "clear", msg, cfg, key=key))
            await notify(db, cfg, msg)
        return
    if old and old.get("text") == text:
        last = _parse(old.get("notified_at"))
        if cfg["remind_hours"] and last and now - last >= timedelta(hours=cfg["remind_hours"]):
            old["notified_at"] = _iso(now)
            await notify(db, cfg, "🔁 " + text)
        return
    alerts[key] = {"text": text, "since": _iso(now), "notified_at": _iso(now)}
    rows.append(_row(now, "alert", text, cfg, key=key))
    await notify(db, cfg, text)


def registry_snapshot(reg: Registry) -> dict:
    """То, что можно показать в API: без логинов."""
    return {
        "donors": [{"key": d.key, "name": d.name, "address": d.address, "port": d.port, "node": d.node,
                    "retired": d.retired, "in_registry": d.in_registry, "disabled": d.disabled,
                    "successor": d.successor, "primary_for": d.primary_for, "used_by": len(d.used_by),
                    "creds_known": d.key in reg.creds}
                   for d in sorted(reg.donors.values(), key=lambda x: (x.retired, -len(x.used_by), x.name))],
        "bindings": [{"id": b.id, "profile_uuid": b.profile_uuid, "profile_name": b.profile_name, "tag": b.tag,
                      "donor": b.donor_key, "services": b.services, "nodes": b.node_names}
                     for b in sorted(reg.bindings, key=lambda x: (x.profile_name, x.tag))],
        "issues": [{"code": i["code"], "text": i["text"]} for i in reg.issues],
    }
