import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


def _derive_key(raw: str) -> bytes:
    value = raw.strip()
    if len(value) == 64:
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    return hashlib.sha256(value.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    key = _derive_key(settings.credentials_encryption_key)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str:
    key = _derive_key(settings.credentials_encryption_key)
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
