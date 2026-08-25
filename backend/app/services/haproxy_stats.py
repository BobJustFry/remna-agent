"""Live HAProxy stats from the documented runtime API (admin.sock).

HAProxy 2.8 configuration.txt: `show stat`, `show info`, `show sess`, `show errors`.
"""

from __future__ import annotations

import re
import shlex
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from app.services.agent_install import AgentInstallError, _run, _run_priv
from app.services.haproxy_script import HaproxyScriptError, _last_json, _open_priv

_STATS_PY = r"""
import json, os, socket

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

print(json.dumps({
    "info": sock_cmd("show info"),
    "stat": sock_cmd("show stat"),
    "sess": sock_cmd("show sess"),
    "errors": sock_cmd("show errors"),
}, ensure_ascii=False))
"""

_SESS_SRC = re.compile(r"\bsrc=(\S+)")
_SESS_FE = re.compile(r"\bfe=(\S+)")
_SESS_BE = re.compile(r"\bbe=(\S+)")
_SESS_AGE = re.compile(r"\bage=(\S+)")


@dataclass
class HaproxyStatRow:
    pxname: str
    svname: str
    scur: int | None = None
    smax: int | None = None
    stot: int | None = None
    bin: int | None = None
    bout: int | None = None
    rate: int | None = None
    rate_max: int | None = None
    status: str = ""
    ereq: int | None = None
    econ: int | None = None
    eresp: int | None = None
    wretr: int | None = None
    wredis: int | None = None
    lastsess: int | None = None


@dataclass
class HaproxySession:
    raw: str
    src: str | None = None
    frontend: str | None = None
    backend: str | None = None
    age: str | None = None


@dataclass
class HaproxyHistoryPoint:
    ts: float
    curr_conns: int | None = None
    conn_rate: int | None = None
    bin: int | None = None
    bout: int | None = None


@dataclass
class HaproxyLiveStats:
    uptime: str | None = None
    curr_conns: int | None = None
    cum_conns: int | None = None
    conn_rate: int | None = None
    bin: int | None = None
    bout: int | None = None
    rows: list[HaproxyStatRow] = field(default_factory=list)
    sessions: list[HaproxySession] = field(default_factory=list)
    history: list[HaproxyHistoryPoint] = field(default_factory=list)
    errors: str = ""
    error: str | None = None


_HISTORY: dict[str, deque[HaproxyHistoryPoint]] = {}
_HISTORY_LOCK = threading.Lock()
_HISTORY_MAX = 240
_HISTORY_MIN_INTERVAL = 8.0


def record_sample(node_id: str, stats: HaproxyLiveStats) -> None:
    if not node_id or stats.error:
        return
    now = time.time()
    with _HISTORY_LOCK:
        buf = _HISTORY.setdefault(node_id, deque(maxlen=_HISTORY_MAX))
        if buf and now - buf[-1].ts < _HISTORY_MIN_INTERVAL:
            return
        buf.append(
            HaproxyHistoryPoint(
                ts=now,
                curr_conns=stats.curr_conns,
                conn_rate=stats.conn_rate,
                bin=stats.bin,
                bout=stats.bout,
            )
        )


