"""Telegram bot commands for the fleet report: /status /report /nodes (getUpdates long polling).

Only messages from the configured chat are handled; everything else is dropped
silently (the offset still advances). Polling runs through the same proxy as
sending (api.telegram.org is not reachable directly from the ragent VPS).
A webhook that is already set is never deleted: it belongs to someone else, so
polling just stays off and the state says why.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import fleet_report as fr
from app.services import subwatch
from app.services.telegram import TelegramError, get_updates, get_webhook_url

log = logging.getLogger("remna.fleetbot")

COMMANDS = ("status", "report", "nodes", "help", "start")
_CMD_RE = re.compile(r"^/([A-Za-z_]+)(?:@\w+)?(?:\s|$)")
WEBHOOK_RECHECK_S = 600


def extract_command(update: dict, chat_id: str) -> tuple[str, str] | None:
    """(command, chat id) for a command from the allowed chat, else None (silently)."""
    allowed = str(chat_id or "").strip()
    if not allowed:
        return None
    msg = update.get("message") or update.get("channel_post")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id")) != allowed:
        return None
    m = _CMD_RE.match(str(msg.get("text") or "").strip())
    if not m:
        return None
    cmd = m.group(1).lower()
    return (cmd, allowed) if cmd in COMMANDS else None


HELP = ("<b>Команды</b>\n/status — нагрузка парка сейчас\n/report — полный отчёт сейчас\n"
        "/nodes — список нод")


async def handle_command(db: AsyncSession, cfg: dict, cmd: str, now: datetime | None = None) -> str:
    """Text answer for a command. Pure of Telegram: the caller sends it."""
    now = now or datetime.now(timezone.utc)
    if cmd in ("help", "start"):
        return HELP
    state = await fr.get_state(db)
    if cmd == "report":
        text_, _ = await fr.compose(db, cfg, "full", state, now, with_day=False)
        return text_       # state (levels/upgrades) is not saved: a manual report is not a slot
    snap = await fr.load_snapshot(db, cfg, state, max_age_s=45)
    if cmd == "nodes":
        return fr.build_nodes(snap)
    # status
    last = state.get("last_sent_at")
    last_txt = "—"
    if last:
        try:
            last_txt = fr.local_now(datetime.fromisoformat(last), cfg).strftime("%d.%m %H:%M МСК")
        except ValueError:
            pass
    nxt = fr.next_report(now, cfg)
    nxt_txt = nxt.strftime("%d.%m %H:%M МСК") if nxt else "—"
    return fr.build_status(snap, await fr.ai_summary_line(db), last_txt, nxt_txt)


async def _reply(db: AsyncSession, cfg: dict, text_: str) -> None:
    err = await fr.send_text(db, cfg, text_)
    if err:
        log.warning("fleet bot reply not delivered: %s", err)


async def get_offset(db: AsyncSession) -> int | None:
    v = (await subwatch._json_setting(db, fr.OFFSET_KEY, {})).get("offset")
    return int(v) if isinstance(v, int) else None


async def set_offset(db: AsyncSession, offset: int) -> None:
    await subwatch._save_json(db, fr.OFFSET_KEY, {"offset": int(offset)})
    await db.commit()


async def process_updates(db: AsyncSession, cfg: dict, updates: list[dict]) -> int:
    """Handle a batch; returns the next offset (or -1 for none)."""
    nxt = -1
    chat = await fr.effective_chat(db, cfg)
    for upd in updates:
        uid = upd.get("update_id")
        if isinstance(uid, int):
            nxt = max(nxt, uid + 1)
        got = extract_command(upd, chat)
        if got is None:
            continue
        try:
            answer = await handle_command(db, cfg, got[0])
        except Exception:  # noqa: BLE001
            log.exception("fleet bot command %s failed", got[0])
            answer = "Не получилось собрать ответ, подробности в журнале рагента."
        await _reply(db, cfg, answer)
    return nxt


async def run_fleet_bot() -> None:
    """Long-polling loop. Idle while the report feature or commands are off."""
    from app.db import SessionLocal

    await asyncio.sleep(50)
    webhook_checked = 0.0
    webhook_blocked = False
    backoff = 5
    loop = asyncio.get_running_loop()
    while True:
        try:
            async with SessionLocal() as db:
                cfg = await fr.get_config(db)
                token = await subwatch.bot_token(db)
                chat = await fr.effective_chat(db, cfg)
                if not (cfg["enabled"] and cfg["commands"] and token and chat):
                    await asyncio.sleep(30)
                    continue
                if loop.time() - webhook_checked > WEBHOOK_RECHECK_S:
                    webhook_checked = loop.time()
                    url = await asyncio.to_thread(get_webhook_url, token)
                    webhook_blocked = bool(url)
                    if webhook_blocked:
                        log.warning("fleet bot: a webhook is set by someone else, polling stays off")
                if webhook_blocked:
                    await asyncio.sleep(60)
                    continue
                offset = await get_offset(db)
                updates = await asyncio.to_thread(get_updates, token, offset, 25)
                nxt = await process_updates(db, cfg, updates)
                if nxt >= 0:
                    await set_offset(db, nxt)
                backoff = 5
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            msg = str(exc)
            log.warning("fleet bot poll failed: %s", msg)
            # 409: another consumer polls with the same token; give it room
            await asyncio.sleep(120 if "409" in msg else backoff)
            backoff = min(backoff * 2, 120)
        except Exception:  # noqa: BLE001
            log.exception("fleet bot loop failed")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
