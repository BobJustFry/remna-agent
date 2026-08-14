import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException

import puttykeys


def is_ppk(raw: str) -> bool:
    head = raw.lstrip()
    return head.startswith("PuTTY-User-Key-File-")


def ppk_version(raw: str) -> int | None:
    head = raw.lstrip().split("\n", 1)[0]
    if head.startswith("PuTTY-User-Key-File-3:"):
        return 3
    if head.startswith("PuTTY-User-Key-File-2:"):
        return 2
    if head.startswith("PuTTY-User-Key-File-"):
        return 1
    return None


def normalize_private_key(raw: str, passphrase: str | None = None) -> str:
    """Return OpenSSH/PEM private key. Converts PuTTY PPK (v2/v3) when needed."""
    text = raw.strip().replace("\r\n", "\n")
    if not text:
        raise HTTPException(status_code=400, detail="Пустой приватный ключ")

    if is_ppk(text):
        converted = _convert_ppk(text, passphrase)
        if not converted or "PRIVATE KEY" not in converted:
            raise HTTPException(status_code=400, detail="Не удалось конвертировать PPK в OpenSSH")
        return converted.strip() + "\n"

    if "PRIVATE KEY" not in text:
        raise HTTPException(
            status_code=400,
            detail="Ожидается OpenSSH/PEM ключ или файл PuTTY (.ppk)",
        )
    return text if text.endswith("\n") else text + "\n"


def _convert_ppk(text: str, passphrase: str | None) -> str:
    errors: list[str] = []

    try:
        return _convert_ppk_puttygen(text, passphrase)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"puttygen: {exc}")

    try:
        if passphrase:
            return puttykeys.ppkraw_to_openssh(text, passphrase)
        return puttykeys.ppkraw_to_openssh(text)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).strip() or "ошибка puttykeys"
        errors.append(f"puttykeys: {msg}")
        low = msg.lower()
        if "password" in low or "passphrase" in low or "decrypt" in low:
            raise HTTPException(
                status_code=400,
                detail="PPK защищён паролем или пароль неверный. Укажите passphrase ключа.",
            ) from exc

    ver = ppk_version(text)
    hint = " (PPK v3)" if ver == 3 else ""
    raise HTTPException(
        status_code=400,
        detail=f"Не удалось конвертировать PPK{hint}. {' | '.join(errors)}",
    )


def _convert_ppk_puttygen(text: str, passphrase: str | None) -> str:
    puttygen = shutil.which("puttygen")
    if not puttygen:
        raise RuntimeError("puttygen не установлен в контейнере")

    with tempfile.TemporaryDirectory(prefix="remna-ppk-") as tmp:
        ppk_path = Path(tmp) / "key.ppk"
        out_path = Path(tmp) / "key.pem"
        ppk_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

        cmd = [puttygen, str(ppk_path), "-O", "private-openssh", "-o", str(out_path)]
        if passphrase:
            pass_file = Path(tmp) / "pass.txt"
            pass_file.write_text(passphrase + "\n", encoding="utf-8")
            cmd.extend(["--old-passphrase", str(pass_file)])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not out_path.is_file():
            err = (proc.stderr or proc.stdout or "puttygen failed").strip()
            low = err.lower()
            if "passphrase" in low or "password" in low:
                raise RuntimeError("нужен passphrase для PPK")
            raise RuntimeError(err)

        converted = out_path.read_text(encoding="utf-8")
        if "PRIVATE KEY" not in converted:
            raise RuntimeError("puttygen вернул пустой/некорректный ключ")
        return converted
