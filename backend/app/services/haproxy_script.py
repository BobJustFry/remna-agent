"""Install and manage HAProxy on a node over SSH."""

from __future__ import annotations

import json
import re
import shlex
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.services.agent_install import (
    AgentInstallCancelled,
    AgentInstallError,
    Privilege,
    _check_cancel,
    _combined_text,
    _detect_privilege,
    _looks_like_shell_denied,
    _run,
    _run_priv,
    _shell_usable,
    _ssh_candidates,
    _yield_output,
)
from app.services.remnanode_script import RemnaScriptError, _stream_priv_command
from app.services.ssh_client import SshConnectError, probe_tcp, ssh_connect
from app.services.ssh_passwd import looks_like_password_expired

DOCKER_SCRIPTS_DIR = Path("/app/scripts/haproxy")
REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "haproxy"

HaproxyAction = Literal["install", "apply", "reload", "start", "stop", "uninstall"]
HaproxyTemplate = Literal["minimal", "front-xhttp", "tcp"]

_BACKEND_RE = re.compile(r"^[A-Za-z0-9._\[\]:-]+:\d{1,5}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
_CFG_MAX = 256 * 1024


class HaproxyScriptError(RemnaScriptError):
    pass


@dataclass
class HaproxyRoute:
    listen: int
    backend: str


@dataclass
class HaproxyParams:
    action: HaproxyAction = "install"
    force: bool = False
    template: HaproxyTemplate = "minimal"
    bind_port: int = 80
    backend: str = "127.0.0.1:10087"
    path_prefix: str = "/api/generate/"
    proxy_protocol: bool = True
    routes: list[HaproxyRoute] = field(default_factory=list)
    config: str | None = None


@dataclass
class HaproxyParsed:
    template: HaproxyTemplate | None = None
    bind_port: int | None = None
    backend: str | None = None
    path_prefix: str | None = None
    proxy_protocol: bool = False
    routes: list[HaproxyRoute] = field(default_factory=list)


@dataclass
class HaproxyStatus:
    installed: bool = False
    running: bool = False
    enabled: bool = False
    version: str | None = None
    config: str | None = None
    listen: list[str] = field(default_factory=list)
    valid: bool | None = None
    error: str | None = None
    parsed: HaproxyParsed | None = None


@dataclass
class HaproxyDiag:
    lines: list[str] = field(default_factory=list)
    error: str | None = None


def runner_path() -> Path:
    for base in (DOCKER_SCRIPTS_DIR, REPO_SCRIPTS_DIR):
        path = base / "haproxy_runner.sh"
        if path.is_file():
            return path
    raise HaproxyScriptError("Скрипт HAProxy не найден в образе/репозитории")


def normalize_haproxy_cfg(text: str) -> str:
    """HAProxy 2.2+ hard-fails without a trailing LF (upstream issue #704)."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        raise HaproxyScriptError("Пустой конфиг HAProxy")
    if not body.endswith("\n"):
        body += "\n"
    return body


_SECTION_RE = re.compile(r"^(frontend|backend|listen)\s+(\S+)", re.M)
_BIND_PORT_RE = re.compile(r"^\s*bind\s+\S*:(\d+)\b", re.M)
_SERVER_RE = re.compile(r"^\s*server\s+\S+\s+(\S+:\d+)\b", re.M)
_PATH_BEG_RE = re.compile(r"\bpath_beg\s+(\S+)")


_DEFAULT_BE_RE = re.compile(r"^\s*default_backend\s+(\S+)", re.M)


def parse_haproxy_cfg(text: str | None) -> HaproxyParsed:
    """Read template/port/backend/path/PROXY out of a live haproxy.cfg."""
    parsed = HaproxyParsed()
    if not text or not text.strip():
        return parsed
    parsed.proxy_protocol = bool(re.search(r"\bsend-proxy(?:-v2)?\b", text))
    path_m = _PATH_BEG_RE.search(text)
    if path_m:
        parsed.path_prefix = path_m.group(1)

    backends: dict[str, str] = {}
    frontends: list[tuple[int, str | None, str]] = []
    saw_http = False
    saw_tcp = False
    for m in _SECTION_RE.finditer(text):
        kind, name = m.group(1), m.group(2)
        start = m.end()
        nxt = _SECTION_RE.search(text, start)
        body = text[start : nxt.start() if nxt else None]
        lname = name.lower()
        if kind == "frontend" and ("stats" in lname or re.search(r"^\s*stats\s+enable", body, re.M)):
            continue
        if kind == "backend" and "camouflage" in lname:
            continue
        if kind == "backend":
            sm = _SERVER_RE.search(body)
            if sm:
                backends[name] = sm.group(1)
                if parsed.backend is None:
                    parsed.backend = sm.group(1)
            if "xhttp" in lname or "path_beg" in text:
                saw_http = True
            if lname.endswith("_tcp") or re.search(r"^\s*mode\s+tcp\b", body, re.M):
                saw_tcp = True
        if kind in ("frontend", "listen"):
            bm = _BIND_PORT_RE.search(body)
            be_name = None
            dm = _DEFAULT_BE_RE.search(body)
            if dm:
                be_name = dm.group(1)
            if bm:
                frontends.append((int(bm.group(1)), be_name, name))
            if re.search(r"^\s*mode\s+tcp\b", body, re.M) or lname.startswith("fe_tcp"):
                saw_tcp = True
            if (
                re.search(r"^\s*mode\s+http\b", body, re.M)
                or "path_beg" in body
                or lname.startswith("fe_http")
            ):
                saw_http = True

    routes: list[HaproxyRoute] = []
    for listen, be_name, _fe in frontends:
        backend = backends.get(be_name or "") or parsed.backend
        if backend:
            routes.append(HaproxyRoute(listen=listen, backend=backend))
    parsed.routes = routes
    if routes:
        parsed.bind_port = routes[0].listen
        parsed.backend = routes[0].backend
    if saw_http or parsed.path_prefix:
        parsed.template = "front-xhttp"
    elif saw_tcp:
        parsed.template = "tcp"
    else:
        parsed.template = "minimal"
    return parsed


def effective_routes(params: HaproxyParams) -> list[HaproxyRoute]:
    if params.routes:
        return params.routes
    return [HaproxyRoute(listen=params.bind_port, backend=params.backend.strip())]


def validate_params(params: HaproxyParams) -> None:
    if params.action == "uninstall":
        return
    if not 1 <= params.bind_port <= 65535:
        raise HaproxyScriptError("bind_port должен быть 1–65535")
    if params.template != "tcp" or not params.routes:
        if not _BACKEND_RE.match(params.backend.strip()):
            raise HaproxyScriptError("backend: ожидается host:port, например 127.0.0.1:10087")
    path = params.path_prefix.strip() or "/"
    if not _PATH_RE.match(path):
        raise HaproxyScriptError("path_prefix: только путь вида /api/generate/")
    if params.config is not None and len(params.config) > _CFG_MAX:
        raise HaproxyScriptError("Конфиг слишком большой")
    if params.routes:
        seen: set[int] = set()
        if len(params.routes) > 32:
            raise HaproxyScriptError("слишком много портов (макс. 32)")
        for route in params.routes:
            if not 1 <= route.listen <= 65535:
                raise HaproxyScriptError(f"listen {route.listen}: порт 1–65535")
            if route.listen in seen:
                raise HaproxyScriptError(f"порт {route.listen} указан дважды")
            seen.add(route.listen)
            if not _BACKEND_RE.match(route.backend.strip()):
                raise HaproxyScriptError(
                    f":{route.listen}: backend должен быть host:port, например 144.31.214.43:443"
                )


def render_haproxy_cfg(params: HaproxyParams) -> str:
    """Build a Debian/systemd-friendly config. Docs: HAProxy 2.8 configuration.txt."""
    validate_params(params)
    port = params.bind_port
    backend = params.backend.strip()
    path = params.path_prefix.strip() or "/api/generate/"
    if not path.endswith("/"):
        path += "/"
    # HAProxy 2.8 configuration.txt: send-proxy-v2 on the server line
    # "enforces use of the PROXY protocol version 2 over any connection
    # established to this server." Xray inbound must set
    # streamSettings.sockopt.acceptProxyProtocol (docs: sockopt.html);
    # otherwise the peer is required to send PROXY and the connection is closed.
    proxy_opt = " send-proxy-v2" if params.proxy_protocol else ""

    # No `daemon`: systemd unit starts master-worker (`-Ws`).
    # stats socket — documented runtime API (`show stat`, reload expose-fd).
    global_defaults = """\
global
    log /dev/log local0
    maxconn 50000
    user haproxy
    group haproxy
    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners
    stats timeout 30s

defaults
    log global
    option dontlognull
    timeout connect 5s
    timeout client 60s
    timeout server 60s
    timeout client-fin 30s
    timeout tunnel 1h
"""
    # timeout tunnel: supersedes client/server once the connection is a tunnel
    # (TCP after analyzers detach; HTTP after upgrade / first response). Docs example: 1h.

    stats = """
frontend fe_stats
    bind 127.0.0.1:8404
    mode http
    stats enable
    stats uri /stats
    stats refresh 5s
    stats admin if { src 127.0.0.0/8 }
"""

    if params.template == "minimal":
        return global_defaults + stats

    if params.template == "tcp":
        blocks = [global_defaults, stats]
        for route in effective_routes(params):
            listen = route.listen
            origin = route.backend.strip()
            blocks.append(
                f"""
frontend fe_tcp_{listen}
    bind *:{listen}
    mode tcp
    option tcplog
    timeout client 1h
    default_backend be_tcp_{listen}

backend be_tcp_{listen}
    mode tcp
    timeout server 1h
    server origin {origin}{proxy_opt}
"""
            )
        return "".join(blocks)

    # front-xhttp — same role as remnawave scripts/front-origin-nginx.sh:
    # HTTP :80, /health, path prefix → Xray xHTTP, camouflage on /.
    # path_beg is the documented ACL for prefix match.
    health_body = "{\\\"status\\\":\\\"ok\\\",\\\"service\\\":\\\"media-gateway\\\"}"
    camouflage = (
        "<!doctype html><html lang=\\\"en\\\"><head><meta charset=\\\"utf-8\\\">"
        "<meta name=\\\"viewport\\\" content=\\\"width=device-width,initial-scale=1\\\">"
        "<title>Media gateway</title></head><body style=\\\"margin:0;min-height:100dvh;"
        "display:grid;place-items:center;background:#0b0d12;color:#d7dde8;"
        "font-family:Georgia,serif\\\"><main style=\\\"text-align:center\\\">"
        "<h1>Media gateway</h1><p style=\\\"color:#8b95a8\\\">Origin is up.</p>"
        "</main></body></html>"
    )
    return (
        global_defaults
        + stats
        + f"""
frontend fe_http
    bind *:{port}
    mode http
    option httplog
    option forwardfor
    timeout client 1h
    acl is_health path /health
    http-request return status 200 content-type "application/json" string "{health_body}" if is_health
    acl is_tunnel path_beg {path}
    use_backend be_xhttp if is_tunnel
    default_backend be_camouflage

backend be_xhttp
    mode http
    timeout server 1h
    option http-keep-alive
    server xray {backend}{proxy_opt}

backend be_camouflage
    mode http
    http-request return status 200 content-type "text/html; charset=utf-8" string "{camouflage}"
"""
    )


_STATUS_PY = r"""
import json, os, re, shutil, subprocess
from pathlib import Path

def sh(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)

bin_path = shutil.which("haproxy") or ("/usr/sbin/haproxy" if os.path.isfile("/usr/sbin/haproxy") else "")
installed = bool(bin_path)
version = None
if installed:
    _rc, out, err = sh(bin_path + " -v")
    text = out or err
    m = re.search(r"version\s+(\S+)", text, re.I)
    version = m.group(1) if m else (text.splitlines()[0][:80] if text else None)

_rc, out, _err = sh("systemctl is-active haproxy")
running = out == "active"
_rc, out, _err = sh("systemctl is-enabled haproxy")
enabled = out == "enabled"

cfg_path = Path("/etc/haproxy/haproxy.cfg")
config = cfg_path.read_text(encoding="utf-8", errors="replace") if cfg_path.is_file() else None

valid = None
if installed and config is not None:
    rc, _out, _err = sh(bin_path + " -c -f /etc/haproxy/haproxy.cfg")
    valid = rc == 0

listen = []
_rc, out, _err = sh("ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true")
for line in out.splitlines():
    if "haproxy" not in line:
        continue
    for m in re.finditer(r"(\S+:\d+)\s", line):
        addr = m.group(1)
        if addr not in listen:
            listen.append(addr)

print(json.dumps({
    "installed": installed,
    "running": running,
    "enabled": enabled,
    "version": version,
    "config": config,
    "listen": listen,
    "valid": valid,
}, ensure_ascii=False))
"""


_DIAG_PY = r"""
import json, os, re, socket, subprocess, time
from pathlib import Path

def sh(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    except Exception as e:
        return str(e)

def sock_cmd(cmd):
    path = "/run/haproxy/admin.sock"
    if not os.path.exists(path):
        return ""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect(path)
        s.sendall((cmd.strip() + "\n").encode())
        chunks = []
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8", "replace")
    except Exception as e:
        return f"[sock] {e}"
    finally:
        try:
            s.close()
        except Exception:
            pass

def tcp_try(host, port, timeout=3.0):
    t0 = time.monotonic()
    try:
        c = socket.create_connection((host, int(port)), timeout)
        c.close()
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "error": None}
    except Exception as e:
        return {"ok": False, "ms": None, "error": str(e)}

cfg_path = Path("/etc/haproxy/haproxy.cfg")
cfg = cfg_path.read_text(encoding="utf-8", errors="replace") if cfg_path.is_file() else ""
proxy = bool(re.search(r"\bsend-proxy(?:-v2)?\b", cfg))
bind_port = None
for m in re.finditer(r"^frontend\s+(\S+)(.*?)(?=^frontend\s|^backend\s|\Z)", cfg, re.M | re.S):
    name, body = m.group(1), m.group(2)
    if "stats" in name.lower() or re.search(r"^\s*stats\s+enable", body, re.M):
        continue
    bm = re.search(r"^\s*bind\s+\S*:(\d+)\b", body, re.M)
    if bm:
        bind_port = int(bm.group(1))
        break
backend = None
be_host = be_port = None
for m in re.finditer(r"^backend\s+(\S+)(.*?)(?=^frontend\s|^backend\s|\Z)", cfg, re.M | re.S):
    name, body = m.group(1), m.group(2)
    if "camouflage" in name.lower():
        continue
    sm = re.search(r"^\s*server\s+\S+\s+(\S+):(\d+)\b", body, re.M)
    if sm:
        be_host, be_port = sm.group(1).strip("[]"), int(sm.group(2))
        backend = f"{sm.group(1)}:{sm.group(2)}"
        break

listen = []
ss_listen = sh("ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true")
for line in ss_listen.splitlines():
    if "haproxy" in line:
        listen.append(line.strip())

estab = []
if bind_port:
    raw_e = sh(f"ss -tnp state established '( sport = :{bind_port} or dport = :{bind_port} )' 2>/dev/null || true")
    for line in raw_e.splitlines()[1:]:
        if line.strip():
            estab.append(line.strip())

backend_tcp = tcp_try(be_host, be_port) if be_host and be_port else None
local_tcp = tcp_try("127.0.0.1", bind_port) if bind_port else None

info = sock_cmd("show info")
stat = sock_cmd("show stat")
errors = sock_cmd("show errors")
sess = sock_cmd("show sess")
journal = sh("journalctl -u haproxy -n 15 --no-pager -l 2>/dev/null || true", timeout=6)

print(json.dumps({
    "bind_port": bind_port,
    "backend": backend,
    "proxy_protocol": proxy,
    "listen": listen,
    "estab": estab[:30],
    "estab_count": len(estab),
    "backend_tcp": backend_tcp,
    "local_tcp": local_tcp,
    "info": info[:4000],
    "stat": stat[:8000],
    "errors": errors[:4000],
    "sess_count": len([ln for ln in sess.splitlines() if ln.strip()]),
    "journal": journal[-3000:],
}, ensure_ascii=False))
"""


def _parse_status(raw: str) -> HaproxyStatus:
    text = raw.strip()
    if not text:
        return HaproxyStatus(error="пустой ответ статуса")
    # last JSON object in output (shell may print warnings first)
    last = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last = line
    if last is None:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            last = text[start : end + 1]
    if last is None:
        return HaproxyStatus(error="не удалось разобрать статус")
    try:
        data = json.loads(last)
    except json.JSONDecodeError as exc:
        return HaproxyStatus(error=f"статус не JSON: {exc}")
    listen = data.get("listen") or []
    if not isinstance(listen, list):
        listen = []
    config = data.get("config") if isinstance(data.get("config"), str) else None
    return HaproxyStatus(
        installed=bool(data.get("installed")),
        running=bool(data.get("running")),
        enabled=bool(data.get("enabled")),
        version=data.get("version") if isinstance(data.get("version"), str) else None,
        config=config,
        listen=[str(x) for x in listen],
        valid=data.get("valid") if isinstance(data.get("valid"), bool) else None,
        parsed=parse_haproxy_cfg(config),
    )


def _open_priv(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    cancel: threading.Event | None,
    log: list[str] | None = None,
):
    def emit(line: str) -> None:
        if log is not None:
            log.append(line)

    _check_cancel(cancel)
    emit(f"→ Проверка TCP {host}:{ssh_port}…")
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        emit(f"✗ {exc.message}")
        raise HaproxyScriptError(exc.message) from exc
    emit(f"✓ Порт {ssh_port} открыт")

    last_error: str | None = None
    for idx, user in enumerate(_ssh_candidates(username)):
        _check_cancel(cancel)
        emit(f"→ SSH {user}@{host}:{ssh_port}…")
        try:
            client_cm = ssh_connect(
                host=host,
                port=ssh_port,
                username=user,
                auth_type=auth_type,
                password=password,
                private_key=private_key,
            )
        except SshConnectError as exc:
            last_error = exc.message
            emit(f"✗ {exc.message}")
            if idx + 1 < len(_ssh_candidates(username)):
                continue
            raise HaproxyScriptError(exc.message) from exc

        try:
            with client_cm as client:
                emit("✓ SSH-сессия открыта")
                code, out, err = _run(client, "id -u; id -un")
                if log is not None:
                    log.extend(_yield_output(out, err))
                text = _combined_text(out, err)
                if looks_like_password_expired(text):
                    raise HaproxyScriptError(
                        "Пароль истёк (PAM требует смену через TTY). "
                        "Сначала установите агент — панель сменит пароль сама."
                    )
                if not _shell_usable(out, err, code):
                    denied = _looks_like_shell_denied(_combined_text(out, err))
                    last_error = denied or (out or err or "shell недоступен").strip()
                    emit(f"✗ {last_error}")
                    if idx + 1 < len(_ssh_candidates(username)):
                        continue
                    raise HaproxyScriptError(last_error)

                priv: Privilege | None = None
                try:
                    for item in _detect_privilege(client, password=password):
                        _check_cancel(cancel)
                        if isinstance(item, Privilege):
                            priv = item
                        else:
                            emit(item)
                except AgentInstallError as exc:
                    raise HaproxyScriptError(str(exc)) from exc
                if priv is None:
                    raise HaproxyScriptError("Не удалось определить права на ноде")
                yield client, priv
                return
        except (HaproxyScriptError, AgentInstallCancelled, AgentInstallError):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or type(exc).__name__
            emit(f"✗ {last_error}")
            if idx + 1 < len(_ssh_candidates(username)):
                continue
            raise HaproxyScriptError(last_error) from exc

    raise HaproxyScriptError(last_error or "Не удалось подключиться по SSH")


def fetch_haproxy_status(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
) -> HaproxyStatus:
    try:
        for client, priv in _open_priv(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            cancel=None,
        ):
            remote = f"/tmp/haproxy-status-{uuid.uuid4().hex[:10]}.py"
            sftp = client.open_sftp()
            try:
                with sftp.file(remote, "w") as f:
                    f.write(_STATUS_PY)
            finally:
                sftp.close()
            try:
                code, out, err = _run_priv(
                    client,
                    priv,
                    f"python3 {shlex.quote(remote)}",
                    timeout=20.0,
                )
            finally:
                _run(client, f"rm -f {shlex.quote(remote)}")
            raw = (out or "") + ("\n" + err if err else "")
            if code != 0 and not raw.strip():
                return HaproxyStatus(error=f"статус: exit {code}")
            status = _parse_status(raw)
            if status.error and code != 0:
                status.error = (status.error + f" (exit {code})").strip()
            return status
    except HaproxyScriptError as exc:
        return HaproxyStatus(error=exc.message)
    except AgentInstallError as exc:
        return HaproxyStatus(error=exc.message)
    return HaproxyStatus(error="не удалось получить статус")


def _last_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    last = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last = line
    if last is None:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            last = text[start : end + 1]
    if last is None:
        return None
    try:
        data = json.loads(last)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def format_haproxy_diag(data: dict) -> list[str]:
    lines: list[str] = []
    bind_port = data.get("bind_port")
    backend = data.get("backend")
    proxy = bool(data.get("proxy_protocol"))
    lines.append(f"frontend: :{bind_port}" if bind_port else "frontend: —")
    lines.append(f"backend: {backend or '—'}")
    lines.append(f"PROXY: {'send-proxy-v2' if proxy else 'выкл'}")
    listen = data.get("listen") or []
    lines.append("listen:")
    if listen:
        lines.extend(f"  {x}" for x in listen)
    else:
        lines.append("  (haproxy не слушает)")

    be = data.get("backend_tcp") or {}
    if be:
        if be.get("ok"):
            lines.append(f"TCP backend: ✅ {be.get('ms')} мс  (голый TCP, без PROXY)")
        else:
            lines.append(f"TCP backend: ❌ {be.get('error')}")
    loc = data.get("local_tcp") or {}
    if loc:
        if loc.get("ok"):
            lines.append(f"TCP 127.0.0.1:{bind_port}: ✅ {loc.get('ms')} мс")
        else:
            lines.append(f"TCP 127.0.0.1:{bind_port}: ❌ {loc.get('error')}")

    estab_n = int(data.get("estab_count") or 0)
    lines.append(f"established на :{bind_port}: {estab_n}")
    for row in (data.get("estab") or [])[:12]:
        lines.append(f"  {row}")

    lines.append(f"сессии HAProxy (show sess): {data.get('sess_count')}")
    stat = data.get("stat") or ""
    if stat and not stat.startswith("[sock]"):
        lines.append("show stat (pxname/svname/scur/stot/bin/bout/status):")
        header = None
        idx = {}
        for row in stat.splitlines():
            if not row or row.startswith("#"):
                if row.startswith("#"):
                    header = [c.strip() for c in row.lstrip("#").split(",")]
                    for want in ("pxname", "svname", "scur", "stot", "bin", "bout", "status"):
                        if want in header:
                            idx[want] = header.index(want)
                continue
            cols = row.split(",")
            if len(cols) < 8:
                continue
            def col(name: str, default: str = "") -> str:
                i = idx.get(name)
                return cols[i] if i is not None and i < len(cols) else default
            px, sv = col("pxname"), col("svname")
            if sv in ("FRONTEND", "BACKEND") or sv == "xray" or sv == "origin":
                lines.append(
                    f"  {px}/{sv}  scur={col('scur')} stot={col('stot')} "
                    f"bin={col('bin')} bout={col('bout')} {col('status')}"
                )
    elif stat:
        lines.append(f"stats socket: {stat.strip()}")

    info = data.get("info") or ""
    for key in ("Name:", "Version:", "Uptime:", "CurrConns:", "CumConns:", "ConnRate:"):
        for ln in info.splitlines():
            if ln.startswith(key):
                lines.append(ln.strip())
                break

    errors = (data.get("errors") or "").strip()
    if errors and "not found" not in errors.lower():
        lines.append("show errors:")
        lines.extend(f"  {ln}" for ln in errors.splitlines()[:20] if ln.strip())

    notes: list[str] = []
    if be and not be.get("ok"):
        notes.append("бэкенд не отвечает на TCP — HAProxy некуда отдать сессию")
    if proxy and be and be.get("ok"):
        notes.append(
            "send-proxy-v2 включён: голый TCP до бэкенда жив, но клиентский трафик "
            "пойдёт только если на inbound Xray стоит sockopt.acceptProxyProtocol=true. "
            "Иначе рукопожатие рвётся — в Remna нули."
        )
    if estab_n == 0 and int(data.get("sess_count") or 0) == 0:
        notes.append("на frontend нет живых сессий — клиенты не доходят до этого IP:порта")
    if notes:
        lines.append("вывод:")
        lines.extend(f"  • {n}" for n in notes)

    journal = (data.get("journal") or "").strip()
    if journal:
        lines.append("journalctl -u haproxy (хвост):")
        lines.extend(f"  {ln}" for ln in journal.splitlines()[-12:])
    return lines


def fetch_haproxy_diag(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
) -> HaproxyDiag:
    try:
        for client, priv in _open_priv(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            cancel=None,
        ):
            remote = f"/tmp/haproxy-diag-{uuid.uuid4().hex[:10]}.py"
            sftp = client.open_sftp()
            try:
                with sftp.file(remote, "w") as f:
                    f.write(_DIAG_PY)
            finally:
                sftp.close()
            try:
                code, out, err = _run_priv(
                    client,
                    priv,
                    f"python3 {shlex.quote(remote)}",
                    timeout=30.0,
                )
            finally:
                _run(client, f"rm -f {shlex.quote(remote)}")
            raw = (out or "") + ("\n" + err if err else "")
            data = _last_json(raw)
            if data is None:
                return HaproxyDiag(
                    error=f"диагностика: не JSON (exit {code})",
                    lines=[raw.strip() or f"exit {code}"],
                )
            return HaproxyDiag(lines=format_haproxy_diag(data))
    except HaproxyScriptError as exc:
        return HaproxyDiag(error=exc.message, lines=[exc.message])
    except AgentInstallError as exc:
        return HaproxyDiag(error=exc.message, lines=[exc.message])
    return HaproxyDiag(error="не удалось выполнить диагностику")


def run_haproxy_script_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    params: HaproxyParams,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    validate_params(params)
    script_body = runner_path().read_text(encoding="utf-8").replace("\r\n", "\n")
    cfg_text: str | None = None
    if params.action in ("install", "apply"):
        raw = params.config if params.config is not None else ""
        cfg_text = normalize_haproxy_cfg(raw if raw.strip() else render_haproxy_cfg(params))

    logs: list[str] = []
    try:
        for client, priv in _open_priv(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            cancel=cancel,
            log=logs,
        ):
            for line in logs:
                yield line
            logs.clear()

            staging = f"/tmp/haproxy-run-{uuid.uuid4().hex[:10]}"
            yield f"$ mkdir -p {staging}"
            try:
                _run(client, f"mkdir -p {shlex.quote(staging)}", check=True)
            except AgentInstallError as exc:
                raise HaproxyScriptError(exc.message) from exc

            remote_script = f"{staging}/haproxy_runner.sh"
            remote_cfg = f"{staging}/haproxy.cfg"
            yield "→ Загрузка haproxy_runner.sh…"
            sftp = client.open_sftp()
            try:
                with sftp.file(remote_script, "w") as f:
                    f.write(script_body)
                if cfg_text is not None:
                    payload = cfg_text.encode("utf-8")
                    if not payload.endswith(b"\n"):
                        payload += b"\n"
                    with sftp.file(remote_cfg, "wb") as f:
                        f.write(payload)
                        f.flush()
                    yield f"→ конфиг {len(payload)} байт → {remote_cfg}"
            finally:
                sftp.close()
            _run(client, f"chmod 700 {shlex.quote(remote_script)}", check=True)
            yield f"✓ {remote_script}"

            env_parts = [
                f"export ACTION={shlex.quote(params.action)}",
                f"export FORCE={shlex.quote('1' if params.force else '0')}",
                f"export BIND_PORT={shlex.quote(str(params.bind_port))}",
                f"export BIND_PORTS={shlex.quote(','.join(str(r.listen) for r in (params.routes or [HaproxyRoute(params.bind_port, params.backend)])))}",
                "export NEEDRESTART_SUSPEND=1",
                "export NEEDRESTART_MODE=l",
            ]
            if cfg_text is not None:
                env_parts.append(f"export HAPROXY_CFG_SRC={shlex.quote(remote_cfg)}")
            run_cmd = (
                "\n".join(env_parts)
                + "\n"
                + f"bash {shlex.quote(remote_script)}\n"
                + "ec=$?\n"
                + f"rm -rf {shlex.quote(staging)}\n"
                + "exit $ec"
            )
            yield f"$ ACTION={params.action} FORCE={1 if params.force else 0} bash haproxy_runner.sh"
            try:
                for line in _stream_priv_command(
                    client, priv, run_cmd, timeout=1800.0, cancel=cancel
                ):
                    yield line
            except AgentInstallCancelled:
                raise
            except RemnaScriptError as exc:
                raise HaproxyScriptError(exc.message) from exc
            except Exception as exc:  # noqa: BLE001
                if cancel and cancel.is_set():
                    raise AgentInstallCancelled() from exc
                raise HaproxyScriptError(str(exc) or type(exc).__name__) from exc

            verb = {
                "install": "установлен",
                "apply": "применён",
                "reload": "перечитан",
                "start": "запущен",
                "stop": "остановлен",
                "uninstall": "удалён",
            }.get(params.action, "готов")
            yield f"✓ HAProxy {verb}"
            return
    finally:
        for line in logs:
            yield line

    raise HaproxyScriptError("Не удалось выполнить действие HAProxy")
