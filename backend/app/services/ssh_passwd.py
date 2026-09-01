"""Detect and drive PAM/passwd when a login password is expired."""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field

_EXPIRED = (
    "password has expired",
    "password change required",
    "you must change your password",
    "must change your password now",
)

_SUCCESS = (
    "password updated successfully",
    "password has been changed",
    "all authentication tokens updated",
    "passwd: password updated",
    "passwd: password changed",
    "please login again",
)

_REJECT_NEW = (
    "you must choose a different",
    "password unchanged",
    "bad password",
    "too similar",
    "too simple",
    "too short",
    "dictionary",
    "already used",
    "same as the old",
    "matches previous",
    "based on a dictionary",
    "not changed",
    "palindrome",
    "way too similar",
)

_FATAL = (
    "authentication token manipulation error",
    "authentication token failure",
    "passwd: authentication token",
    "no tty available",
    "password change required but no tty",
)


def looks_like_password_expired(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _EXPIRED)


def generate_login_password(length: int = 20) -> str:
    """Meet typical pam_pwquality: mixed classes, no shell-hostile chars."""
    alphabet = string.ascii_letters + string.digits + "!@#%^*_+-="
    if length < 12:
        length = 12
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in "!@#%^*_+-=" for c in pw)
        ):
            return pw


def _is_retype_prompt(low: str) -> bool:
    return any(
        token in low
        for token in ("retype", "re-enter", "reenter", "again", "confirm")
    ) and "password" in low


def _is_new_prompt(low: str) -> bool:
    if _is_retype_prompt(low):
        return False
    return "new password" in low or "new unix password" in low or "enter new" in low


def _is_current_prompt(low: str) -> bool:
    return (
        "current password" in low
        or "current unix password" in low
        or "(current)" in low
        or "old password" in low
    )


def _has_any(low: str, phrases: tuple[str, ...]) -> bool:
    return any(p in low for p in phrases)


@dataclass
class PasswdDialog:
    current_password: str
    generated_password: str
    new_password: str = ""
    sent_current: bool = False
    new_sends: int = 0
    success: bool = False
    failed: str | None = None
    switched_to_generated: bool = False
    expired_seen: bool = False
    _reject_hits: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.new_password:
            self.new_password = self.current_password

    def consume(self, buf: str) -> tuple[str | None, str]:
        """Feed terminal output. Returns (password to send or None, leftover buffer)."""
        if self.success or self.failed:
            return None, buf

        low = buf.lower()
        if looks_like_password_expired(buf):
            self.expired_seen = True

        if _has_any(low, _SUCCESS) and self.new_sends >= 1:
            self.success = True
            return None, buf

        if self.expired_seen and "no tty available" in low and not self.sent_current:
            self.failed = (
                "Сервер требует смену пароля, но TTY так и не появился. "
                "Зайдите вручную: ssh -t user@host"
            )
            return None, buf

        if _has_any(low, _FATAL) and "no tty" not in low:
            self.failed = _first_error_line(buf) or "passwd отклонил смену пароля"
            return None, buf

        if _has_any(low, _REJECT_NEW):
            self._reject_hits += 1
            if not self.switched_to_generated:
                self.switched_to_generated = True
                self.new_password = self.generated_password
                self.new_sends = 0
                buf = _strip_reject(buf)
                low = buf.lower()
            elif self.new_sends >= 1:
                self.failed = (
                    "Сервер отклонил и старый пароль, и новый. "
                    "Смените пароль вручную: ssh -t user@host"
                )
                return None, buf
            else:
                buf = _strip_reject(buf)
                low = buf.lower()

        if _is_retype_prompt(low) and self.new_sends == 1:
            self.new_sends = 2
            return self.new_password, ""

        if _is_new_prompt(low) and self.new_sends == 0:
            self.new_sends = 1
            return self.new_password, ""

        if _is_current_prompt(low) and not self.sent_current:
            self.sent_current = True
            self.expired_seen = True
            return self.current_password, ""

        return None, buf


_REJECT_LINE = re.compile(
    r"(?im)^.*(?:bad password|too similar|too short|dictionary|unchanged|"
    r"choose a different|already used|same as the old).*$"
)


def _strip_reject(buf: str) -> str:
    return _REJECT_LINE.sub("", buf)


def _first_error_line(buf: str) -> str:
    for line in buf.splitlines():
        stripped = line.strip()
        if stripped and not stripped.lower().startswith("warning:"):
            low = stripped.lower()
            if "password" in low or "passwd" in low or "token" in low:
                return stripped[:240]
    return ""
