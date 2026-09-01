"""Login throttling.

OWASP Authentication Cheat Sheet: throttle / lock out brute-force
(https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html).
Generic 429 — do not distinguish unknown user vs locked account.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import HTTPException, status

FAIL_LIMIT = 8
WINDOW_SEC = 15 * 60
LOCK_SEC = 15 * 60


@dataclass
class _Bucket:
    fails: int = 0
    window_start: float = 0.0
    locked_until: float = 0.0


_buckets: dict[str, _Bucket] = {}


def _now() -> float:
    return time.monotonic()


def _purge(now: float) -> None:
    drop = [
        key
        for key, bucket in _buckets.items()
        if bucket.locked_until < now and now - bucket.window_start > WINDOW_SEC
    ]
    for key in drop:
        del _buckets[key]


def _keys(ip: str, username: str) -> list[str]:
    user = username.strip().lower() or "-"
    return [f"ip:{ip}", f"u:{user}"]


def assert_login_allowed(ip: str, username: str) -> None:
    now = _now()
    _purge(now)
    for key in _keys(ip, username):
        bucket = _buckets.get(key)
        if bucket and bucket.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много попыток входа. Подождите и попробуйте снова.",
            )


def register_login_failure(ip: str, username: str) -> None:
    now = _now()
    for key in _keys(ip, username):
        bucket = _buckets.get(key)
        if bucket is None or now - bucket.window_start > WINDOW_SEC:
            bucket = _Bucket(fails=0, window_start=now)
            _buckets[key] = bucket
        bucket.fails += 1
        if bucket.fails >= FAIL_LIMIT:
            bucket.locked_until = now + LOCK_SEC


def register_login_success(ip: str, username: str) -> None:
    for key in _keys(ip, username):
        _buckets.pop(key, None)
