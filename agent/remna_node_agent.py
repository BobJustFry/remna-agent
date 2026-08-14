#!/usr/bin/env python3
"""Lightweight Remna node agent — stdlib only. Serves /health and /metrics."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "0.1.4"
STARTED_AT = time.time()
_LAST_CPU = None
_REMNA_VER_CACHE: tuple[float, bool, str | None] | None = None
_REMNA_VER_TTL = 60.0
_SEMVER_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+][\w.]+)?)$", re.I)


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def read_token() -> str:
    token = env("REMNA_AGENT_TOKEN", "")
    if token:
        return token.strip()
    token_file = env("REMNA_AGENT_TOKEN_FILE")
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text(encoding="utf-8").strip()
    return ""


def cpu_percent() -> float | None:
    global _LAST_CPU
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            parts = f.readline().split()
        # user nice system idle iowait irq softirq steal
        nums = [int(x) for x in parts[1:8]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        total = sum(nums)
        if _LAST_CPU is None:
            _LAST_CPU = (idle, total)
            time.sleep(0.12)
            return cpu_percent()
        prev_idle, prev_total = _LAST_CPU
        _LAST_CPU = (idle, total)
        d_total = total - prev_total
        d_idle = idle - prev_idle
        if d_total <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0)), 1)
    except Exception:
        return None


def mem_stats() -> tuple[float | None, float | None, float | None]:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = int(value.strip().split()[0])  # kB
        total = info.get("MemTotal")
        available = info.get("MemAvailable")
        if total is None:
            return None, None, None
        if available is None:
            free = info.get("MemFree", 0)
            buffers = info.get("Buffers", 0)
            cached = info.get("Cached", 0)
            available = free + buffers + cached
        used = max(0, total - available)
        total_mb = round(total / 1024, 1)
        used_mb = round(used / 1024, 1)
        percent = round(used / total * 100.0, 1) if total else 0.0
        return total_mb, used_mb, percent
    except Exception:
        return None, None, None


def disk_stats(path: str = "/") -> tuple[float | None, float | None, float | None]:
    try:
        st = os.statvfs(path)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = max(0, total - free)
        if total <= 0:
            return None, None, None
        return (
            round(total / (1024**3), 2),
            round(used / (1024**3), 2),
            round(used / total * 100.0, 1),
        )
    except Exception:
        return None, None, None


def loadavg() -> list[float] | None:
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            parts = f.read().split()[:3]
        return [round(float(x), 2) for x in parts]
    except Exception:
        return None


def _normalize_ver(raw: str | None, *, allow_latest: bool = False) -> str | None:
    if not raw:
        return None
    v = raw.strip()
    if not v or v in ("<no value>", "<none>", "null"):
        return None
    if v.lower().startswith("v") and len(v) > 1 and v[1].isdigit():
        v = v[1:]
    if v.lower() in ("latest", "dev"):
        return "latest" if allow_latest and v.lower() == "latest" else None
    # Placeholders / junk from minified bundles
    if v in ("0.0.0", "0.0.1"):
        return None
    return v or None


def _semver_from_tag(tag: str | None, *, from_image_tag: bool = False) -> str | None:
    if not tag:
        return None
    tag = tag.strip()
    # remnawave/node:3.1.1 or just 3.1.1
    if "/" in tag and ":" in tag:
        tag = tag.rsplit(":", 1)[-1]
    elif tag.startswith("sha256:"):
        return None
    m = _SEMVER_RE.match(tag)
    if not m:
        return None
    ver = _normalize_ver(m.group(1))
    if ver in ("0.0.0", "0.0.1"):
        return None
    # 1.0.0 from random grep is useless; allow only if it came from an image tag name
    if ver == "1.0.0" and not from_image_tag:
        return None
    return ver


def _docker_out(
    args: list[str],
    *,
    timeout: float = 5.0,
    merge_stderr: bool = False,
) -> str | None:
    try:
        out = subprocess.check_output(
            args,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
        return out.strip() or None
    except Exception:
        return None


def _docker_inspect(fmt: str, target: str = "remnanode") -> str | None:
    return _docker_out(["docker", "inspect", "-f", fmt, target])


def _version_from_repo_tags(image_ref: str) -> str | None:
    raw = _docker_out(["docker", "image", "inspect", "-f", "{{json .RepoTags}}", image_ref])
    if not raw:
        return None
    try:
        tags = json.loads(raw)
    except Exception:
        return None
    if not isinstance(tags, list):
        return None
    found: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        ver = _semver_from_tag(tag, from_image_tag=True)
        if ver:
            found.append(ver)
    if not found:
        return None
    # Prefer highest-looking semver string (lexicographic ok for x.y.z)
    found.sort(key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", s)])
    return found[-1]


def _version_from_hub_digest(digest_ref: str) -> str | None:
    """Map remnawave/node@sha256:… to a version tag via Docker Hub."""
    if "@" not in digest_ref:
        return None
    digest = digest_ref.split("@", 1)[1].strip()
    if not digest.startswith("sha256:"):
        return None
    url = (
        "https://hub.docker.com/v2/repositories/remnawave/node/tags/"
        f"?page_size=40&ordering=-last_updated"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "remna-node-agent"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        ver = _semver_from_tag(name if isinstance(name, str) else None, from_image_tag=True)
        if not ver:
            continue
        images = item.get("images") or []
        if not isinstance(images, list):
            continue
        for img in images:
            if isinstance(img, dict) and img.get("digest") == digest:
                return ver
        # Some Hub responses put digest on the tag object itself
        if item.get("digest") == digest:
            return ver
    return None


_RW_BANNER_RE = re.compile(r"Remnawave Node v(\d+\.\d+\.\d+(?:[-+][\w.]+)?)", re.I)


def _version_from_rwnode_banner(text: str | None) -> str | None:
    """Parse version from Remnawave Node start banner / baked __RWNODE_VERSION__.

    Source: remnawave/node rspack DefinePlugin(__RWNODE_VERSION__) + get-start-message.ts
    → renders «Remnawave Node v{package.json version}».
    """
    if not text:
        return None
    m = _RW_BANNER_RE.search(text)
    if not m:
        return None
    return _normalize_ver(m.group(1))


def _version_from_container() -> str | None:
    """Read version the way Remnawave Node itself exposes it."""
    # 1) Baked banner string inside dist/main.js (compile-time __RWNODE_VERSION__)
    out = _docker_out(
        [
            "docker",
            "exec",
            "remnanode",
            "sh",
            "-c",
            "grep -aoE 'Remnawave Node v[0-9]+\\.[0-9]+\\.[0-9]+[-+[:alnum:].]*' "
            "/opt/app/dist/main.js 2>/dev/null | head -1",
        ],
        timeout=8,
    )
    ver = _version_from_rwnode_banner(out)
    if ver:
        return ver

    # 2) Startup logs (same banner from getStartMessage; docker logs → stderr)
    out = _docker_out(
        ["docker", "logs", "--tail", "300", "remnanode"],
        timeout=8,
        merge_stderr=True,
    )
    ver = _version_from_rwnode_banner(out)
    if ver:
        return ver

    return None


def remnanode_info() -> tuple[bool, str | None]:
    """Return (running, version) for the remnanode container."""
    global _REMNA_VER_CACHE
    now = time.time()
    if _REMNA_VER_CACHE is not None and (now - _REMNA_VER_CACHE[0]) < _REMNA_VER_TTL:
        return _REMNA_VER_CACHE[1], _REMNA_VER_CACHE[2]

    running = False
    version: str | None = None
    try:
        state = _docker_inspect("{{.State.Running}}")
        if state is None:
            _REMNA_VER_CACHE = (now, False, None)
            return False, None
        running = state.lower() == "true"
        image_ref = _docker_inspect("{{.Image}}") or ""
        config_image = _docker_inspect("{{.Config.Image}}") or ""

        # 1) Official source: __RWNODE_VERSION__ from package.json (see remnawave/node
        #    rspack.config.mjs + get-start-message.ts → «Remnawave Node vX.Y.Z»).
        #    Docker tag :latest is NOT the product version.
        if running:
            version = _version_from_container()

        # 2) Fallbacks if banner/logs unavailable
        if not version and image_ref:
            version = _version_from_repo_tags(image_ref)
        if not version and config_image:
            version = _semver_from_tag(config_image, from_image_tag=True)
        if not version and image_ref:
            digests_raw = _docker_out(
                ["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", image_ref]
            )
            digests: list[str] = []
            if digests_raw:
                try:
                    parsed = json.loads(digests_raw)
                    if isinstance(parsed, list):
                        digests = [x for x in parsed if isinstance(x, str)]
                except Exception:
                    digests = []
            for dref in digests:
                if "remnawave/node" in dref:
                    version = _version_from_hub_digest(dref)
                    if version:
                        break
            if not version:
                for dref in digests:
                    version = _version_from_hub_digest(dref)
                    if version:
                        break
    except Exception:
        running = False
        version = None

    # Never report bare "latest" — image tag, not Remnawave Node version
    if version == "latest":
        version = None

    _REMNA_VER_CACHE = (now, running, version)
    return running, version


def collect_metrics() -> dict:
    mem_total, mem_used, mem_percent = mem_stats()
    disk_total, disk_used, disk_percent = disk_stats("/")
    rn_running, rn_version = remnanode_info()
    return {
        "version": VERSION,
        "uptime_sec": int(time.time() - STARTED_AT),
        "cpu_percent": cpu_percent(),
        "mem_total_mb": mem_total,
        "mem_used_mb": mem_used,
        "mem_percent": mem_percent,
        "disk_total_gb": disk_total,
        "disk_used_gb": disk_used,
        "disk_percent": disk_percent,
        "loadavg": loadavg(),
        "remnanode_running": rn_running,
        "remnanode_version": rn_version,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"RemnaNodeAgent/{VERSION}"

    def log_message(self, fmt: str, *args) -> None:  # quieter
        return

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = getattr(self.server, "agent_token", "")  # type: ignore[attr-defined]
        if not expected:
            return False
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {expected}":
            return True
        return self.headers.get("X-Remna-Token", "") == expected

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"ok": True, "version": VERSION})
            return
        if path == "/metrics":
            if not self._authorized():
                self._send(401, {"ok": False, "error": "unauthorized"})
                return
            self._send(200, collect_metrics())
            return
        self._send(404, {"ok": False, "error": "not found"})


def main() -> None:
    host = env("REMNA_AGENT_HOST", "0.0.0.0") or "0.0.0.0"
    port = int(env("REMNA_AGENT_PORT", "7422") or "7422")
    token = read_token()
    if not token:
        raise SystemExit("REMNA_AGENT_TOKEN is required")

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.agent_token = token  # type: ignore[attr-defined]
    print(f"remna-node-agent {VERSION} on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
