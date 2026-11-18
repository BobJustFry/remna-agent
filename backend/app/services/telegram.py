"""Отправка уведомлений в Telegram.

Отдельно от того, кто именно тревожится: сегодня это следилка за подпиской,
завтра — любая другая проверка. Знает только «куда» и «каким текстом».

Все обращения к Telegram идут через `_request`. Если задан прокси (настройка
«Прокси для Telegram»), трафик идёт через него: http(s):// — через CONNECT,
socks5:// и socks5h:// — через socksio (httpx[socks]). Адрес прокси держится в
модуле (`set_proxy`): его выставляют при старте и при сохранении настроек, так
что сигнатуры send_message/describe_chat не меняются.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import urllib.parse
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
TIMEOUT = 15.0
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")

_proxy: str = ""
_proxy_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "telegram_proxy_override", default=None)


class TelegramError(Exception):
    """Текст уже очищен от токена и данных прокси."""


# ── прокси ───────────────────────────────────────────────────────────────────

def validate_proxy(url: str) -> str | None:
    """None — адрес годится, иначе причина (без самого адреса)."""
    try:
        p = urllib.parse.urlsplit(url.strip())
        host, port = p.hostname, p.port
    except ValueError:
        return "не разобрать адрес прокси"
    if p.scheme not in PROXY_SCHEMES:
        return "схема прокси: http, https, socks5 или socks5h"
    if not host:
        return "в адресе прокси нет хоста"
    if port is None:
        return "в адресе прокси нет порта"
    return None


def mask_proxy(url: str) -> str:
    """Вид для людей: схема://host:port, без логина и пароля."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        p = urllib.parse.urlsplit(url)
        if p.hostname and p.port:
            host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
            return f"{p.scheme}://{host}:{p.port}"
    except ValueError:
        pass
    return "прокси задан"


def set_proxy(url: str | None) -> None:
    global _proxy
    _proxy = (url or "").strip()


def get_proxy() -> str:
    """Действующий прокси: временный (из контекста проверки), иначе сохранённый."""
    override = _proxy_override.get()
    return _proxy if override is None else override


@contextlib.contextmanager
def using_proxy(url: str | None):
    """Временно подменить прокси для вызовов в этом контексте (и в to_thread).

    None — не подменять. Пустая строка — напрямую. Глобальный _proxy не трогаем,
    чтобы параллельная работа следилки не увидела чужой адрес.
    """
    if url is None:
        yield
        return
    tok = _proxy_override.set(url.strip())
    try:
        yield
    finally:
        _proxy_override.reset(tok)


def scrub(text: str, token: str = "") -> str:
    """Убрать из текста токен бота, адрес прокси и его логин/пароль."""
    text = str(text)
    secrets: list[str] = []
    if token:
        secrets += [token, token.split(":", 1)[0] if ":" in token else ""]
    for url in {_proxy, get_proxy()}:
        if not url:
            continue
        secrets.append(url)
        try:
            p = urllib.parse.urlsplit(url)
            secrets += [p.username or "", p.password or "",
                        urllib.parse.unquote(p.username or ""),
                        urllib.parse.unquote(p.password or "")]
        except ValueError:
            pass
    for s in sorted({s for s in secrets if len(s) >= 3}, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


# ── транспорт ────────────────────────────────────────────────────────────────

def _request(token: str, method: str, fields: dict[str, Any] | None = None,
             post: bool = False, timeout: float = TIMEOUT) -> dict:
    """Один вызов Bot API; возвращает разобранный JSON. Ошибки — TelegramError."""
    url = f"{API}/bot{token}/{method}"
    proxy = get_proxy() or None
    try:
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False) as client:
            if post:
                resp = client.post(url, data=fields or {})
            else:
                resp = client.get(url, params=fields or {})
    except httpx.TimeoutException:
        where = "через прокси" if proxy else "напрямую"
        raise TelegramError(f"Telegram недоступен {where}: таймаут") from None
    except httpx.HTTPError as exc:
        where = "через прокси" if proxy else "напрямую"
        raise TelegramError(
            f"Telegram недоступен {where}: {scrub(exc, token) or type(exc).__name__}") from None
    except Exception as exc:  # noqa: BLE001 — например, нет socksio или кривая схема
        raise TelegramError(f"Telegram: {scrub(exc, token)}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        detail = body.get("description") if isinstance(body, dict) else None
        detail = detail or resp.text[:200]
        raise TelegramError(f"Telegram {resp.status_code}: {scrub(detail, token)}")
    if not isinstance(body, dict) or not body.get("ok"):
        desc = body.get("description", "?") if isinstance(body, dict) else "?"
        raise TelegramError(f"Telegram отказал: {scrub(desc, token)}")
    return body


# ── операции ─────────────────────────────────────────────────────────────────

def send_message(token: str, chat_id: str, text: str,
                 parse_mode: str | None = "HTML") -> str | None:
    """Отправить сообщение. None — доставлено, иначе текст ошибки.

    Наружу не бросаем: не доставленное уведомление не должно ронять проверку,
    ради которой всё затевалось.
    """
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return "не заданы токен бота или id чата"

    fields = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:          # None — обычный текст: адреса и «_» не ломают разметку
        fields["parse_mode"] = parse_mode
    try:
        _request(token, "sendMessage", fields, post=True)
    except TelegramError as exc:
        return str(exc)
    return None


def describe_chat(token: str, chat_id: str) -> str | None:
    """Название чата — чтобы человек убедился, что бот пишет туда, куда он думает."""
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return None
    try:
        body = _request(token, "getChat", {"chat_id": chat_id})
    except TelegramError:      # это украшение, а не функциональность
        return None
    chat = body.get("result") or {}
    return chat.get("title") or chat.get("username") or None


def get_me(token: str) -> dict:
    """Данные бота (getMe). TelegramError, если недоступен."""
    return (_request((token or "").strip(), "getMe").get("result")) or {}


def detect_chats(token: str) -> list[dict]:
    """Чаты, из которых боту писали (getUpdates). Тексты сообщений не отдаём."""
    body = _request((token or "").strip(), "getUpdates",
                    {"limit": 100, "timeout": 0,
                     "allowed_updates": '["message","channel_post","my_chat_member"]'})
    found: dict[int, dict] = {}
    for upd in body.get("result") or []:
        if not isinstance(upd, dict):
            continue
        for key in ("message", "channel_post", "my_chat_member"):
            chat = (upd.get(key) or {}).get("chat")
            if not isinstance(chat, dict) or "id" not in chat:
                continue
            name = (chat.get("title")
                    or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                    or None)
            found[chat["id"]] = {
                "id": chat["id"],
                "type": chat.get("type") or "?",
                "title": name,
                "username": chat.get("username"),
            }
    return list(found.values())


# ── входящие (long polling) ──────────────────────────────────────────────────

def get_updates(token: str, offset: int | None, wait_s: int = 25) -> list[dict]:
    """Long-poll getUpdates through the same proxy as sending. Raises TelegramError."""
    fields: dict[str, Any] = {"timeout": int(wait_s), "limit": 50,
                              "allowed_updates": '["message","channel_post"]'}
    if offset is not None:
        fields["offset"] = int(offset)
    body = _request((token or "").strip(), "getUpdates", fields, timeout=wait_s + 15.0)
    res = body.get("result")
    return [u for u in res if isinstance(u, dict)] if isinstance(res, list) else []


def get_webhook_url(token: str) -> str:
    """URL of a configured webhook ('' if none). Raises TelegramError."""
    body = _request((token or "").strip(), "getWebhookInfo")
    return str(((body.get("result") or {}).get("url")) or "")