def get_history(node_id: str) -> list[HaproxyHistoryPoint]:
    if not node_id:
        return []
    with _HISTORY_LOCK:
        return list(_HISTORY.get(node_id, ()))


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_show_info(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        out[key.strip()] = val.strip()
    return out


def parse_show_stat(text: str) -> list[HaproxyStatRow]:
    if not text or text.startswith("[sock]"):
        return []
    header: list[str] = []
    idx: dict[str, int] = {}
    rows: list[HaproxyStatRow] = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("#"):
            header = [c.strip() for c in line.lstrip("#").split(",")]
            idx = {name: i for i, name in enumerate(header)}
            continue
        cols = line.split(",")
        if not idx or len(cols) < 8:
            continue

        def col(name: str) -> str:
            i = idx.get(name)
            return cols[i] if i is not None and i < len(cols) else ""

        sv = col("svname")
        if sv not in ("FRONTEND", "BACKEND", "origin", "xray"):
            continue
        rows.append(
            HaproxyStatRow(
                pxname=col("pxname"),
                svname=sv,
                scur=_to_int(col("scur")),
                smax=_to_int(col("smax")),
                stot=_to_int(col("stot")),
                bin=_to_int(col("bin")),
                bout=_to_int(col("bout")),
                rate=_to_int(col("rate")),
                rate_max=_to_int(col("rate_max")),
                status=col("status"),
                ereq=_to_int(col("ereq")),
                econ=_to_int(col("econ")),
                eresp=_to_int(col("eresp")),
                wretr=_to_int(col("wretr")),
                wredis=_to_int(col("wredis")),
                lastsess=_to_int(col("lastsess")),
            )
        )
    return rows


def parse_show_sess(text: str) -> list[HaproxySession]:
    if not text or text.startswith("[sock]"):
        return []
    sessions: list[HaproxySession] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        src = _SESS_SRC.search(raw)
        fe = _SESS_FE.search(raw)
        be = _SESS_BE.search(raw)
        age = _SESS_AGE.search(raw)
        sessions.append(
            HaproxySession(
                raw=raw,
                src=src.group(1) if src else None,
                frontend=fe.group(1) if fe else None,
                backend=be.group(1) if be else None,
                age=age.group(1) if age else None,
            )
        )
        if len(sessions) >= 80:
            break
    return sessions


def build_live_stats(data: dict) -> HaproxyLiveStats:
    info = parse_show_info(data.get("info") or "")
    rows = parse_show_stat(data.get("stat") or "")
    front = [r for r in rows if r.svname == "FRONTEND"]
    bin_total = sum(r.bin or 0 for r in front) or None
    bout_total = sum(r.bout or 0 for r in front) or None
    errors = (data.get("errors") or "").strip()
    if errors.startswith("[sock]"):
        errors = ""
    return HaproxyLiveStats(
        uptime=info.get("Uptime") or info.get("Uptime_sec"),
        curr_conns=_to_int(info.get("CurrConns")),
        cum_conns=_to_int(info.get("CumConns")),
        conn_rate=_to_int(info.get("ConnRate")),
        bin=bin_total,
        bout=bout_total,
        rows=rows,
        sessions=parse_show_sess(data.get("sess") or ""),
        errors=errors,
    )


def fetch_haproxy_stats(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    node_id: str | None = None,
) -> HaproxyLiveStats:
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
            remote = f"/tmp/haproxy-live-{uuid.uuid4().hex[:10]}.py"
            sftp = client.open_sftp()
            try:
                with sftp.file(remote, "w") as f:
                    f.write(_STATS_PY)
            finally:
                sftp.close()
            try:
                code, out, err = _run_priv(
                    client,
                    priv,
                    f"python3 {shlex.quote(remote)}",
                    timeout=25.0,
                )
            finally:
                _run(client, f"rm -f {shlex.quote(remote)}")
            raw = (out or "") + ("\n" + err if err else "")
            payload = _last_json(raw)
            if payload is None:
                stats = HaproxyLiveStats(error=f"статистика: не JSON (exit {code})")
                if node_id:
                    stats.history = get_history(node_id)
                return stats
            stats = build_live_stats(payload)
            if (payload.get("stat") or "").startswith("[sock]"):
                stats.error = (payload.get("stat") or "нет admin.sock").strip()
            if node_id:
                record_sample(node_id, stats)
                stats.history = get_history(node_id)
            return stats
    except HaproxyScriptError as exc:
        stats = HaproxyLiveStats(error=exc.message)
    except AgentInstallError as exc:
        stats = HaproxyLiveStats(error=exc.message)
    else:
        stats = HaproxyLiveStats(error="не удалось снять статистику HAProxy")
    if node_id:
        stats.history = get_history(node_id)
    return stats
