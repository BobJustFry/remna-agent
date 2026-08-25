"""Cached latest wgcf release from GitHub (ViRb3/wgcf)."""

from __future__ import annotations

import asyncio
import os
import platform
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from app.services.remnawave_releases import normalize_version

WGCF_RELEASES_URL = "https://api.github.com/repos/ViRb3/wgcf/releases/latest"
WGCF_FALLBACK_VERSION = "2.2.32"
CACHE_TTL_SEC = 30 * 60
_HTTP_TIMEOUT = 12.0
_DL_TIMEOUT = 60.0
_MIN_WGCF_BYTES = 50_000

_cache_ver: str | None = None
_cache_at: float = 0.0
_lock = asyncio.Lock()
_bin_lock = threading.Lock()
_reg_lock = threading.Lock()


async def get_latest_wgcf_version(*, force: bool = False) -> str | None:
    global _cache_ver, _cache_at
    now = time.time()
    if not force and _cache_ver and (now - _cache_at) < CACHE_TTL_SEC:
        return _cache_ver

    async with _lock:
        now = time.time()
        if not force and _cache_ver and (now - _cache_at) < CACHE_TTL_SEC:
            return _cache_ver
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(
                    WGCF_RELEASES_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "remna-agent",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            ver = normalize_version(data.get("tag_name") or data.get("name"))
            if ver:
                _cache_ver = ver
                _cache_at = time.time()
            return _cache_ver
        except Exception:  # noqa: BLE001
            return _cache_ver


def goarch_from_uname(raw: str) -> str | None:
    t = (raw or "").strip().lower()
    if t in ("x86_64", "amd64"):
        return "amd64"
    if t in ("aarch64", "arm64"):
        return "arm64"
    return None


def wgcf_cache_dir() -> Path:
    env = (os.environ.get("WGCF_CACHE_DIR") or "").strip()
    if env:
        return Path(env)
    docker = Path("/var/cache/remna-wgcf")
    if docker.is_dir() or docker.parent.is_dir():
        return docker
    return Path("/tmp/remna-wgcf-cache")


def _cache_path(version: str, goarch: str) -> Path:
    ver = version.lstrip("v")
    return wgcf_cache_dir() / f"wgcf_{ver}_linux_{goarch}"


def fetch_wgcf_binary(version: str, goarch: str) -> bytes:
    ver = version.lstrip("v")
    url = f"https://github.com/ViRb3/wgcf/releases/download/v{ver}/wgcf_{ver}_linux_{goarch}"
    with httpx.Client(timeout=_DL_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "remna-agent"})
        resp.raise_for_status()
        data = resp.content
    if len(data) < _MIN_WGCF_BYTES:
        raise RuntimeError(f"wgcf слишком маленький ({len(data)} байт) с {url}")
    return data


def get_wgcf_binary(version: str, goarch: str) -> tuple[bytes, bool]:
    """Return (blob, from_cache). Downloads from GitHub only on cache miss."""
    path = _cache_path(version, goarch)
    with _bin_lock:
        if path.is_file() and path.stat().st_size >= _MIN_WGCF_BYTES:
            return path.read_bytes(), True
        blob = fetch_wgcf_binary(version, goarch)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.tmp"
        tmp.write_bytes(blob)
        tmp.replace(path)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return blob, False


def panel_goarch() -> str:
    return goarch_from_uname(platform.machine()) or "amd64"


def register_wgcf_account(version: str) -> tuple[bytes, bytes]:
    """Register a fresh WARP account on the panel (not on the node).

    Returns (wgcf-account.toml, wgcf-profile.conf). Used when the node cannot
    reach api.cloudflareclient.com (typical for RU VPS).
    """
    arch = panel_goarch()
    blob, _cached = get_wgcf_binary(version, arch)
    exe = _cache_path(version, arch)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not exe.is_file() or exe.stat().st_size != len(blob):
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(blob)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with _reg_lock:
        with tempfile.TemporaryDirectory(prefix="wgcf-reg-") as td:
            cwd = Path(td)
            try:
                proc = subprocess.run(
                    [str(exe), "register", "--accept-tos"],
                    cwd=cwd,
                    timeout=70,
                    capture_output=True,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("таймаут wgcf register на панели") from exc
            except OSError as exc:
                raise RuntimeError(f"не запустить wgcf на панели ({arch}): {exc}") from exc
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[-400:]
                raise RuntimeError(err.strip() or f"wgcf register exit {proc.returncode}")
            account = cwd / "wgcf-account.toml"
            if not account.is_file() or account.stat().st_size < 40:
                raise RuntimeError("wgcf-account.toml не появился после register")
            proc2 = subprocess.run(
                [str(exe), "generate"],
                cwd=cwd,
                timeout=30,
                capture_output=True,
                check=False,
            )
            profile = cwd / "wgcf-profile.conf"
            if proc2.returncode != 0 or not profile.is_file():
                err = (proc2.stderr or proc2.stdout or b"").decode("utf-8", "replace")[-300:]
                raise RuntimeError(err.strip() or "wgcf generate на панели не удался")
            return account.read_bytes(), profile.read_bytes()
