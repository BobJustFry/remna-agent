#!/usr/bin/env python3
"""Lightweight Remna node agent — stdlib only. Serves /health and /metrics."""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "0.1.15"
STARTED_AT = time.time()
_LAST_CPU = None
_REMNA_VER_CACHE: tuple[float, bool, str | None] | None = None
_REMNA_VER_TTL = 60.0
_WARP_CACHE: tuple[float, dict] | None = None
_WARP_TTL = 60.0
_HAPROXY_CACHE: tuple[float, dict] | None = None
_HAPROXY_TTL = 20.0
_PEERS_CACHE: tuple[float, dict] | None = None
_PEERS_TTL = 8.0
_PROXY_PROC = ("rw-core", "xray", "haproxy")
_WARP_HANDSHAKE_MAX = 180
_WARP_EGRESS_TTL = 180.0
_SEMVER_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+][\w.]+)?)$", re.I)
_WARP_IFACES = ("warp", "CloudflareWARP", "WARP")
_WARP_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
_egress_lock = threading.Lock()
_egress_state: dict = {"ok": None, "at": 0.0, "busy": False, "iface": None}
# 1.1.1.1, not cp.cloudflare.com DNS: that name often lands on 104.16.x with a
# distant anycast PoP (Adman NSK → ~500 ms). 1.1.1.1 is the same generate_204
# with Host set, usually the nearest Cloudflare (DME from ru-hy2).
_CF204_IP = "1.1.1.1"
_CF204_HOST = "cp.cloudflare.com"
_CF204_PATH = "/generate_204"
_CF204_INTERVAL = 20.0
_cf204_lock = threading.Lock()
_cf204_state: dict = {"ok": None, "ms": None, "at": 0.0}


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
# rspack 3.3.x: renderBox("Remnawave Node v".concat("3.3.2"), ...)
_RW_CONCAT_RE = re.compile(
    r"""Remnawave Node v["']\.concat\(["'](\d+\.\d+\.\d+(?:[-+][\w.]+)?)["']\)""",
    re.I,
)
_RW_NODEVERSION_RE = re.compile(
    r"""nodeVersion\s*=\s*["'](\d+\.\d+\.\d+(?:[-+][\w.]+)?)["']?"""
)


def _version_from_rwnode_banner(text: str | None) -> str | None:
    """Parse version from Remnawave Node start banner / baked __RWNODE_VERSION__.

    Source: remnawave/node rspack DefinePlugin(__RWNODE_VERSION__) + get-start-message.ts
    → «Remnawave Node v{version}», or after minify: «Remnawave Node v».concat("3.3.2").
    """
    if not text:
        return None
    for rx in (_RW_BANNER_RE, _RW_CONCAT_RE):
        m = rx.search(text)
        if m:
            ver = _normalize_ver(m.group(1))
            if ver:
                return ver
    for m in _RW_NODEVERSION_RE.finditer(text):
        ver = _normalize_ver(m.group(1))
        if ver:
            return ver
    return None


