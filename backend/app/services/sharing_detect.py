"""Детектор шаринга по уникальным клиентским IP Xray (GetStatsOnlineIpList).

Не путать с ss ESTAB / proxy_peers: xHTTP держит пачку TCP на один IP.
Порог — разные исходные адреса одного UUID, не число сокетов.
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

from app.services import asn_lookup
from app.services.remnawave_api import RemnawaveApiError, rw_api, rw_nodes

log = logging.getLogger("remna.sharing")

# Живой туннель: lastSeen не старше этого.
WIN_5 = timedelta(minutes=5)
WIN_15 = timedelta(minutes=15)

# Один человек: 1 IP, реже 2–3 (LTE+Wi‑Fi / CGNAT).
# 8+ разных IP за 5 мин — ферма.
BAN_IPS_5M = 8
BAN_IPS_15M = 12

# Операторы, а не /16. Российские провайдеры держат один пул абонентов в
# нескольких /16 (МТС — 91.78.x и 91.79.x, Ростелеком — 85.112.x и 80.234.x),
# поэтому счёт по маске штрафовал человека с мобильным интернетом за то, что
# у него меняется адрес. Порог применяется к ОДНОВРЕМЕННО активным операторам.
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


def _reasons(ips_5m: int, conc_nets: int, ips_15m: int) -> list[str]:
    why = []
    if ips_5m >= BAN_IPS_5M:
        why.append(f"уникальных клиентских IP за 5 мин: {ips_5m} (порог {BAN_IPS_5M})")
    if conc_nets >= BAN_NETS_CONCURRENT:
        why.append(
            f"операторов одновременно: {conc_nets} (порог {BAN_NETS_CONCURRENT}) — "
            "разные сети в одну минуту, не смена адреса"
        )
    if ips_15m >= BAN_IPS_15M:
        why.append(f"уникальных IP за 15 мин: {ips_15m} (порог {BAN_IPS_15M})")
    return why


def _unwrap_user(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get("_error"):
        return {}
    if payload.get("id") is not None:
        return payload
    inner = payload.get("user") or payload.get("response")
    return inner if isinstance(inner, dict) else {}


@dataclass
class _UserStat:
    uid: int
    rows: list[dict] = field(default_factory=list)
    ips_5m: int = 0
    ips_15m: int = 0
    s16_5m: int = 0          # операторов за 5 мин (имя оставлено для совместимости)
    conc_ips: int = 0        # максимум одновременных IP
    conc_nets: int = 0       # максимум одновременных операторов
    own_ips: int = 0         # отсеяно адресов собственных нод
    nodes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    username: str = ""


_flagged_stats: dict[int, _UserStat] = {}



def _networks(ips) -> set[str]:
    """Операторы этих адресов. Падение резолва деградирует до /16, не до нуля."""
    try:
        return asn_lookup.networks_of(ips)
    except Exception:  # noqa: BLE001
        log.warning("ASN lookup failed, fallback to /16", exc_info=True)
        return {n for n in (_slash16(ip) for ip in ips) if n}


def _concurrency(rows: list[dict]) -> tuple[int, int]:
    """Максимум адресов и операторов, живших в одном окне CONCURRENCY_WINDOW.

    Человек, который переезжает с вышки на вышку, набирает адреса
    последовательно — в любом окне их один-два. Ферма даёт пачку сразу.
    """
    stamped = []
    for r in rows:
        ts = _parse_ts(r["lastSeen"])
        if ts:
            stamped.append((r["ip"], ts))
    if not stamped:
        return 0, 0
    window = CONCURRENCY_WINDOW.total_seconds()
    best_ips = best_nets = 0
    for _, anchor in stamped:
        group = {ip for ip, ts in stamped if abs((ts - anchor).total_seconds()) <= window}
        if len(group) > best_ips:
            best_ips = len(group)
            best_nets = max(best_nets, len(_networks(group)))
    return best_ips, best_nets


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
        st.conc_ips, st.conc_nets = _concurrency(st.rows)
        st.nodes = sorted({r["node"] for r in st.rows})
        st.reasons = _reasons(st.ips_5m, st.conc_nets, st.ips_15m)
    return by_user


def scan_once(agent_nodes: list[tuple[str, str, str]]) -> dict[str, Any]:
    """agent_nodes: (agent_id, name, host). Sync — вызывать из to_thread."""
    try:
        rw = rw_nodes()
    except RemnawaveApiError as exc:
        err = str(exc)
        mark_error(err)
        return snapshot()
    jobs: list[tuple[str, str, str]] = []
    addr_by_uuid: dict[str, str] = {}
    for n in rw:
        if not isinstance(n, dict) or n.get("isDisabled"):
            continue
        uuid = n.get("uuid")
        if not uuid:
            continue
        name = n.get("name") or uuid
        addr_by_uuid[str(uuid)] = str(n.get("address") or "")
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
    stats = _collect_stats(node_results, own_ips)
    flagged: list[_UserStat] = []
    for st in stats.values():
        if not st.reasons:
            continue
        card = _unwrap_user(_try_api(f"/api/users/{st.uid}"))
        name = (card.get("username") or "").lower()
        if name.startswith(SKIP_PREFIX):
            continue
        st.username = str(card.get("username") or "")
        flagged.append(st)

    with _lock:
        _flagged_stats.clear()
        _flagged_stats.update({st.uid: st for st in flagged})

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

    by_agent: dict[str, list[dict]] = defaultdict(list)
    for st in flagged:
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
            by_agent[aid].append({
                "user_id": st.uid,
                "username": st.username,
                "ips_5m": st.ips_5m,
                "ips_15m": st.ips_15m,
                "s16_5m": st.s16_5m,
                "conc_ips": st.conc_ips,
                "conc_nets": st.conc_nets,
                "own_ips": st.own_ips,
                "ips_on_node": len(on_node),
                "reasons": st.reasons,
                "rw_nodes": st.nodes,
            })

    out = {
        "scanned_at": _now().isoformat(),
        "error": None,
        "scanning": False,
        "online_users": len(stats),
        "flagged": len(flagged),
        "by_agent_id": dict(by_agent),
        "peers_by_agent_id": peers_by_agent,
        "thresholds": {
            "ips_5m": BAN_IPS_5M,
            "nets_concurrent": BAN_NETS_CONCURRENT,
            "ips_15m": BAN_IPS_15M,
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
        "sharing scan: online=%s flagged=%s share_nodes=%s peer_nodes=%s unmatched=%s",
        len(stats),
        len(flagged),
        len(by_agent),
        len(peers_by_agent),
        unmatched[:12],
    )
    return out


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
    hits = []
    for users in (snap.get("by_agent_id") or {}).values():
        for u in users:
            if u.get("user_id") == user_id:
                hits.append(u)
    reasons = hits[0]["reasons"] if hits else []
    ips_5m = hits[0]["ips_5m"] if hits else 0
    ips_15m = hits[0]["ips_15m"] if hits else 0
    s16_5m = hits[0]["s16_5m"] if hits else 0
    conc_ips = hits[0].get("conc_ips", 0) if hits else 0
    conc_nets = hits[0].get("conc_nets", 0) if hits else 0
    own_ips = hits[0].get("own_ips", 0) if hits else 0
    rw_nodes = hits[0].get("rw_nodes") if hits else []
    with _lock:
        st = _flagged_stats.get(user_id)

    # Live IPs: one more by-user is not available; reuse last scan reasons.
    lines = [
        f"# SHARING dossier  user {user_id}",
        f"# snapshot {_now().isoformat()}",
        "",
        "## Пользователь",
        *_fmt_user_header(card),
        "",
        "## Решение",
        "ШАРИНГ: да" if reasons else "ШАРИНГ: нет в последнем скане (карточка всё равно собрана)",
    ]
    for w in reasons:
        lines.append(f"- {w}")
    lines += [
        "",
        "## Доказательства шаринга",
        f"ноды: {', '.join(rw_nodes) if rw_nodes else '—'}",
        f"уникальных клиентских IP  5 мин: {ips_5m}",
        f"уникальных клиентских IP 15 мин: {ips_15m}",
        f"операторов за 5 мин: {s16_5m}  (один человек обычно 1–3)",
        f"ОДНОВРЕМЕННО адресов: {conc_ips}, операторов: {conc_nets}  "
        f"(окно {int(CONCURRENCY_WINDOW.total_seconds())} с — вот это и отличает "
        f"шаринг от смены адреса)",
        f"отсеяно адресов собственных нод: {own_ips}",
        f"HWID записей: {len(devices)}  лимит: {card.get('hwidDeviceLimit')}",
    ]
    for d in devices:
        if not isinstance(d, dict):
            continue
        lines.append(
            f"  hwid={d.get('hwid')} platform={d.get('platform')} "
            f"os={d.get('osVersion')} model={d.get('deviceModel')} "
            f"ua={d.get('userAgent')} ip={d.get('requestIp')} "
            f"updated={d.get('updatedAt')}"
        )
    if len(devices) <= 1 and ips_5m >= BAN_IPS_5M:
        lines.append(
            "  HWID не бьётся с числом IP: либо клон x-hwid, либо лимит не смотрит туннель."
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
        "Порог: уникальные IP Xray (вход на ноду), не TCP ss. "
        f"Шаринг если IP за 5 мин ≥ {BAN_IPS_5M} ИЛИ операторов одновременно ≥ {BAN_NETS_CONCURRENT} "
        f"ИЛИ IP за 15 мин ≥ {BAN_IPS_15M}."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_node_dossier(user_ids: list[int]) -> str:
    parts = [build_dossier(uid) for uid in user_ids]
    return "\n\n".join(parts) if parts else "на этой ноде шаринг в последнем скане не найден\n"
