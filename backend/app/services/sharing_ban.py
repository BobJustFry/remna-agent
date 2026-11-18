"""Бан за шаринг: отключить в панели, разорвать соединения, записать в журнал.

Одно действие `ban` / `unban` — его зовут и кнопка в интерфейсе, и автобан
после скана. Лимит устройств пользователя не трогаем никогда: это платная услуга.

Вызовы панели Remnawave 3.4.4 (ключ пользователя — числовой `id`):
  GET   /api/users/{id}                  — карточка (статус, описание)
  POST  /api/users/{id}/actions/disable  — status = DISABLED
  POST  /api/users/{id}/actions/enable   — status = ACTIVE
  POST  /api/connections/drop            — сброс соединений на нодах,
        {"dropBy": {"by": "userIds", "userIds": [id]},
         "targetNodes": {"target": "allNodes"}}
  PATCH /api/users                       — {"id": id, "description": "...", "tag": "SHARER"}
        (tag по контракту: ^[A-Z0-9_]+$, до 16 символов, nullable)

Журнал действий лежит в app_settings (`sharing_ban_log`, последние 200 записей):
отдельной таблицы аудита у рагента нет, а services/audit.py — это проверки
парка, не журнал действий.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import sharing_report, sharing_score, sharing_watch, subwatch
from app.services.remnawave_api import RemnawaveApiError, rw_api
from app.services.telegram import send_message

log = logging.getLogger("remna.sharing")

LOG_KEY = "sharing_ban_log"
LOG_KEEP = 200
# Поле description у Remnawave не ограничено контрактом явно; держим запас.
DESCRIPTION_MAX = 1000
AUTO_BANS_PER_HOUR = 3

STATUS_DISABLED = "DISABLED"
SHARER_TAG = "SHARER"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M UTC")


# ── чистая логика ────────────────────────────────────────────────────────────

def append_note(description: str | None, note: str, limit: int = DESCRIPTION_MAX) -> str:
    """Дописать пометку к описанию, не затирая прежний текст.

    Не влезло — отрезаем начало старого текста, а не новую пометку: свежее
    важнее.
    """
    old = (description or "").strip()
    if not old:
        return note[:limit]
    joined = f"{old} | {note}"
    if len(joined) <= limit:
        return joined
    keep = limit - len(note) - 3
    if keep <= 0:
        return note[-limit:]
    return f"…{old[-(keep - 1):]} | {note}" if keep > 1 else note


def ban_note(now: datetime, score: int, auto: bool) -> str:
    return f"Забанен за шаринг {_stamp(now)}, оценка {int(score)}/100" + (" (авто)" if auto else "")


def prev_tag_note(tag: str) -> str:
    return f"прежний тег: {tag}"


def unban_note(now: datetime) -> str:
    return f"разбанен {_stamp(now)}"


def excluded_ids(cfg: dict) -> set[int]:
    out: set[int] = set()
    for v in cfg.get("ban_exclude_ids") or []:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


def recent_auto_bans(log_rows: list[dict], now: datetime,
                     window: timedelta = timedelta(hours=1)) -> int:
    n = 0
    for r in log_rows:
        if r.get("action") != "ban" or r.get("source") != "auto" or not r.get("ok"):
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        if now - ts < window:
            n += 1
    return n


def auto_ban_decision(cfg: dict, hit: dict, user_state: dict | None, panel_status: str | None,
                      recent: int) -> tuple[str, str]:
    """(решение, причина). Решение: "ban" | "limit" | "skip".

    "limit" — все условия выполнены, но лимит автобанов за час исчерпан:
    только уведомление.
    """
    if not cfg.get("auto_ban"):
        return "skip", "автобан выключен"
    uid = int(hit.get("user_id") or 0)
    if uid in excluded_ids(cfg):
        return "skip", "в списке исключений"
    if str(panel_status or "").upper() == STATUS_DISABLED:
        return "skip", "уже отключён"
    st = user_state or {}
    if not st.get("alerted"):
        return "skip", "тревога не поднята"
    if str(hit.get("level")) != sharing_score.RED:
        return "skip", "сейчас не красная зона"
    need_score = int(cfg.get("auto_ban_score") or 85)
    if int(hit.get("score") or 0) < need_score:
        return "skip", f"оценка ниже порога {need_score}"
    need_scans = max(1, int(cfg.get("auto_ban_scans") or 5))
    if int(st.get("red") or 0) < need_scans:
        return "skip", f"красных сканов подряд меньше {need_scans}"
    if recent >= AUTO_BANS_PER_HOUR:
        return "limit", f"лимит автобанов за час ({AUTO_BANS_PER_HOUR}) исчерпан"
    return "ban", "условия выполнены"


# ── панель (синхронно, звать из to_thread) ───────────────────────────────────

def _panel_ban(user_id: int, note: str) -> dict:
    res: dict = {"ok": False, "steps": [], "skipped": None}
    try:
        user = rw_api(f"/api/users/{int(user_id)}")
    except RemnawaveApiError as exc:
        res["error"] = f"карточка: {exc.message}"
        return res
    user = user if isinstance(user, dict) else {}
    res["username"] = str(user.get("username") or "")
    if str(user.get("status") or "").upper() == STATUS_DISABLED:
        res.update(ok=True, skipped="уже отключён")
        return res
    try:
        rw_api(f"/api/users/{int(user_id)}/actions/disable", "POST", {})
        res["steps"].append("disable")
    except RemnawaveApiError as exc:
        res["error"] = f"отключение: {exc.message}"
        return res
    res["ok"] = True            # пользователь отключён — главное сделано
    try:
        rw_api("/api/connections/drop", "POST", {
            "dropBy": {"by": "userIds", "userIds": [int(user_id)]},
            "targetNodes": {"target": "allNodes"},
        })
        res["steps"].append("drop")
    except RemnawaveApiError as exc:
        res["warn"] = f"соединения не сброшены: {exc.message}"
    try:
        desc = append_note(user.get("description"), note)
        old_tag = user.get("tag")
        if old_tag and str(old_tag) != SHARER_TAG:
            desc = append_note(desc, prev_tag_note(str(old_tag)))
        rw_api("/api/users", "PATCH", {"id": int(user_id), "description": desc,
                                       "tag": SHARER_TAG})
        res["steps"].append("description")
        res["steps"].append("tag")
    except RemnawaveApiError as exc:
        res["warn"] = (res.get("warn", "") + f" комментарий/тег не записан: {exc.message}").strip()
    return res


def _panel_unban(user_id: int, note: str) -> dict:
    res: dict = {"ok": False, "steps": [], "skipped": None}
    try:
        user = rw_api(f"/api/users/{int(user_id)}")
    except RemnawaveApiError as exc:
        res["error"] = f"карточка: {exc.message}"
        return res
    user = user if isinstance(user, dict) else {}
    res["username"] = str(user.get("username") or "")
    if str(user.get("status") or "").upper() != STATUS_DISABLED:
        res.update(ok=True, skipped=f"статус {user.get('status')}, не отключён")
        return res
    try:
        rw_api(f"/api/users/{int(user_id)}/actions/enable", "POST", {})
        res["steps"].append("enable")
    except RemnawaveApiError as exc:
        res["error"] = f"включение: {exc.message}"
        return res
    res["ok"] = True
    try:
        desc = append_note(user.get("description"), note)
        body: dict = {"id": int(user_id), "description": desc}
        tag = user.get("tag")
        if tag == SHARER_TAG:
            body["tag"] = None
        elif tag:
            res["tag_note"] = (f"тег «{tag}» оставлен как есть; "
                               "прежний тег до бана смотри в комментарии")
        rw_api("/api/users", "PATCH", body)
        res["steps"].append("description")
        if "tag" in body:
            res["steps"].append("untag")
    except RemnawaveApiError as exc:
        res["warn"] = f"комментарий/тег не записан: {exc.message}"
    return res


# ── журнал ───────────────────────────────────────────────────────────────────

async def get_log(db: AsyncSession) -> list[dict]:
    data = await subwatch._json_setting(db, LOG_KEY, {"rows": []})
    rows = data.get("rows")
    return rows if isinstance(rows, list) else []


async def _append_log(db: AsyncSession, row: dict) -> None:
    rows = await get_log(db)
    rows.append(row)
    await subwatch._save_json(db, LOG_KEY, {"rows": rows[-LOG_KEEP:]})


# ── сообщения ────────────────────────────────────────────────────────────────

def ban_text(hit: dict, auto: bool, result: dict) -> str:
    name = html.escape(str(hit.get("username") or result.get("username") or hit.get("user_id")))
    score = int(hit.get("score") or 0)
    if result.get("skipped"):
        return f"ℹ️ <b>{name}</b>: {html.escape(str(result['skipped']))}, ничего не менял."
    head = "⛔ <b>Отключён автоматически за шаринг: " if auto else "⛔ <b>Отключён за шаринг: "
    lines = [f"{head}{name}</b>", f"ID {hit.get('user_id')}, оценка {score} из 100."]
    for p in (hit.get("parts") or [])[:4]:
        lines.append("• " + html.escape(str(p.get("why") or "")))
    lines.append("Соединения на нодах сброшены." if "drop" in result.get("steps", [])
                 else "Соединения сбросить не удалось — оборвутся сами при отключении.")
    if result.get("warn"):
        lines.append("⚠️ " + html.escape(str(result["warn"])))
    lines.append("Вернуть: рагент → «Шаринг» → «Отключённые за шаринг» → «Вернуть». "
                 "Лимит устройств не менялся.")
    return "\n".join(lines)


async def _notify_text(db: AsyncSession, cfg: dict, text: str) -> None:
    if not cfg.get("notify"):
        return
    token = await subwatch.bot_token(db)
    chat = await sharing_watch.effective_chat(db, cfg)
    if not token or not chat:
        return
    err = await asyncio.to_thread(send_message, token, chat, text)
    if err:
        log.warning("sharing ban: уведомление не ушло: %s", err)


async def send_client_report(db: AsyncSession, cfg: dict, hit: dict, auto: bool,
                             client_text: str) -> bool:
    """Отправить владельцу клиентский текст отчёта обычным текстом (без разметки), несколькими
    сообщениями по порядку; первым — шапка для владельца. Бот не настроен — молча пропускаем;
    любой сбой только в журнал: бан уже выполнен."""
    try:
        if not cfg.get("notify"):
            return False
        token = await subwatch.bot_token(db)
        chat = await sharing_watch.effective_chat(db, cfg)
        if not token or not chat:
            return False
        parts = sharing_report.split_parts(client_text)
        if not parts:
            return False
        head = sharing_report.owner_header(
            str(hit.get("username") or ""), int(hit["user_id"]), int(hit.get("score") or 0),
            auto, len(parts))
        for msg in [head, *parts]:
            err = await asyncio.to_thread(send_message, token, chat, msg, None)
            if err:
                log.warning("sharing ban: отчёт не ушёл в Telegram user=%s: %s", hit.get("user_id"), err)
                return False
        return True
    except Exception:  # noqa: BLE001
        log.warning("sharing ban: сбой отправки отчёта user=%s", hit.get("user_id"), exc_info=True)
        return False


# ── действия ─────────────────────────────────────────────────────────────────

async def ban(db: AsyncSession, cfg: dict, hit: dict, source: str = "manual",
              now: datetime | None = None) -> dict:
    """Забанить пользователя. hit — строка подозреваемого (user_id, score, parts…).

    source: "manual" | "auto". Коммит — на вызывающем.
    """
    now = now or _now()
    uid = int(hit["user_id"])
    auto = source == "auto"
    note = ban_note(now, int(hit.get("score") or 0), auto)
    # Отчёт-доказательство собираем до отключения, пока адреса ещё на серверах.
    # Ошибка отчёта не должна срывать бан.
    report: sharing_report.BanReport | None = None
    try:
        async with db.begin_nested():   # сбой таблицы кеша не портит общую транзакцию
            report = await sharing_report.prepare_for_ban(db, uid, now)
    except Exception:  # noqa: BLE001
        log.warning("sharing ban: отчёт не собран user=%s", uid, exc_info=True)
    res = await asyncio.to_thread(_panel_ban, uid, note)
    if report and res.get("ok") and not res.get("skipped"):
        try:
            await sharing_report.store(db, uid, report.text, report.username, now,
                                       report.client)
        except Exception:  # noqa: BLE001
            log.warning("sharing ban: отчёт не сохранён user=%s", uid, exc_info=True)
    await _append_log(db, {
        "ts": now.isoformat(), "action": "ban", "source": source, "user_id": uid,
        "username": hit.get("username") or res.get("username") or "",
        "score": int(hit.get("score") or 0), "ok": bool(res.get("ok")),
        "skipped": res.get("skipped"), "steps": res.get("steps"),
        "error": res.get("error"), "warn": res.get("warn"),
    })
    log.warning("sharing ban: user=%s source=%s ok=%s steps=%s err=%s", uid, source,
                res.get("ok"), res.get("steps"), res.get("error"))
    if res.get("ok") and not res.get("skipped"):
        await _notify_text(db, cfg, ban_text(hit, auto, res))
        if report:
            await send_client_report(db, cfg, hit, auto, report.client)
    return res


async def unban(db: AsyncSession, cfg: dict, user_id: int, username: str = "",
                now: datetime | None = None) -> dict:
    now = now or _now()
    res = await asyncio.to_thread(_panel_unban, int(user_id), unban_note(now))
    name = username or res.get("username") or ""
    await _append_log(db, {
        "ts": now.isoformat(), "action": "unban", "source": "manual", "user_id": int(user_id),
        "username": name, "score": 0, "ok": bool(res.get("ok")),
        "skipped": res.get("skipped"), "steps": res.get("steps"),
        "error": res.get("error"), "warn": res.get("warn"),
        "tag_note": res.get("tag_note"),
    })
    if res.get("ok") and not res.get("skipped"):
        extra = ("\n" + html.escape(res["tag_note"])) if res.get("tag_note") else ""
        await _notify_text(db, cfg, f"✅ <b>Возвращён после бана за шаринг: "
                                    f"{html.escape(str(name or user_id))}</b> (ID {user_id})."
                                    f"{extra}")
    return res


def banned_now(rows: list[dict]) -> list[dict]:
    """Кто сейчас числится отключённым нами: последнее действие по человеку — бан."""
    last: dict[int, dict] = {}
    for r in rows:
        if not r.get("ok") or r.get("skipped"):
            continue
        try:
            last[int(r["user_id"])] = r
        except (KeyError, TypeError, ValueError):
            continue
    return [r for r in last.values() if r.get("action") == "ban"]


async def process_auto_bans(db: AsyncSession, cfg: dict, suspects: list[dict],
                            now: datetime | None = None) -> list[tuple[str, dict]]:
    """После handle_scan: банит тех, кто прошёл все условия. [(решение, hit)]."""
    if not cfg.get("auto_ban") or not suspects:
        return []
    now = now or _now()
    state = await sharing_watch.get_state(db)
    users: dict = dict(state.get("users") or {})
    rows = await get_log(db)
    recent = recent_auto_bans(rows, now)
    from app.services import sharing_detect     # локально: модуль тяжёлый
    cache = sharing_detect.users_cache().get("by_id") or {}
    done: list[tuple[str, dict]] = []
    changed = False
    for hit in suspects:
        uid = int(hit["user_id"])
        st = users.get(str(uid))
        status = (cache.get(uid) or {}).get("status")
        retry = (st or {}).get("ban_retry_after")
        if retry and str(retry) > now.isoformat():
            continue                    # прошлая попытка не удалась — не долбим панель
        decision, why = auto_ban_decision(cfg, hit, st, status, recent)
        if decision == "limit":
            if st is not None and not st.get("ban_limit_notified"):
                st["ban_limit_notified"] = True
                changed = True
                name = html.escape(str(hit.get("username") or uid))
                await _notify_text(db, cfg, (
                    f"⏸ <b>Автобан не сработал: {name}</b>\n"
                    f"Условия выполнены (оценка {hit.get('score')}), но лимит "
                    f"{AUTO_BANS_PER_HOUR} автобана в час исчерпан. Решение за владельцем: "
                    f"рагент → «Шаринг» → «Отключить и разорвать»."))
            done.append(("limit", hit))
            continue
        if decision != "ban":
            continue
        res = await ban(db, cfg, hit, "auto", now)
        done.append(("ban" if res.get("ok") else "error", hit))
        if not res.get("ok") and st is not None:
            st["ban_retry_after"] = (now + timedelta(minutes=30)).isoformat()
            changed = True
        if res.get("ok"):
            recent += 1
            users.pop(str(uid), None)   # отключён: «шаринг снят» про него врать не надо
            if uid in cache:
                cache[uid]["status"] = STATUS_DISABLED
            changed = True
    if changed:
        state["users"] = users
        await sharing_watch.save_state(db, state)
    return done
