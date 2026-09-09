"""Отправка уведомлений в Telegram.

Отдельно от того, кто именно тревожится: сегодня это следилка за подпиской,
завтра — любая другая проверка. Знает только «куда» и «каким текстом».
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org"
TIMEOUT = 15.0


def send_message(token: str, chat_id: str, text: str) -> str | None:
    """Отправить сообщение. None — доставлено, иначе текст ошибки.

    Наружу не бросаем: не доставленное уведомление не должно ронять проверку,
    ради которой всё затевалось.
    """
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return "не заданы токен бота или id чата"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"{API}/bot{token}/sendMessage", data=payload)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        try:
            detail = json.loads(detail).get("description", detail)
        except ValueError:
            pass
        return f"Telegram {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return f"Telegram недоступен: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return f"Telegram: {exc}"
    if not body.get("ok"):
        return f"Telegram отказал: {body.get('description', '?')}"
    return None


def describe_chat(token: str, chat_id: str) -> str | None:
    """Название чата — чтобы человек убедился, что бот пишет туда, куда он думает."""
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return None
    url = f"{API}/bot{token}/getChat?" + urllib.parse.urlencode({"chat_id": chat_id})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode() or "{}")
    except Exception:  # noqa: BLE001  — это украшение, а не функциональность
        return None
    chat = body.get("result") or {}
    return chat.get("title") or chat.get("username") or None
