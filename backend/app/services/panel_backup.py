"""Бэкап панели Remnawave: забрать шифрованный дамп и сложить у себя.

Панель — единственное место, где живут пользователи, подписки и вся история.
API отдать бэкап не умеет (в бэкенде панели нет такой ручки), поэтому дамп
снимает скрипт на самой панели, а мы его только забираем.

Ключевое: шифрование происходит **на панели**, до отправки. Сюда приезжает уже
закрытый файл, расшифровать который может только владелец закрытого ключа —
на серверах этого ключа нет ни одного. Поэтому даже полный доступ к этой машине
не даёт базы пользователей.

SSH-ключ, которым мы ходим на панель, ограничен на её стороне одной командой
(`command="/usr/local/bin/rw-backup"` в authorized_keys). Оболочку он не даёт.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.services.crypto import decrypt_secret
from app.services import subwatch
from app.services.telegram import send_message

log = logging.getLogger(__name__)

CONFIG_KEY = "panel_backup_config"
STATE_KEY = "panel_backup_state"
SSH_KEY_KEY = "panel_backup_ssh_key"

BACKUP_DIR = Path(os.environ.get("PANEL_BACKUP_DIR", "/var/lib/remna-backups"))
MAGIC = b"RWBK1\n"
MIN_SIZE = 100_000          # меньше — значит дамп не снялся
SSH_TIMEOUT = 60.0
READ_TIMEOUT = 900.0
NAME_RE = re.compile(r"^panel-(\d{8})-(\d{4})\.rwbk$")

DEFAULTS = {
    "enabled": False,        # включает человек
    "interval_hours": 6,     # четыре раза в сутки
    "host": "",
    "port": 22,
    "user": "root",
    "keep_recent": 28,       # все подряд — неделя при четырёх в сутки
    "keep_daily": 30,        # дальше по одному в день
    "notify": True,
}


@dataclass
class BackupFile:
    name: str
    size: int
    created: datetime

    def as_dict(self) -> dict:
        return {"name": self.name, "size": self.size,
                "created": self.created.isoformat()}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── настройки и состояние ────────────────────────────────────────────────────

async def _json_setting(db: AsyncSession, key: str, fallback: dict) -> dict:
    row = await db.get(AppSetting, key)
    if not row or not row.value_enc:
        return dict(fallback)
    try:
        data = json.loads(row.value_enc)
    except ValueError:
        return dict(fallback)
    return {**fallback, **data} if isinstance(data, dict) else dict(fallback)


async def _save_json(db: AsyncSession, key: str, data: dict) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value_enc=None)
        db.add(row)
        await db.flush()
    row.value_enc = json.dumps(data, ensure_ascii=False)


async def get_config(db: AsyncSession) -> dict:
    return await _json_setting(db, CONFIG_KEY, DEFAULTS)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await _save_json(db, CONFIG_KEY, cfg)


async def get_state(db: AsyncSession) -> dict:
    return await _json_setting(db, STATE_KEY, {
        "last_run_at": None, "last_ok_at": None, "last_error": None,
        "last_size": None, "last_duration_ms": None, "notified_error": False,
    })


async def ssh_key(db: AsyncSession) -> str:
    row = await db.get(AppSetting, SSH_KEY_KEY)
    if not row or not row.value_enc:
        return ""
    try:
        return decrypt_secret(row.value_enc)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования
        return ""


# ── файлы ────────────────────────────────────────────────────────────────────

def listing() -> list[BackupFile]:
    if not BACKUP_DIR.exists():
        return []
    out: list[BackupFile] = []
    for f in BACKUP_DIR.glob("panel-*.rwbk"):
        m = NAME_RE.match(f.name)
        if not m:
            continue
        try:
            when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append(BackupFile(f.name, f.stat().st_size, when))
    return sorted(out, key=lambda b: b.created, reverse=True)


def prune(keep_recent: int, keep_daily: int) -> list[str]:
    """Свежие храним подряд, дальше — по одному на день. Возвращает удалённое."""
    files = listing()
    keep = {b.name for b in files[:keep_recent]}
    seen_days: set[str] = set()
    for b in files[keep_recent:]:
        day = b.created.strftime("%Y%m%d")
        if day in seen_days:
            continue
        seen_days.add(day)
        if len(seen_days) <= keep_daily:
            keep.add(b.name)
    removed = []
    for b in files:
        if b.name not in keep:
            (BACKUP_DIR / b.name).unlink(missing_ok=True)
            removed.append(b.name)
    return removed


# ── снятие ───────────────────────────────────────────────────────────────────

def fetch(cfg: dict, key_text: str) -> BackupFile:
    """Сходить на панель и положить шифрованный архив. Бросает при неудаче."""
    host = (cfg.get("host") or "").strip()
    if not host:
        raise RuntimeError("не задан адрес панели")
    if not key_text.strip():
        raise RuntimeError("не задан SSH-ключ для панели")

    key = None
    for loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            key = loader.from_private_key(io.StringIO(key_text))
            break
        except Exception:  # noqa: BLE001 — просто не тот тип ключа
            continue
    if key is None:
        raise RuntimeError("не удалось разобрать SSH-ключ")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M")
    final = BACKUP_DIR / f"panel-{stamp}.rwbk"
    tmp = BACKUP_DIR / f".{final.name}.part"

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    started = time.monotonic()
    try:
        cli.connect(host, port=int(cfg.get("port") or 22),
                    username=(cfg.get("user") or "root"), pkey=key,
                    timeout=SSH_TIMEOUT, look_for_keys=False, allow_agent=False)
        # Команда здесь не важна: на той стороне authorized_keys подменяет её
        # своей. Пишем осмысленную на случай, если ограничение когда-то снимут.
        _, out, err = cli.exec_command("/usr/local/bin/rw-backup", timeout=READ_TIMEOUT)
        size = 0
        with tmp.open("wb") as fh:
            while True:
                block = out.read(1024 * 256)
                if not block:
                    break
                fh.write(block)
                size += len(block)
        code = out.channel.recv_exit_status()
        stderr = err.read().decode("utf-8", "replace").strip()
    finally:
        cli.close()

    if code != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"скрипт на панели вернул {code}: {stderr[:300]}")
    if size < MIN_SIZE:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"архив подозрительно мал: {size} байт. {stderr[:200]}")
    with tmp.open("rb") as fh:
        if fh.read(len(MAGIC)) != MAGIC:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("это не шифрованный архив RWBK1 — проверьте скрипт на панели")

    tmp.rename(final)
    log.info("бэкап панели: %s, %s байт за %.1f с", final.name, size,
             time.monotonic() - started)
    return BackupFile(final.name, size, _now())


# ── один прогон ──────────────────────────────────────────────────────────────

async def run_once(db: AsyncSession, cfg: dict | None = None) -> dict:
    cfg = cfg or await get_config(db)
    key_text = await ssh_key(db)
    state = await get_state(db)
    started = time.monotonic()

    try:
        item = await asyncio.to_thread(fetch, cfg, key_text)
        removed = await asyncio.to_thread(
            prune, int(cfg.get("keep_recent") or 28), int(cfg.get("keep_daily") or 30))
        state.update({
            "last_run_at": _now().isoformat(), "last_ok_at": _now().isoformat(),
            "last_error": None, "last_size": item.size,
            "last_duration_ms": int((time.monotonic() - started) * 1000),
        })
        if state.get("notified_error"):
            await _notify(db, cfg, f"🟢 <b>Бэкап панели снова снимается</b>\n{item.name}, "
                                   f"{item.size // 1024} КБ")
            state["notified_error"] = False
        if removed:
            log.info("бэкап панели: удалено старых %s", len(removed))
        result = {"ok": True, "file": item.as_dict(), "removed": len(removed)}
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        state.update({
            "last_run_at": _now().isoformat(), "last_error": msg,
            "last_duration_ms": int((time.monotonic() - started) * 1000),
        })
        log.warning("бэкап панели не снялся: %s", msg)
        if not state.get("notified_error"):
            await _notify(db, cfg, "🔴 <b>Бэкап панели не снялся</b>\n" + msg
                                   + "\nПока это не починить, свежей копии базы нет.")
            state["notified_error"] = True
        result = {"ok": False, "error": msg}

    await _save_json(db, STATE_KEY, state)
    await db.commit()
    return result


async def _notify(db: AsyncSession, cfg: dict, text: str) -> None:
    """Шлём тем же ботом, что и следилка за подпиской — он у нас один."""
    if not cfg.get("notify"):
        return
    token = await subwatch.bot_token(db)
    chat = ((await subwatch.get_config(db)).get("chat_id") or "").strip()
    if not token or not chat:
        return
    err = await asyncio.to_thread(send_message, token, chat, text)
    if err:
        log.warning("бэкап панели: уведомление не ушло: %s", err)


# ── фоновый цикл ─────────────────────────────────────────────────────────────

async def run_panel_backup() -> None:
    """Спит и снимает по расписанию. Выключенный только спит."""
    from app.db import SessionLocal

    await asyncio.sleep(20)
    while True:
        delay = 300.0
        try:
            async with SessionLocal() as db:
                cfg = await get_config(db)
                interval = max(1, int(cfg.get("interval_hours") or 6))
                if not cfg.get("enabled"):
                    delay = 300.0
                else:
                    state = await get_state(db)
                    last = state.get("last_ok_at") or state.get("last_run_at")
                    due = True
                    if last:
                        try:
                            due = _now() - datetime.fromisoformat(last) >= timedelta(hours=interval)
                        except ValueError:
                            due = True
                    if due:
                        await run_once(db, cfg)
                    delay = 300.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("бэкап панели: цикл упал")
        await asyncio.sleep(delay)
