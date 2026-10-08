"""Сторона базы у детектора шаринга: настройки, суточный профиль, тревоги.

Чистая арифметика вердикта живёт в sharing_score, сбор с нод — в
sharing_detect. Здесь всё, чему нужна база: настройки из app_settings (PATCH
только из браузера, как у subwatch/ycwatch), накопление часового профиля
активности и выдержка в красной зоне с сообщением в Телеграм.

Тревога поднимается не на первом красном скане, а когда человек держится в
красной зоне несколько проверок подряд: переезд с Wi-Fi на LTE, гость с
телефоном и дача на выходных дают один красный скан и проходят.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SharingActivity
from app.services import sharing_score, subwatch
from app.services.sharing_score import CONFIG_KEY, STATE_KEY, DEFAULTS
from app.services.telegram import send_message

log = logging.getLogger("remna.sharing")

# Служебный «пользователь», в котором лежит число сканов за час.
ALL_SCANS = 0
# Профиль суток пересчитывается не чаще: он меняется за часы, не за минуты.
PROFILE_TTL = timedelta(minutes=30)

_profiles: dict[str, object] = {"at": None, "data": {}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── настройки и состояние ────────────────────────────────────────────────────

async def get_config(db: AsyncSession) -> dict:
    return await subwatch._json_setting(db, CONFIG_KEY, DEFAULTS)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await subwatch._save_json(db, CONFIG_KEY, cfg)


async def get_state(db: AsyncSession) -> dict:
    return await subwatch._json_setting(db, STATE_KEY, {"users": {}})


async def save_state(db: AsyncSession, state: dict) -> None:
    await subwatch._save_json(db, STATE_KEY, state)


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    """Свой чат, а если не задан — общий, из следилки за подпиской."""
    return (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()


# ── суточный профиль ─────────────────────────────────────────────────────────

async def record_activity(db: AsyncSession, online_ids: list[int],
                          now: datetime | None = None) -> None:
    """Отметить час: сколько было сканов и кто в нём был онлайн."""
    now = now or _now()
    bucket = now.replace(minute=0, second=0, microsecond=0)
    rows = [{"user_id": ALL_SCANS, "bucket": bucket, "seen": 1}]
    rows += [{"user_id": int(uid), "bucket": bucket, "seen": 1}
             for uid in online_ids if int(uid) != ALL_SCANS]
    stmt = pg_insert(SharingActivity).values(rows)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[SharingActivity.user_id, SharingActivity.bucket],
        set_={"seen": SharingActivity.seen + 1},
    ))


async def prune_activity(db: AsyncSession, cfg: dict) -> int:
    days = max(1, int(cfg.get("night_keep_days") or 14))
    res = await db.execute(
        delete(SharingActivity).where(SharingActivity.bucket < _now() - timedelta(days=days))
    )
    return res.rowcount or 0


async def build_profiles(db: AsyncSession, cfg: dict) -> dict[int, tuple[float, int]]:
    """{user_id: (занятость самого тихого окна суток в %, суток наблюдений)}.

    Считается от доли «был онлайн / было сканов в этот час»: рагент
    перезапускается, часы бывают неполными, и сырое число отметок обмануло бы.
    """
    days = max(1, int(cfg.get("night_keep_days") or 14))
    cutoff = _now() - timedelta(days=days)
    hour = func.extract("hour", SharingActivity.bucket)
    rows = await db.execute(
        select(SharingActivity.user_id, hour.label("h"), func.sum(SharingActivity.seen))
        .where(SharingActivity.bucket >= cutoff)
        .group_by(SharingActivity.user_id, "h")
    )
    per_user: dict[int, dict[int, int]] = {}
    totals: dict[int, int] = {}
    for uid, h, seen in rows.all():
        uid, h, seen = int(uid), int(h), int(seen or 0)
        if uid == ALL_SCANS:
            totals[h] = seen
        else:
            per_user.setdefault(uid, {})[h] = seen

    spans = await db.execute(
        select(SharingActivity.user_id,
               func.count(func.distinct(func.date_trunc("day", SharingActivity.bucket))))
        .where(SharingActivity.bucket >= cutoff, SharingActivity.user_id != ALL_SCANS)
        .group_by(SharingActivity.user_id)
    )
    span_by_user = {int(uid): int(n or 0) for uid, n in spans.all()}

    width = int(cfg.get("night_window_h") or 4)
    out: dict[int, tuple[float, int]] = {}
    for uid, hours in per_user.items():
        table = {h: (totals.get(h, 0), hours.get(h, 0)) for h in totals}
        pct = sharing_score.quiet_window(table, width)
        if pct is None:
            continue
        out[uid] = (pct, span_by_user.get(uid, 0))
    return out


async def profiles(db: AsyncSession, cfg: dict) -> dict[int, tuple[float, int]]:
    """Профиль с кэшем: пересчитываем раз в полчаса, не на каждый скан."""
    at = _profiles.get("at")
    if isinstance(at, datetime) and _now() - at < PROFILE_TTL:
        return _profiles["data"]  # type: ignore[return-value]
    data = await build_profiles(db, cfg)
    _profiles.update(at=_now(), data=data)
    return data


def forget_profiles() -> None:
    _profiles.update(at=None, data={})


# ── тревоги ──────────────────────────────────────────────────────────────────

def _why(hit: dict, limit: int = 4) -> list[str]:
    return [str(p.get("why") or "") for p in (hit.get("parts") or [])[:limit]]


def alert_text(kind: str, hit: dict) -> str:
    """Сообщение в чат. Коротко: кто, сколько баллов и за что."""
    name = html.escape(str(hit.get("username") or hit.get("user_id")))
    score = hit.get("score", 0)
    limit = hit.get("device_limit", 0)
    if kind == "clear":
        return (f"🟢 <b>Шаринг снят: {name}</b>\n"
                f"Оценка опустилась до {score} и держится. Больше не тревожим.")
    head = "🔴 <b>Похоже на шаринг: " if kind == "alert" else "🔁 <b>Всё ещё похоже на шаринг: "
    lines = [f"{head}{name}</b>",
             f"Оценка {score} из 100, норма считалась от лимита устройств {limit}"
             + (" (безлимит)" if hit.get("unlimited") else "") + ".",
             f"ID {hit.get('user_id')}, ноды: "
             f"{html.escape(', '.join(hit.get('rw_nodes') or []) or '—')}"]
    lines += ["• " + html.escape(w) for w in _why(hit)]
    lines.append("Лимит устройств не трогаем — это платная услуга. Решение за владельцем.")
    return "\n".join(lines)


async def handle_scan(db: AsyncSession, cfg: dict, suspects: list[dict],
                      now: datetime | None = None) -> list[tuple[str, dict]]:
    """Прокрутить выдержку по каждому подозреваемому и сказать, о ком говорить.

    Возвращает [(событие, строка пользователя)] — чтобы тесты и ручной прогон
    видели решение, не отправляя ничего в чат.
    """
    now = now or _now()
    state = await get_state(db)
    users: dict = dict(state.get("users") or {})
    by_id = {int(h["user_id"]): h for h in suspects}
    events: list[tuple[str, dict]] = []

    # Кто уже был под наблюдением, но в этом скане не всплыл — зелёный: он либо
    # ушёл в офлайн, либо успокоился. И то и другое снимает выдержку.
    for key in list(users):
        try:
            uid = int(key)
        except (TypeError, ValueError):
            users.pop(key, None)
            continue
        if uid in by_id:
            continue
        st, event = sharing_score.step_user(users[key], cfg, sharing_score.GREEN, 0, now)
        if event == "clear":
            events.append((event, {"user_id": uid, "username": st.get("username", ""),
                                   "score": 0, "device_limit": 0}))
            users.pop(key, None)
        elif st.get("alerted") or st.get("red"):
            users[key] = st
        else:
            users.pop(key, None)

    for uid, hit in by_id.items():
        st, event = sharing_score.step_user(
            users.get(str(uid)), cfg, str(hit.get("level")), int(hit.get("score") or 0), now)
        st["username"] = hit.get("username") or ""
        if event:
            events.append((event, hit))
        if st.get("alerted") or st.get("red") or st.get("green"):
            users[str(uid)] = st
        else:
            users.pop(str(uid), None)

    state["users"] = users
    state["checked_at"] = now.isoformat()
    await save_state(db, state)
    return events


async def notify(db: AsyncSession, cfg: dict, events: list[tuple[str, dict]]) -> list[str]:
    """Отправить сообщения. Бот не настроен — молча ничего не шлём."""
    if not events or not cfg.get("notify"):
        return []
    token = await subwatch.bot_token(db)
    chat = await effective_chat(db, cfg)
    if not token or not chat:
        log.info("sharing: событий %s, но бот не настроен — молчим", len(events))
        return []
    errors: list[str] = []
    for kind, hit in events:
        err = await asyncio.to_thread(send_message, token, chat, alert_text(kind, hit))
        if err:
            log.warning("sharing: уведомление не ушло: %s", err)
            errors.append(err)
    return errors