def _version_from_container() -> str | None:
    """Read version the way Remnawave Node itself exposes it."""
    # 1) Banner / concat / nodeVersion inside dist/main.js
    out = _docker_out(
        [
            "docker",
            "exec",
            "remnanode",
            "sh",
            "-c",
            "grep -aoE 'Remnawave Node v.{0,80}' /opt/app/dist/main.js 2>/dev/null; "
            "grep -aoE 'nodeVersion=.?[0-9]+\\.[0-9]+\\.[0-9]+[-+[:alnum:].]*' "
            "/opt/app/dist/main.js 2>/dev/null | head -20",
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


def _run_cmd(args: list[str], timeout: float = 2.0) -> str | None:
    try:
        out = subprocess.check_output(
            args,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _which(name: str) -> str | None:
    extra = ("/usr/local/bin", "/usr/bin", "/bin", "/opt/warp-wgcf")
    dirs = list(os.environ.get("PATH", "").split(":")) + list(extra)
    seen: set[str] = set()
    for d in dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _iface_up(name: str) -> bool:
    oper = Path(f"/sys/class/net/{name}/operstate")
    try:
        state = oper.read_text(encoding="utf-8").strip().lower()
        if state == "up":
            return True
        if state == "unknown":
            # WireGuard often reports unknown; treat carrier/flags as up.
            flags = Path(f"/sys/class/net/{name}/flags")
            raw = int(flags.read_text(encoding="utf-8").strip(), 0)
            return bool(raw & 0x1)  # IFF_UP
    except (OSError, ValueError):
        pass
    return False


def _iface_ipv4(name: str) -> str | None:
    out = _run_cmd(["ip", "-4", "-o", "addr", "show", "dev", name])
    if not out:
        return None
    m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def _handshake_age_sec(iface: str) -> int | None:
    """Seconds since last WireGuard handshake, or None if never."""
    out = _run_cmd(["wg", "show", iface, "latest-handshakes"])
    if not out:
        return None
    now = int(time.time())
    best: int | None = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            ts = int(parts[-1])
        except ValueError:
            continue
        if ts <= 0:
            continue
        age = max(0, now - ts)
        if best is None or age < best:
            best = age
    return best


def _egress_worker(iface: str) -> None:
    curl = _which("curl")
    ok = False
    if curl:
        out = _run_cmd(
            [
                curl,
                "-4",
                "--interface",
                iface,
                "-sS",
                "--max-time",
                "8",
                _WARP_TRACE_URL,
            ],
            timeout=12.0,
        )
        if out and ("warp=on" in out or ("colo=" in out and "ip=" in out)):
            ok = True
    with _egress_lock:
        _egress_state["ok"] = ok
        _egress_state["at"] = time.time()
        _egress_state["busy"] = False
        _egress_state["iface"] = iface


def _kick_egress(iface: str) -> bool | None:
    """Return last egress result; refresh at most every _WARP_EGRESS_TTL seconds."""
    with _egress_lock:
        now = time.time()
        if _egress_state["iface"] != iface:
            _egress_state["ok"] = None
            _egress_state["at"] = 0.0
        age = now - float(_egress_state["at"] or 0)
        result = _egress_state["ok"]
        stale = result is None or age >= _WARP_EGRESS_TTL
        if not stale or _egress_state["busy"]:
            return result if isinstance(result, bool) else None
        _egress_state["busy"] = True
    threading.Thread(target=_egress_worker, args=(iface,), daemon=True, name="warp-egress").start()
    return result if isinstance(result, bool) else None


def _version_from_go_binary(bin_path: str) -> str | None:
    """wgcf has no --version; read Go build info embedded in the binary."""
    try:
        data = Path(bin_path).read_bytes()
    except OSError:
        return None
    m = re.search(rb"github\.com/ViRb3/wgcf/v2\tv(\d+\.\d+\.\d+)", data)
    if m:
        return m.group(1).decode("ascii")
    m = re.search(rb"ViRb3/wgcf[^\x00]{0,32}v(\d+\.\d+\.\d+)", data)
    if m:
        return m.group(1).decode("ascii")
    return None


def _tool_version(bin_path: str) -> str | None:
    for args in ([bin_path, "version"], [bin_path, "--version"], [bin_path, "-V"]):
        out = _run_cmd(args)
        if not out:
            continue
        for line in out.splitlines():
            if "unknown" in line.lower() or "error:" in line.lower() or line.lower().startswith("usage:"):
                continue
            m = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][\w.]+)?)\b", line)
            if m:
                return m.group(1)
    return _version_from_go_binary(bin_path)


