"""Цикл сторожа доноров: панель → пробы → решения → (боевой режим) PATCH профилей.

Логика решений и разбор — в donor_watch (чистые функции, тесты). Здесь сеть,
база и порядок действий. Всё, что меняет панель, — только при dry_run=false,
по одному профилю, с бэкапом, сверкой и проверкой нод; любой сбой — стоп
автоматики (state.halted) до ручного снятия.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import time
import uuid as uuidlib
from datetime import datetime, timedelta

from sqlalchemy import select

from app.services import donor_probe as dp
from app.services import donor_watch as dw
from app.services.donor_watch import _iso, _now, _parse, _row

log = logging.getLogger("remna.donorwatch")

run_now = asyncio.Event()
force_next = False
_last_ips: dict[str, str] = {}


def _resolve(host: str) -> str:
    if dw._is_ip(host):
        return host
    try:
        from app.services.remnawave_api import _resolve as rr

        return rr(host)
    except Exception:  # noqa: BLE001
        return ""


def _panel_read() -> tuple[list[dict], list[dict]]:
    """Ноды и профили целиком. Только чтение. Исключение — панель недоступна."""
    from app.services.remnawave_api import rw_api

    nodes = rw_api("/api/nodes")
    nodes = nodes if isinstance(nodes, list) else (nodes or {}).get("nodes") or []
    lst = rw_api("/api/config-profiles")
    lst = (lst.get("configProfiles") if isinstance(lst, dict) else lst) or []
    profiles = []
    for p in lst:
        full = rw_api("/api/config-profiles/" + p["uuid"])
        full = full.get("configProfile", full) if isinstance(full, dict) else full
        profiles.append(full)
    return nodes, profiles


def _panel_profile(uuid: str) -> dict:
    from app.services.remnawave_api import rw_api

    full = rw_api("/api/config-profiles/" + uuid)
    return full.get("configProfile", full) if isinstance(full, dict) else full


def _panel_nodes() -> list[dict]:
    from app.services.remnawave_api import rw_api

    nodes = rw_api("/api/nodes")
    return nodes if isinstance(nodes, list) else (nodes or {}).get("nodes") or []


def _retry(fn, *a, tries: int = 4, pause: float = 5.0):
    """После PATCH панель несколько секунд рвёт соединения — повторяем."""
    last = None
    for i in range(tries):
        try:
            return fn(*a)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(pause * (i + 1))
    raise last  # type: ignore[misc]


def _secrets(reg: dw.Registry | None = None) -> list[str]:
    """Что вычистить из текста ошибок: токен панели и логины доноров."""
    out: list[str] = []
    try:
        from app.services.remnawave_api import settings

        out.append(str(settings.remnawave_api_token or "").strip())
    except Exception:  # noqa: BLE001
        pass
    if reg is not None:
        for lst in reg.creds.values():
            for c in lst:
                out += [c.user, c.password]
    return out


def _scrub_err(exc: BaseException, reg: dw.Registry | None = None) -> str:
    return dp.scrub(getattr(exc, "message", None) or str(exc), _secrets(reg))[:160]


async def _agent_nodes_by_ip(db) -> dict:
    """Ноды рагента по IP. DNS — в потоке, чтобы не стопорить цикл событий."""
    from app.models import Node

    rows = (await db.execute(select(Node).where(Node.monitoring_enabled.is_(True)))).scalars().all()
    hosts = [n.host for n in rows]
    ips = await asyncio.to_thread(lambda: [_resolve(h) for h in hosts])
    by_ip: dict = {}
    for n, ip in zip(rows, ips):
        if ip:
            by_ip.setdefault(ip, n)
    return by_ip


async def _disabled_node_addresses(db) -> set[str]:
    from app.models import Node
    hosts = list((await db.execute(select(Node.host).where(Node.monitoring_enabled.is_(False)))).scalars().all())
    resolved = await asyncio.to_thread(lambda: [_resolve(host) for host in hosts])
    return set(hosts) | {ip for ip in resolved if ip}


# ── прыжки ──────────────────────────────────────────────────────────────────

async def _hops_for(db, reg: dw.Registry, st: dict, cfg: dict, key: str,
                    by_ip: dict | None = None) -> list[dp.Hop]:
    """Ноды, через которые щупать донора: probe_hops → доказанные → из allowlist → пользователи донора.

    probe_hops_only — только явный список; probe_hops_exclude — никогда (нода владельца и т.п.).
    """
    from app.services.crypto import decrypt_secret

    d = reg.donors[key]
    if by_ip is None:
        by_ip = await _agent_nodes_by_ip(db)
    dst = (st.get("donors") or {}).get(key) or {}
    fw = dst.get("allowlist") or {}
    exclude = set(cfg.get("probe_hops_exclude") or [])
    names: list[str] = list(cfg["probe_hops"].get(d.name) or [])
    if not cfg.get("probe_hops_only"):
        names += list(dst.get("good_hops") or [])
        users = [n for b in reg.bindings if b.donor_key == key for n in b.node_names]
        if fw.get("ips") is not None and not fw.get("open"):
            allowed = set(fw["ips"])
            names += [n for n in users if reg.node_ip.get(n) and dw.ip_allowed(reg.node_ip[n], allowed)]
            names += sorted(n for n, ip in reg.node_ip.items() if dw.ip_allowed(ip, allowed))
        names += users
    out: list[dp.Hop] = []
    seen: set[str] = set()
    for nm in names:
        ip = reg.node_ip.get(nm, "")
        node = by_ip.get(ip)     # только по адресу: имена в рагенте и панели расходятся (fi-1/fi-AI)
        if node is None or node.name in seen or ip == d.address or (d.node and nm == d.node):
            continue
        if nm in exclude or node.name in exclude:
            continue
        seen.add(node.name)
        try:
            out.append(dp.Hop(name=nm, host=node.host, port=node.ssh_port, user=node.ssh_user,
                              auth_type=node.auth_type,
                              password=decrypt_secret(node.password_enc) if node.password_enc else None,
                              private_key=decrypt_secret(node.private_key_enc) if node.private_key_enc else None))
        except Exception:  # noqa: BLE001 — ключ шифрования сменился и т.п.
            continue
        if len(out) >= 3:
            break
    return out


def _proven(st: dict, key: str, hop: dp.Hop | None, reg: dw.Registry) -> bool:
    if hop is None:
        return True
    dst = (st.get("donors") or {}).get(key) or {}
    if hop.name in (dst.get("good_hops") or []):
        return True
    fw = dst.get("allowlist") or {}
    ip = reg.node_ip.get(hop.name)
    if ip and fw.get("ips") is not None and dw.ip_allowed(ip, set(fw["ips"])):
        return True
    return False


async def _probe_donor(reg, st, cfg, key, services, hops, gemini_key) -> tuple[dict, dp.Hop | None]:
    """Один проход по донору с переходом на второй прыжок, если дело в прыжке/пути."""
    d = reg.donors[key]
    creds = reg.creds[key][0]
    path = [None] if cfg["transport"] == "direct" else hops[:2]
    if not path:
        return {s: (dp.RAW_NO_HOP, "нет ноды-прыжка с SSH в рагенте") for s in services}, None
    res: dict = {}
    used = None
    unreachable_from: list[dp.Hop | None] = []
    for hop in path:
        res = await asyncio.to_thread(
            dp.probe, hop, d.address, d.port, creds, services, timeout=float(cfg["req_timeout_s"]),
            gemini_key=gemini_key if "gemini" in services else "",
            gap=(float(cfg["gap_min_s"]), float(cfg["gap_max_s"])))
        used = hop
        raws = {r for r, _ in res.values()}
        if raws <= {dp.RAW_HOP_FAILED}:
            continue
        if raws <= {dp.RAW_UNREACHABLE}:
            unreachable_from.append(hop)
            continue
        return res, used
    if res and {r for r, _ in res.values()} <= {dp.RAW_UNREACHABLE}:
        # «не достучаться» засчитываем, только если с двух прыжков или с доказанного
        if len(unreachable_from) >= 2 or any(_proven(st, key, h, reg) for h in unreachable_from):
            return res, used
        return {s: (dp.RAW_ERROR, "не достучаться с непроверенного прыжка") for s in services}, used
    return res, used


async def _guarded_probe(db, reg, st, cfg, key, services, hops, gemini_key):
    from app.services.monitoring_gate import monitoring_lock
    async with monitoring_lock:
        disabled = await _disabled_node_addresses(db)
        if reg.donors[key].address in disabled:
            return None, None
        hops = [hop for hop in hops if hop is not None and hop.host not in disabled]
        return await _probe_donor(reg, st, cfg, key, services, hops, gemini_key)


# ── фаервол доноров ─────────────────────────────────────────────────────────

async def _check_allowlists(db, reg: dw.Registry, st: dict, cfg: dict, now: datetime) -> None:
    from app.services.crypto import decrypt_secret
    from app.services.monitoring_gate import monitoring_lock

    by_ip = await _agent_nodes_by_ip(db)
    disabled = await _disabled_node_addresses(db)
    every = timedelta(hours=cfg["allowlist_every_h"])
    for key, d in reg.donors.items():
        if d.address in disabled:
            continue
        if d.retired or not (d.used_by or d.in_registry):
            continue
        dst = st["donors"].setdefault(key, {"svc": {}})
        fw = dst.get("allowlist") or {}
        last = _parse(fw.get("checked_at"))
        if last and now - last < every:
            continue
        node = by_ip.get(d.address)
        if node is None:
            dst["allowlist"] = {"checked_at": _iso(now), "ips": None, "open": False,
                                "error": "донора нет среди нод рагента — фаервол не посмотреть"}
            continue
        try:
            hop = dp.Hop(name=node.name, host=node.host, port=node.ssh_port, user=node.ssh_user,
                         auth_type=node.auth_type,
                         password=decrypt_secret(node.password_enc) if node.password_enc else None,
                         private_key=decrypt_secret(node.private_key_enc) if node.private_key_enc else None)
            async with monitoring_lock:
                if d.address in await _disabled_node_addresses(db):
                    continue
                text = await asyncio.to_thread(dp.read_firewall, hop)
            ips, open_all = dw.parse_firewall(text, d.port)
            dst["allowlist"] = {"checked_at": _iso(now), "ips": sorted(ips) if ips is not None else None,
                                "open": open_all,
                                "error": None if ips is not None else "правил на порт не найдено"}
        except Exception as exc:  # noqa: BLE001
            dst["allowlist"] = {"checked_at": _iso(now), "ips": fw.get("ips"), "open": fw.get("open", False),
                                "error": f"SSH: {type(exc).__name__}"}


# ── применение ──────────────────────────────────────────────────────────────

def _node_snapshot(profile_uuid: str) -> dict[str, dict]:
    out = {}
    for n in _panel_nodes():
        if ((n.get("configProfile") or {}).get("activeConfigProfileUuid")) == profile_uuid:
            out[n["uuid"]] = {"name": n.get("name"), "address": n.get("address"), "connected": n.get("isConnected") is True,
                              "disabled": n.get("isDisabled") is True,
                              "uptime": n.get("xrayUptime") if isinstance(n.get("xrayUptime"), (int, float)) else None}
    return out


def _post_check(profile_uuid: str, before: dict[str, dict], deadline_s: int,
                sleep=time.sleep, restart=None, monitored=None) -> tuple[bool, str]:
    """Ноды, что были в сети до PATCH, снова в сети и с перезапущенным ядром.

    Панель не всегда перезапускает ноду после PATCH профиля (DE, 07.09.2026):
    если через минуту ядро старое — один раз просим перезапуск.
    """
    from app.services.remnawave_api import rw_api

    t0 = time.monotonic()
    need = {u for u, n in before.items() if n["connected"] and not n["disabled"]}
    if not need:
        return True, "нод в сети не было — проверять нечего"
    kicked: set[str] = set()
    skipped: set[str] = set()
    last = {}
    while time.monotonic() - t0 < deadline_s:
        sleep(15)
        if monitored is not None:
            need = {u for u in need if monitored(before[u].get("address"))}
            if not need:
                return True, "мониторинг нод профиля отключён"
        try:
            last = _node_snapshot(profile_uuid)
        except Exception:  # noqa: BLE001
            continue
        elapsed = time.monotonic() - t0
        ok = set(skipped)
        for u in need:
            n = last.get(u)
            if not n or not n["connected"]:
                continue
            up, up0 = n["uptime"], before[u]["uptime"]
            restarted = up is None or up0 is None or up < up0 or up <= elapsed + 30
            if restarted:
                ok.add(u)
            elif elapsed > 60 and u not in kicked:
                kicked.add(u)
                try:
                    if restart is None:
                        rw_api(f"/api/nodes/{u}/actions/restart", "POST", {"forceRestart": True})
                    elif restart(u, before[u].get("address")) is False:
                        skipped.add(u)
                        ok.add(u)
                except Exception:  # noqa: BLE001
                    pass
        if need <= ok:
            extra = f"; ядро перезапущено вручную на {len(kicked)}" if kicked else ""
            return True, f"ноды профиля в сети ({len(need)}){extra}"
    bad = [before[u]["name"] for u in need if not (last.get(u) or {}).get("connected")]
    stale = [before[u]["name"] for u in need if u not in bad]
    return False, ("не вернулись в сеть: " + ", ".join(bad) if bad else
                   "ядро не перезапустилось: " + ", ".join(stale))


async def _apply_profile(db, reg: dw.Registry, st: dict, cfg: dict, acts: list[dw.Action],
                         now: datetime, rows: list[dict]) -> bool:
    """Один профиль: свежий профиль → сравнение-и-замена → бэкап → pending → PATCH → сверка → ноды."""
    from app.services.remnawave_api import rw_api

    puid = acts[0].profile_uuid
    try:
        prof = await asyncio.to_thread(_panel_profile, puid)
    except Exception as exc:  # noqa: BLE001
        rows.append(_row(now, "error", f"профиль {puid[:8]} не прочитан: {_scrub_err(exc, reg)}", cfg))
        return False
    changes = []
    for a in acts:
        dd = reg.donors[a.dst]
        changes.append((a.tag, reg.donors[a.src].address, reg.donors[a.src].port, dd.address, dd.port,
                        reg.creds[a.dst][0]))
    try:
        new_cfg = dw.apply_changes(prof.get("config") or {}, changes)
    except dw.Conflict as exc:
        rows.append(_row(now, "conflict", f"{prof.get('name')}: {exc} — не трогаю", cfg))
        return True          # не сбой автоматики: просто профиль уже другой
    try:
        before = await asyncio.to_thread(_node_snapshot, puid)
    except Exception as exc:  # noqa: BLE001 — без снимка нод не проверить итог: PATCH не делаем
        rows.append(_row(now, "error", f"{prof.get('name')}: ноды профиля не прочитаны "
                                       f"({_scrub_err(exc, reg)}) — PATCH не делаю", cfg))
        return False
    backup_id = await dw._backup(db, prof, "; ".join(a.reason for a in acts))
    st["pending"] = {"id": uuidlib.uuid4().hex[:8], "at": _iso(now), "profile_uuid": puid,
                     "profile_name": prof.get("name"), "backup": backup_id,
                     "changes": [{"binding": a.binding, "tag": a.tag, "src": a.src, "dst": a.dst} for a in acts]}
    st["patches"].append({"at": _iso(now), "profile": puid})
    await dw.save_state(db, st)
    await db.commit()
    text = "; ".join(f"{prof.get('name')}/{a.tag}: {reg.donors[a.src].name} → {reg.donors[a.dst].name}" for a in acts)
    await dw.notify(db, cfg, f"🔄 переключаю {text} (бэкап {backup_id})")
    try:
        await asyncio.to_thread(rw_api, "/api/config-profiles", "PATCH",
                                {"uuid": puid, "name": prof.get("name"), "config": new_cfg})
    except Exception as exc:  # noqa: BLE001 — ответа нет: мог и примениться, сверим ниже
        rows.append(_row(now, "patch_error", f"{prof.get('name')}: PATCH: {_scrub_err(exc, reg)}", cfg))
    ok, msg = await _finish_pending(db, st, cfg, reg, before, rows)
    if st.get("pending"):
        rows.append(_row(now, "error", f"{msg} — сверка в следующий заход", cfg))
    return ok


async def _finish_pending(db, st: dict, cfg: dict, reg: dw.Registry | None, before: dict | None,
                          rows: list[dict]) -> tuple[bool, str]:
    """Сверить, что записалось, и что ноды профиля живы. Зовётся и после рестарта рагента."""
    now = _now()
    pend = st.get("pending") or {}
    puid = pend.get("profile_uuid")
    try:
        prof = await asyncio.to_thread(_retry, _panel_profile, puid)
    except Exception as exc:  # noqa: BLE001
        return False, f"панель не отдаёт профиль после PATCH: {_scrub_err(exc, reg)}"
    conf = prof.get("config") or {}
    applied, untouched, other = [], [], []
    for ch in pend.get("changes") or []:
        tgt = dw.outbound_target(conf, ch["tag"])
        (applied if tgt == ch["dst"] else untouched if tgt == ch["src"] else other).append(ch)
    name = pend.get("profile_name") or puid[:8]
    if not applied:
        st["pending"] = None
        msg = f"{name}: изменения не записались (адреса прежние)" if not other else \
            f"{name}: адреса поменял кто-то другой — автоматика остановлена"
        rows.append(_row(now, "patch_failed", msg, cfg))
        st["halted"] = {"at": _iso(now), "reason": msg}
        await dw.notify(db, cfg, "⛔ " + msg + " — автоматика остановлена до ручного снятия")
        await dw.save_state(db, st)
        await db.commit()
        return False, msg
    for ch in applied:
        b = st["bindings"].setdefault(ch["binding"], {"home": ch["src"]})
        b["current"] = ch["dst"]
        b["switched_at"] = _iso(now)
        st["switches"].append({"at": _iso(now), "binding": ch["binding"], "src": ch["src"], "dst": ch["dst"]})
    if before is None:
        # после перезапуска рагента снимка «до» нет: требуем только «в сети»
        try:
            before = await asyncio.to_thread(_node_snapshot, puid)
        except Exception:  # noqa: BLE001
            before = {}
        for n in before.values():
            n["uptime"] = None
            n["connected"] = not n["disabled"]   # строго: все включённые ноды профиля должны быть в сети
    disabled = await _disabled_node_addresses(db)
    before = {node_id: item for node_id, item in before.items() if item.get("address") not in disabled}
    loop = asyncio.get_running_loop()

    async def is_monitored(address):
        from app.db import SessionLocal
        async with SessionLocal() as check_db:
            return address not in await _disabled_node_addresses(check_db)

    async def guarded_restart(node_uuid, address, deadline):
        from app.db import SessionLocal
        from app.services.monitoring_gate import monitoring_lock
        from app.services.remnawave_api import rw_api
        async with monitoring_lock:
            if time.monotonic() >= deadline:
                return None
            async with SessionLocal() as check_db:
                if address in await _disabled_node_addresses(check_db):
                    return False
            if time.monotonic() >= deadline:
                return None
            await asyncio.to_thread(rw_api, f"/api/nodes/{node_uuid}/actions/restart", "POST", {"forceRestart": True})
            return True

    deadline = time.monotonic() + cfg["post_check_s"]

    def restart(node_uuid, address):
        return asyncio.run_coroutine_threadsafe(guarded_restart(node_uuid, address, deadline), loop).result()

    def monitored(address):
        return asyncio.run_coroutine_threadsafe(is_monitored(address), loop).result()

    ok, msg = await asyncio.to_thread(_post_check, puid, before, cfg["post_check_s"], restart=restart, monitored=monitored)
    st["pending"] = None
    partial = f"; не записались: {', '.join(c['tag'] for c in untouched + other)}" if (untouched or other) else ""
    if ok and not partial:
        rows.append(_row(now, "switched", f"{name}: переключено, {msg}", cfg))
        await dw.notify(db, cfg, f"✅ {name}: переключено, {msg}")
    else:
        reason = f"{name}: {msg}{partial}"
        st["halted"] = {"at": _iso(now), "reason": reason}
        rows.append(_row(now, "post_check_failed", reason, cfg))
        await dw.notify(db, cfg, f"⛔ {reason}. Бэкап {pend.get('backup')}. Автоматика остановлена до ручного снятия")
    await dw.save_state(db, st)
    await db.commit()
    return ok and not partial, msg


# ── цикл ────────────────────────────────────────────────────────────────────

async def run_cycle(db) -> dict:
    """Один цикл. Возвращает краткую сводку (для журнала и API)."""
    from app.services import ai_access

    t0 = time.monotonic()
    now = _now()
    cycle_at = _iso(now)
    cfg = await dw.get_config(db)
    st = await dw.get_state(db)
    rows: list[dict] = []
    summary = {"at": cycle_at, "probes": 0, "rechecks": 0, "actions": []}

    # Старт пишем до любых проб: упадёт цикл где угодно — следующий всё равно через полный интервал
    st["cycle_started_at"] = cycle_at
    await dw.save_state(db, st)
    await db.commit()

    # 0) рагент упал посреди переключения — довести сверку; не вышло — без решений
    if st.get("pending"):
        await _resume(db, st, cfg, rows)
        if st.get("pending"):
            st["last_cycle"] = {**summary, "error": "незавершённое переключение не сверено — жду панель"}
            await dw.save_state(db, st)
            await dw.append_log(db, rows)
            await db.commit()
            return st["last_cycle"]

    # 1) панель
    try:
        nodes, profiles = await asyncio.to_thread(_panel_read)
    except Exception as exc:  # noqa: BLE001
        err = _scrub_err(exc)
        pd = st.setdefault("panel", {})
        pd.setdefault("down_since", cycle_at)
        pd["fails"] = int(pd.get("fails", 0)) + 1
        if pd["fails"] >= 2:
            await dw.alert(db, st, cfg, "panel", f"панель не отдаёт ноды/профили ({err}) — "
                                                 "доноры не проверяются, переключений нет", now, rows)
        st["last_cycle"] = {**summary, "error": f"панель: {err}"}
        await dw.save_state(db, st)
        await dw.append_log(db, rows)
        await db.commit()
        return st["last_cycle"]
    st["panel"] = {}
    await dw.alert(db, st, cfg, "panel", None, now, rows)

    reg = await asyncio.to_thread(dw.discover, profiles, nodes, cfg, _resolve)   # внутри DNS
    st["registry"] = dw.registry_snapshot(reg)
    for note in dw.sync_bindings(reg, st, now):
        rows.append(_row(now, "external", note, cfg))
    for iss in reg.issues:
        if iss["code"] != "no_services":
            await dw.alert(db, st, cfg, f"issue:{iss['code']}:{iss['key']}", iss["text"], now, rows)
    live_issue_keys = {f"issue:{i['code']}:{i['key']}" for i in reg.issues}
    for k in [k for k in st["alerts"] if k.startswith("issue:") and k not in live_issue_keys]:
        await dw.alert(db, st, cfg, k, None, now, rows)

    # 2) фаервол доноров (раз в несколько часов, только чтение)
    try:
        await _check_allowlists(db, reg, st, cfg, now)
    except Exception:  # noqa: BLE001
        log.exception("donorwatch: allowlist")
    gaps = dw.allowlist_gaps(reg, st)
    for key, d in reg.donors.items():
        txt = (f"{d.name} ({d.address}): в allowlist порта {d.port} нет нод, которые им пользуются: "
               + ", ".join(gaps[key])) if key in gaps else None
        await dw.alert(db, st, cfg, f"allowlist:{key}", txt, now, rows)
    await dw.save_state(db, st)
    await db.commit()

    # 3) пробы: доноры вразнобой, паузы со случайным разбросом
    plan = dw.probe_plan(reg, cfg)
    disabled = await _disabled_node_addresses(db)
    plan = {key: services for key, services in plan.items() if reg.donors[key].address not in disabled}
    gemini_key = await ai_access.gemini_key(db) if any("gemini" in v for v in plan.values()) else ""
    by_ip = await _agent_nodes_by_ip(db) if plan else {}
    hops = {k: await _hops_for(db, reg, st, cfg, k, by_ip) for k in plan}
    await db.commit()          # не держим транзакцию открытой, пока ходим по сети
    keys = list(plan)
    random.shuffle(keys)
    first: dict[str, dict] = {}
    used_hop: dict[str, dp.Hop | None] = {}
    fail_at: dict[str, float] = {}
    slot = cfg["spread_s"] / max(1, len(keys))
    for i, key in enumerate(keys):
        if i:
            await asyncio.sleep(slot * random.uniform(0.5, 1.5))
        res, hop = await _guarded_probe(db, reg, st, cfg, key, plan[key], hops[key], gemini_key)
        if res is None:
            continue
        first[key], used_hop[key] = res, hop
        summary["probes"] += len(res)
        if any(r in dp.FAIL_RAW for r, _ in res.values()):
            fail_at[key] = time.monotonic()
    # перепроверка провалов — один раз, после паузы
    second: dict[str, dict] = {}
    for key in fail_at:
        wait = cfg["recheck_delay_s"] + random.uniform(0, 30) - (time.monotonic() - fail_at[key])
        if wait > 0:
            await asyncio.sleep(wait)
        svcs = [s for s, (r, _) in first[key].items() if r in dp.FAIL_RAW]
        hop_list = [used_hop[key]] + [h for h in hops[key] if h is not used_hop[key]] if used_hop[key] else hops[key]
        res, _hop = await _guarded_probe(db, reg, st, cfg, key, svcs, hop_list, gemini_key)
        if res is None:
            continue
        second[key] = res
        summary["rechecks"] += len(res)

    results: dict[str, dict] = {}
    for key, per in first.items():
        results[key] = {}
        for svc, raw in per.items():
            re_ = second.get(key, {}).get(svc)
            h = dw.health_of(raw, re_)
            results[key][svc] = {"health": h, "raw": (re_ or raw)[0], "note": (re_ or raw)[1],
                                 "flapped": re_ is not None and h == dw.ALIVE}
    dw.update_streaks(st, results, now)
    for key, per in results.items():
        hop = used_hop.get(key)
        if hop and any(r["health"] == dw.ALIVE for r in per.values()):
            good = st["donors"][key].setdefault("good_hops", [])
            if hop.name not in good:
                good.insert(0, hop.name)
                del good[5:]
    for key, d in reg.donors.items():
        per = results.get(key) or {}
        dead = [s for s, r in per.items() if r["health"] == dw.DEAD]
        txt = None
        if dead:
            notes = "; ".join(f"{dw.SERVICE_NAMES[s]}: {per[s]['raw']} {per[s]['note']}".strip() for s in dead)
            txt = f"донор {d.name} ({d.address}) не работает — {notes}"
        await dw.alert(db, st, cfg, f"donor:{key}", txt, now, rows)

    # 4) решения
    acts = dw.decide(reg, st, cfg, now, cycle_at)
    cfg = await dw.get_config(db)          # режим могли переключить, пока шли пробы
    by_profile: dict[str, list[dw.Action]] = {}
    plans: list[str] = []
    for a in acts:
        summary["actions"].append({"kind": a.kind, "binding": a.binding, "reason": a.reason})
        if a.kind in ("switch", "switch_back"):
            if cfg["dry_run"]:
                plans.append(a.reason)
            else:
                by_profile.setdefault(a.profile_uuid, []).append(a)
        else:
            await dw.alert(db, st, cfg, f"act:{a.binding}", ("⛔ " if a.kind != "hold" else "⏳ ") + a.reason,
                           now, rows)
    # сухой прогон: одно сводное «сделал бы» на цикл, а не по сообщению на привязку
    for k in [k for k in st["alerts"] if k.startswith("plan:")]:
        st["alerts"].pop(k, None)          # ключи прежнего формата — снимаем молча
    plan_txt = ("сделал бы (" + str(len(plans)) + "): " + "; ".join(plans))[:3500] if plans else None
    await dw.alert(db, st, cfg, "plan", plan_txt, now, rows)
    acted = {a.binding for a in acts}
    for k in [k for k in st["alerts"] if k.startswith("act:") and k.split(":", 1)[1] not in acted]:
        await dw.alert(db, st, cfg, k, None, now, rows)

    # 5) боевой режим: по одному профилю (меньше людей в сети — раньше), стоп на первом сбое,
    #    не дольше cycle_budget_s на цикл — остаток в следующем цикле
    order = sorted(by_profile, key=lambda p: dw.profile_rank(reg, p))
    deferred: list[str] = []
    for i, puid in enumerate(order):
        if st.get("halted") or st.get("pending"):
            break
        if time.monotonic() - t0 + cfg["post_check_s"] + 60 > cfg["cycle_budget_s"]:
            deferred = order[i:]
            break
        ok = await _apply_profile(db, reg, st, cfg, by_profile[puid], now, rows)
        if not ok:
            left = [a.binding for p in order[i + 1:] for a in by_profile[p]]
            if left:
                rows.append(_row(now, "skipped", "не сделано из-за сбоя: " + ", ".join(left), cfg))
            break
    dnames = [next((b.profile_name for b in reg.bindings if b.profile_uuid == p), p[:8]) for p in deferred]
    summary["deferred"] = dnames
    await dw.alert(db, st, cfg, "deferred",
                   (f"цикл не уложился в {cfg['cycle_budget_s']} с — профили перенесены на следующий цикл: "
                    + ", ".join(dnames)) if dnames else None, now, rows)

    st["last_cycle"] = summary
    await dw.save_state(db, st)
    await dw.append_log(db, rows)
    await db.commit()
    return summary


async def _resume(db, st: dict, cfg: dict, rows: list[dict]) -> None:
    rows.append(_row(_now(), "resume", f"незавершённое переключение {st['pending'].get('profile_name')} — "
                                       "сверяю после перезапуска рагента", cfg))
    await _finish_pending(db, st, cfg, None, None, rows)


async def resume_pending(db) -> None:
    """Только сверка незавершённого переключения — без проб и решений."""
    cfg = await dw.get_config(db)
    st = await dw.get_state(db)
    if not st.get("pending"):
        return
    rows: list[dict] = []
    await _resume(db, st, cfg, rows)
    await dw.save_state(db, st)
    await dw.append_log(db, rows)
    await db.commit()


_fail_until = 0.0          # monotonic: после падения цикла — пауза на полный интервал


async def _tick(db) -> float:
    """Один заход фонового цикла. Возвращает паузу до следующего, с.

    Пробы — не чаще interval_min, даже если цикл падает: старт цикла пишется в
    состояние до проб (cycle_started_at), а падение ставит паузу в памяти.
    """
    global force_next, _fail_until
    force_next = False         # /check только будит; интервал всё равно проверяется здесь
    cfg = await dw.get_config(db)
    if not cfg["enabled"]:
        return 300.0
    st = await dw.get_state(db)
    interval_s = cfg["interval_min"] * 60
    hold = _fail_until - time.monotonic()
    if hold > 0:
        return max(30.0, hold)
    last = dw.last_cycle_at(st)
    left = 0.0 if last is None else interval_s - (_now() - last).total_seconds()
    if left > 0:
        if st.get("pending"):
            try:
                async with dw._lock:
                    await resume_pending(db)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("donorwatch: сверка pending упала")
            return min(max(30.0, left), 300.0)
        return max(30.0, left + random.uniform(0, 60))
    try:
        async with dw._lock:
            out = await run_cycle(db)
        log.info("donorwatch: цикл: проб %s, перепроверок %s, действий %s",
                 out.get("probes"), out.get("rechecks"), len(out.get("actions") or []))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _fail_until = time.monotonic() + interval_s
        log.exception("donorwatch: цикл упал — следующий через полный интервал")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
    return interval_s + random.uniform(0, 60)


async def run_donor_watch() -> None:
    """Фон: раз в interval_min (не чаще 20 минут), со случайным сдвигом старта."""
    from app.db import SessionLocal

    await asyncio.sleep(120 + random.uniform(0, 120))
    while True:
        delay = 60.0
        try:
            async with SessionLocal() as db:
                delay = await _tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("donorwatch: заход упал")
        run_now.clear()
        try:
            await asyncio.wait_for(run_now.wait(), timeout=max(30.0, delay))
        except asyncio.TimeoutError:
            pass


# ── откат из бэкапа (ручка API, только браузерная сессия) ──────────────────

async def restore_backup(db, backup_id: str, dry_run: bool = True) -> dict:
    """Вернуть профиль из бэкапа сторожа. dry_run — только показать, что поменяется.

    Перед восстановлением снимается бэкап текущего профиля. После PATCH профиль
    перечитывается и сверяются адреса аутбаундов. Остановку (halted) не трогает.
    LookupError — нет бэкапа; RuntimeError — есть незавершённое переключение.
    """
    from app.services.remnawave_api import rw_api

    b = await dw.load_backup(db, backup_id)
    if b is None:
        raise LookupError("бэкап не найден")
    old = b["profile"]
    puid = str(old.get("uuid") or "")
    old_conf = old.get("config") or {}
    cur = await asyncio.to_thread(_panel_profile, puid)
    cur_conf = cur.get("config") or {}
    out = {"backup": b["meta"], "profile_name": cur.get("name"), "dry_run": dry_run,
           "identical": cur_conf == old_conf, "outbounds": dw.outbound_diff(cur_conf, old_conf),
           "applied": False}
    if dry_run or out["identical"]:
        return out
    st = await dw.get_state(db)
    if st.get("pending"):
        raise RuntimeError("есть незавершённое переключение — сначала его сверка")
    cfg = await dw.get_config(db)
    now = _now()
    pre_id = await dw._backup(db, cur, f"перед восстановлением из {backup_id}")
    out["pre_restore_backup"] = pre_id
    err = None
    try:
        await asyncio.to_thread(rw_api, "/api/config-profiles", "PATCH",
                                {"uuid": puid, "name": cur.get("name"), "config": old_conf})
    except Exception as exc:  # noqa: BLE001 — ответа нет: мог и примениться, сверим ниже
        err = _scrub_err(exc)
    after = await asyncio.to_thread(_retry, _panel_profile, puid)
    left = dw.outbound_diff(after.get("config") or {}, old_conf)
    out["applied"] = not left
    out["verified_outbounds"] = not left
    if err:
        out["patch_error"] = err
    name = cur.get("name") or puid[:8]
    if not left:
        # адреса теперь как в бэкапе — это наша правка, не «чужая»: родной донор не сбрасываем
        for bid, bst in (st.get("bindings") or {}).items():
            if bid.startswith(puid + ":"):
                tgt = dw.outbound_target(old_conf, bid.split(":", 1)[1])
                if tgt:
                    bst["current"] = tgt
                    bst.pop("switched_at", None)
        st["patches"].append({"at": _iso(now), "profile": puid, "manual": True})
        text = f"{name}: восстановлен из бэкапа {backup_id} (до этого снят бэкап {pre_id})"
    else:
        text = (f"{name}: восстановление из {backup_id} не подтвердилось — расходятся "
                + ", ".join(d["tag"] for d in left) + f"; бэкап текущего {pre_id}")
    await dw.save_state(db, st)
    await dw.append_log(db, [_row(now, "restore" if not left else "restore_failed", text, cfg)])
    await db.commit()
    await dw.notify(db, cfg, ("↩️ " if not left else "⛔ ") + text)
    return out
