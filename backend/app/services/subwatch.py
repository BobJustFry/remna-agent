"""Следилка за подпиской: отдаётся ли она клиенту прямо сейчас.

Единственная проверка, которая отвечает на вопрос «новый человек сможет
подключиться?». Подписка идёт через чужой CDN на наш ориджин, и сломаться
может любое звено: у провайдера снят ресурс, протух сертификат (он там
продлевается руками), лёг Caddy, панель отдала пустой список. Снаружи всё
это выглядит одинаково — тишина, о которой никто не сообщит: старые клиенты
продолжают работать на уже скачанном конфиге.

Поэтому забираем подписку ровно так, как её забирает клиент, и смотрим не
на код ответа, а на то, есть ли внутри конфиги.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, SubCheck
from app.services.crypto import decrypt_secret
from app.services import telegram
from app.services.telegram import send_message

log = logging.getLogger(__name__)

CONFIG_KEY = "subwatch_config"
STATE_KEY = "subwatch_state"
TG_TOKEN_KEY = "telegram_bot_token"
TG_PROXY_KEY = "telegram_proxy"

# Клиенты представляются по-разному, а панель по User-Agent выбирает формат.
# Нейтральный агент даёт базовый список ссылок — его и разбираем.
USER_AGENT = "remna-agent-subwatch/1"
TIMEOUT = 15.0
MAX_BODY = 2 * 1024 * 1024
KEEP_DAYS = 14

# Одна осечка — это сеть моргнула. Тревожим со второй подряд.
FAIL_STREAK = 2
# Пока лежит, напоминаем не чаще этого — чтобы группа не превратилась в шум.
REMIND_MINUTES = 60

# Схема считается только с начала строки: иначе "ss://" находится внутри
# "vless://" и каждая ссылка учитывается дважды.
LINK_RE = re.compile(
    r"(?:^|[\s,;])(?:vless|vmess|trojan|ss|ssr|hysteria2?|hy2|tuic|socks)://",
    re.MULTILINE,
)

DEFAULTS = {
    "enabled": False,          # включает человек, а не установка пакета
    "interval_min": 15,
    "url": "",
    "origin_url": "",
    "min_hosts": 3,
    "notify": True,
    "chat_id": "",
}


@dataclass
class Probe:
    ok: bool
    via: str
    http_status: int | None = None
    duration_ms: int = 0
    hosts: int | None = None
    cert_days: int | None = None
    error: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count_configs(raw: bytes) -> int:
    """Сколько конфигов внутри. Подписка бывает голым списком и base64."""
    text = raw.decode("utf-8", errors="replace").strip()
    if text and not LINK_RE.search(text):
        # base64 бывает с переносами и без набивки — обе формы приводим к одной.
        compact = re.sub(r"\s+", "", text)
        if re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact or "x"):
            try:
                pad = "=" * (-len(compact) % 4)
                decoded = base64.b64decode(compact + pad, altchars=b"-_", validate=False)
                text = decoded.decode("utf-8", errors="replace")
            except (binascii.Error, ValueError):
                pass
    return len(LINK_RE.findall(text))


def _cert_days(host: str, port: int) -> int | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None
    if not der:
        return None
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        return None
    return (cert.not_valid_after_utc - _now()).days


def probe(url: str, via: str, min_hosts: int) -> Probe:
    """Забрать подписку и убедиться, что внутри конфиги, а не страница ошибки."""
    url = (url or "").strip()
    if not url:
        return Probe(ok=False, via=via, error="адрес подписки не задан")

    parsed = urllib.parse.urlparse(url)
    started = time.monotonic()
    status: int | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            body = resp.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        ms = int((time.monotonic() - started) * 1000)
        return Probe(ok=False, via=via, http_status=exc.code, duration_ms=ms,
                     error=f"подписка ответила {exc.code}")
    except urllib.error.URLError as exc:
        ms = int((time.monotonic() - started) * 1000)
        return Probe(ok=False, via=via, duration_ms=ms, error=f"не достучались: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        ms = int((time.monotonic() - started) * 1000)
        return Probe(ok=False, via=via, duration_ms=ms, error=f"не достучались: {exc}")

    ms = int((time.monotonic() - started) * 1000)
    hosts = _count_configs(body)
    days = None
    if parsed.scheme == "https" and parsed.hostname:
        days = _cert_days(parsed.hostname, parsed.port or 443)

    if hosts < min_hosts:
        return Probe(ok=False, via=via, http_status=status, duration_ms=ms, hosts=hosts,
                     cert_days=days,
                     error=f"ответ 200, но конфигов внутри {hosts} — подписка пустая")
    return Probe(ok=True, via=via, http_status=status, duration_ms=ms, hosts=hosts,
                 cert_days=days)


# ── настройки и состояние ────────────────────────────────────────────────────

async def _json_setting(db: AsyncSession, key: str, fallback: dict) -> dict:
    row = await db.get(AppSetting, key)
    if not row or not row.value_enc:
        return dict(fallback)
    try:
        data = json.loads(row.value_enc)
    except ValueError:
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    return {**fallback, **data}


async def _save_json(db: AsyncSession, key: str, data: dict) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value_enc=None)
        db.add(row)
        await db.flush()
    row.value_enc = json.dumps(data, ensure_ascii=False)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await _save_json(db, CONFIG_KEY, cfg)


def now() -> datetime:
    return _now()


async def get_config(db: AsyncSession) -> dict:
    return await _json_setting(db, CONFIG_KEY, DEFAULTS)


async def get_state(db: AsyncSession) -> dict:
    return await _json_setting(db, STATE_KEY,
                               {"ok": None, "since": None, "fails": 0, "last_notified": None})


async def bot_token(db: AsyncSession) -> str:
    row = await db.get(AppSetting, TG_TOKEN_KEY)
    if not row or not row.value_enc:
        return ""
    try:
        return decrypt_secret(row.value_enc)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования
        return ""


async def telegram_proxy(db: AsyncSession) -> str:
    """Прокси для Telegram из базы (расшифрованный); пусто — идём напрямую."""
    row = await db.get(AppSetting, TG_PROXY_KEY)
    if not row or not row.value_enc:
        return ""
    try:
        return decrypt_secret(row.value_enc)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования
        return ""


async def apply_telegram_proxy(db: AsyncSession) -> None:
    """Прочитать прокси из базы и выставить его модулю telegram."""
    telegram.set_proxy(await telegram_proxy(db))


# ── один цикл проверки ───────────────────────────────────────────────────────

def _delta_since(iso: str | None, now: datetime) -> timedelta | None:
    if not iso:
        return None
    try:
        return now - datetime.fromisoformat(iso)
    except ValueError:
        return None


def _alert_text(cfg: dict, main: Probe, origin: Probe | None) -> str:
    lines = [
        "🔴 <b>Подписка не отдаётся</b>",
        cfg.get("url", ""),
        f"Причина: {main.error}",
    ]
    # Ориджин щупаем только при поломке — зато сразу видно, чья это поломка.
    if origin is not None:
        if origin.ok:
            lines.append(f"Ориджин напрямую отвечает — {origin.hosts} конфигов.")
            lines.append("Значит, оборвалось между клиентом и нами: CDN.")
        else:
            lines.append(f"Ориджин напрямую тоже молчит: {origin.error}")
            lines.append("Значит, дело на нашей стороне, CDN ни при чём.")
    lines.append("Новые клиенты сейчас не получат конфиг.")
    return "\n".join(x for x in lines if x)


def _recovery_text(cfg: dict, main: Probe, down_for: timedelta | None) -> str:
    lines = ["🟢 <b>Подписка снова отдаётся</b>", f"{cfg.get('url', '')}"]
    if down_for is not None:
        minutes = max(1, int(down_for.total_seconds() // 60))
        lines.append(f"Лежала примерно {minutes} мин.")
    lines.append(f"Сейчас: {main.hosts} конфигов за {main.duration_ms} мс.")
    return "\n".join(lines)


async def check_once(db: AsyncSession, cfg: dict | None = None) -> dict:
    """Проверить подписку, записать результат и, если надо, сказать в телеграм."""
    cfg = cfg or await get_config(db)
    main = await asyncio.to_thread(probe, cfg["url"], "cdn", int(cfg["min_hosts"]))

    # Ориджин трогаем только когда основной путь сломался: так видно, чья вина,
    # и мы не создаём лишнего трафика, пока всё хорошо.
    origin: Probe | None = None
    if not main.ok and (cfg.get("origin_url") or "").strip():
        origin = await asyncio.to_thread(probe, cfg["origin_url"], "origin", int(cfg["min_hosts"]))

    for p in (main, origin):
        if p is None:
            continue
        db.add(SubCheck(via=p.via, ok=p.ok, http_status=p.http_status,
                        duration_ms=p.duration_ms, hosts=p.hosts,
                        cert_days=p.cert_days, error=p.error))

    state = await get_state(db)
    now = _now()
    notified: str | None = None

    if main.ok:
        was_down = state.get("ok") is False
        if was_down:
            notified = await _notify(
                db, cfg, _recovery_text(cfg, main, _delta_since(state.get("since"), now)))
        changed = state.get("ok") is not True
        state = {
            "ok": True,
            # since — «в этом состоянии с»: сдвигаем только на переходе.
            "since": now.isoformat() if changed else (state.get("since") or now.isoformat()),
            "fails": 0,
            "last_notified": now.isoformat() if was_down else state.get("last_notified"),
        }
    else:
        fails = int(state.get("fails") or 0) + 1
        state = dict(state)
        state["fails"] = fails
        if state.get("ok") is not False and fails >= FAIL_STREAK:
            state["ok"] = False
            state["since"] = now.isoformat()
            notified = await _notify(db, cfg, _alert_text(cfg, main, origin))
            state["last_notified"] = now.isoformat()
        elif state.get("ok") is False:
            waited = _delta_since(state.get("last_notified"), now)
            if waited is None or waited >= timedelta(minutes=REMIND_MINUTES):
                notified = await _notify(db, cfg, _alert_text(cfg, main, origin))
                state["last_notified"] = now.isoformat()

    await _save_json(db, STATE_KEY, state)
    await db.commit()
    return {"main": asdict(main), "origin": asdict(origin) if origin else None, "state": state,
            "notify_error": notified}


async def _notify(db: AsyncSession, cfg: dict, text: str) -> str | None:
    """Вернёт текст ошибки доставки, либо None если отправили (или не просили)."""
    if not cfg.get("notify"):
        return None
    token = await bot_token(db)
    chat = (cfg.get("chat_id") or "").strip()
    if not token or not chat:
        return "уведомления включены, но бот не настроен"
    err = await asyncio.to_thread(send_message, token, chat, text)
    if err:
        log.warning("subwatch: уведомление не ушло: %s", err)
    return err


async def prune(db: AsyncSession) -> int:
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    res = await db.execute(delete(SubCheck).where(SubCheck.checked_at < cutoff))
    await db.commit()
    return res.rowcount or 0


async def recent(db: AsyncSession, limit: int = 40) -> list[SubCheck]:
    rows = await db.execute(
        select(SubCheck).order_by(SubCheck.checked_at.desc()).limit(limit)
    )
    return list(rows.scalars())


# ── фоновый цикл ─────────────────────────────────────────────────────────────

async def run_subwatch() -> None:
    """Спит и проверяет. Выключенный — просто спит, ничего не трогая."""
    # Движок базы поднимается здесь, а не при импорте: разбор подписки
    # должен проверяться тестами без живого постгреса.
    from app.db import SessionLocal

    await asyncio.sleep(10)
    ticks = 0
    while True:
        delay = 60.0
        try:
            async with SessionLocal() as db:
                cfg = await get_config(db)
                delay = max(60.0, float(cfg.get("interval_min") or 15) * 60)
                if cfg.get("enabled") and (cfg.get("url") or "").strip():
                    out = await check_once(db, cfg)
                    ok = out["main"]["ok"]
                    log.info("subwatch: %s (%s мс, конфигов %s)",
                             "ок" if ok else "ПОДПИСКА НЕ ОТДАЁТСЯ",
                             out["main"]["duration_ms"], out["main"]["hosts"])
                    ticks += 1
                    if ticks % 96 == 0:
                        await prune(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("subwatch: цикл упал")
        await asyncio.sleep(delay)
