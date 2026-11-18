"""Защита логов от утечки токена Telegram-бота и адреса прокси."""

from __future__ import annotations

import logging
import re

_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_NOISY = ("httpx", "httpcore")


class TokenMaskFilter(logging.Filter):
    """Заменяет `bot<id>:<токен>` на `bot***` в тексте записи."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        masked = _TOKEN_RE.sub("bot***", text)
        if masked != text:
            record.msg = masked
            record.args = None
        return True


def setup_log_safety() -> None:
    """httpx/httpcore — не ниже WARNING; маска токена на всех хендлерах."""
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    flt = TokenMaskFilter()
    loggers = [logging.getLogger(), logging.getLogger("uvicorn"),
               logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")]
    for lg in loggers:
        for h in lg.handlers:
            if not any(isinstance(f, TokenMaskFilter) for f in h.filters):
                h.addFilter(flt)