def _read_stamp_version(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", raw)
    return m.group(1) if m else None


def _warp_health(iface: str | None, *, present: bool, up: bool) -> tuple[bool, int | None, bool | None]:
    handshake_sec = _handshake_age_sec(iface) if iface and up else None
    egress_ok = _kick_egress(iface) if iface and up else None
    handshake_ok = handshake_sec is not None and handshake_sec <= _WARP_HANDSHAKE_MAX
    if not present or not up:
        healthy = False
    elif egress_ok is False and not handshake_ok:
        healthy = False
    else:
        healthy = handshake_ok or egress_ok is True
    return healthy, handshake_sec, egress_ok


def warp_info() -> dict:
    """Detect host WARP interface used by Xray sockopt.interface (freedom → warp)."""
    global _WARP_CACHE
    now = time.time()
    if _WARP_CACHE is not None and (now - _WARP_CACHE[0]) < _WARP_TTL:
        return _WARP_CACHE[1]

    iface: str | None = None
    for name in _WARP_IFACES:
        if Path(f"/sys/class/net/{name}").is_dir():
            iface = name
            break

    present = iface is not None
    up = _iface_up(iface) if iface else False
    ipv4 = _iface_ipv4(iface) if iface and up else None
    healthy, handshake_sec, egress_ok = _warp_health(iface, present=present, up=up)

    wgcf_bin = next(
        (
            str(p)
            for p in (
                Path("/usr/local/bin/wgcf"),
                Path("/usr/bin/wgcf"),
                Path("/opt/warp-wgcf/wgcf"),
            )
            if p.is_file()
        ),
        _which("wgcf"),
    )
    warp_cli = _which("warp-cli")
    warp_go = _which("warp-go")
    stamp = _read_stamp_version(Path("/opt/warp-wgcf/version"))

    method: str | None = None
    version: str | None = stamp

    # Prefer wgcf when iface is the wg-quick name our installer uses.
    if iface in ("warp", "WARP") or Path("/etc/wireguard/warp.conf").is_file() or wgcf_bin:
        method = "wgcf"
        if not version and wgcf_bin:
            version = _tool_version(wgcf_bin)
    elif iface == "CloudflareWARP" or warp_cli or Path("/lib/systemd/system/warp-svc.service").is_file():
        method = "warp-cli"
        if not version and warp_cli:
            version = _tool_version(warp_cli)
    elif warp_go:
        method = "warp-go"
        if not version:
            version = _tool_version(warp_go)
    elif present:
        method = "wireguard"

    data = {
        "warp_present": present,
        "warp_up": up if present else False,
        "warp_healthy": healthy if present else False,
        "warp_handshake_sec": handshake_sec,
        "warp_egress_ok": egress_ok,
        "warp_interface": iface,
        "warp_method": method,
        "warp_version": version,
        "warp_ipv4": ipv4,
    }
    _WARP_CACHE = (now, data)
    return data


def haproxy_info() -> dict:
    """Host HAProxy: package/binary, systemd active, listen addresses."""
    global _HAPROXY_CACHE
    now = time.time()
    if _HAPROXY_CACHE is not None and (now - _HAPROXY_CACHE[0]) < _HAPROXY_TTL:
        return _HAPROXY_CACHE[1]

    bin_path = _which("haproxy")
    if not bin_path and Path("/usr/sbin/haproxy").is_file():
        bin_path = "/usr/sbin/haproxy"
    present = bool(bin_path) or Path("/etc/haproxy/haproxy.cfg").is_file()
    up = (_run_cmd(["systemctl", "is-active", "haproxy"]) or "") == "active"

    version = None
    if bin_path:
        raw = _run_cmd([bin_path, "-v"]) or ""
        m = re.search(r"version\s+(\S+)", raw, re.I)
        version = m.group(1) if m else None

    listen: list[str] = []
    ss_out = _run_cmd(["ss", "-lntp"]) or ""
    for line in ss_out.splitlines():
        if "haproxy" not in line:
            continue
        for m in re.finditer(r"(\S+:\d+)\s", line):
            addr = m.group(1)
            if addr not in listen:
                listen.append(addr)

    data = {
        "haproxy_present": present,
        "haproxy_up": up if present else False,
        "haproxy_version": version,
        "haproxy_listen": ",".join(listen) if listen else None,
    }
    _HAPROXY_CACHE = (now, data)
    return data


def _ss_host_port(token: str) -> tuple[str, int] | None:
    token = (token or "").strip()
    if not token or token in (".", "*"):
        return None
    if token.startswith("["):
        end = token.find("]")
        if end < 0:
            return None
        host = token[1:end]
        rest = token[end + 1 :]
        if not rest.startswith(":"):
            return None
        try:
            return host, int(rest[1:])
        except ValueError:
            return None
    host, sep, port_s = token.rpartition(":")
    if not sep:
        return None
    try:
        return host, int(port_s)
    except ValueError:
        return None


def _norm_ip(host: str) -> str:
    h = host.strip().lower()
    if h.startswith("::ffff:"):
        h = h[7:]
    return h.strip("[]")


def _is_loopback_ip(host: str) -> bool:
    h = _norm_ip(host)
    return h in ("127.0.0.1", "::1", "localhost")


def _addr_tokens(parts: list[str]) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for token in parts:
        hp = _ss_host_port(token)
        if hp:
            found.append(hp)
    return found


def _proxy_listen_ports(ss_lnp: str) -> set[int]:
    ports: set[int] = set()
    for line in ss_lnp.splitlines():
        if not any(name in line for name in _PROXY_PROC):
            continue
        addrs = _addr_tokens(line.split())
        if not addrs:
            continue
        host, port = addrs[0]
        if _is_loopback_ip(host):
            continue
        ports.add(port)
    return ports


def peers_info() -> dict:
    """Unique remote IPs on public listen ports of rw-core / xray / haproxy.

    Many TCP from one address count as one peer. Loopback (steal dest, origin
    :10087) is skipped. Not Remnawave UUID users.
    """
    global _PEERS_CACHE
    now = time.time()
    if _PEERS_CACHE is not None and (now - _PEERS_CACHE[0]) < _PEERS_TTL:
        return _PEERS_CACHE[1]

    empty = {"proxy_peers": None, "proxy_conns": None}
    lnp = _run_cmd(["ss", "-tlnp"], timeout=2.0)
    if not lnp:
        if _PEERS_CACHE is not None:
            return _PEERS_CACHE[1]
        _PEERS_CACHE = (now, empty)
        return empty
    ports = _proxy_listen_ports(lnp)
    if not ports:
        data = {"proxy_peers": 0, "proxy_conns": 0}
        _PEERS_CACHE = (now, data)
        return data

    est = _run_cmd(["ss", "-tn", "state", "established"], timeout=2.0)
    if not est:
        if _PEERS_CACHE is not None:
            return _PEERS_CACHE[1]
        _PEERS_CACHE = (now, empty)
        return empty

    ips: set[str] = set()
    conns = 0
    for line in est.splitlines():
        addrs = _addr_tokens(line.split())
        if len(addrs) < 2:
            continue
        local, peer = addrs[0], addrs[1]
        if local[1] not in ports:
            continue
        if _is_loopback_ip(peer[0]):
            continue
        ips.add(_norm_ip(peer[0]))
        conns += 1

    data = {"proxy_peers": len(ips), "proxy_conns": conns}
    _PEERS_CACHE = (now, data)
    return data


def _cf204_probe() -> tuple[bool, float | None]:
    t0 = time.perf_counter()
    try:
        conn = http.client.HTTPConnection(_CF204_IP, 80, timeout=3.0)
        try:
            conn.request("GET", _CF204_PATH, headers={"Host": _CF204_HOST})
            resp = conn.getresponse()
            code = int(resp.status)
            resp.read()
            ms = round((time.perf_counter() - t0) * 1000.0, 1)
            return code in (200, 204), ms
        finally:
            conn.close()
    except Exception:
        return False, None


def _cf204_loop() -> None:
    while True:
        ok, ms = _cf204_probe()
        with _cf204_lock:
            _cf204_state["ok"] = ok
            _cf204_state["ms"] = ms
            _cf204_state["at"] = time.time()
        time.sleep(_CF204_INTERVAL)


def cf204_info() -> dict:
    with _cf204_lock:
        return {
            "cf204_ok": _cf204_state["ok"],
            "cf204_ms": _cf204_state["ms"],
        }


def start_cf204_loop() -> None:
    ok, ms = _cf204_probe()
    with _cf204_lock:
        _cf204_state["ok"] = ok
        _cf204_state["ms"] = ms
        _cf204_state["at"] = time.time()
    threading.Thread(target=_cf204_loop, daemon=True, name="cf204").start()


def collect_metrics() -> dict:
    mem_total, mem_used, mem_percent = mem_stats()
    disk_total, disk_used, disk_percent = disk_stats("/")
    rn_running, rn_version = remnanode_info()
    warp = warp_info()
    haproxy = haproxy_info()
    peers = peers_info()
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
        **warp,
        **haproxy,
        **peers,
        **cf204_info(),
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

    start_cf204_loop()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.agent_token = token  # type: ignore[attr-defined]
    print(f"remna-node-agent {VERSION} on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
