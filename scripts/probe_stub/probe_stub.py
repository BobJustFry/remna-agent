#!/usr/bin/env python3
"""Локальная заглушка generate_204 для быстрого proxy-ping через туннель.

Фон: ping шлюза (default route). На запрос GET/HEAD к /generate_204 → 204 если
шлюз жив, иначе 503. Поддерживает Host от Cloudflare / Google / Gstatic.

Запуск: GATEWAY=auto LISTEN=127.0.0.1:19081 python3 probe_stub.py
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATEWAY = os.environ.get("GATEWAY", "auto").strip()
LISTEN = os.environ.get("LISTEN", "127.0.0.1:19081").strip()
CHECK_INTERVAL = float(os.environ.get("CHECK_INTERVAL", "10"))
GW_TIMEOUT = float(os.environ.get("GW_TIMEOUT", "2"))
STALE_SECS = float(os.environ.get("STALE_SECS", "30"))

PROBE_PATHS = {"/generate_204", "/gen_204", "/"}

_state = {"ok": False, "ts": 0.0, "gateway": "", "last_ms": None, "err": ""}
_lock = threading.Lock()


def _detect_gateway() -> str:
    if GATEWAY and GATEWAY.lower() != "auto":
        return GATEWAY
    try:
        out = subprocess.check_output(["ip", "route"], text=True, timeout=5)
    except Exception as ex:
        raise RuntimeError(f"ip route failed: {ex}") from ex
    for line in out.splitlines():
        if line.startswith("default "):
            parts = line.split()
            if "via" in parts:
                return parts[parts.index("via") + 1]
    raise RuntimeError("default gateway not found")


def _ping_gateway(gw: str) -> tuple[bool, int | None, str]:
    try:
        t0 = time.perf_counter()
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", str(max(1, int(GW_TIMEOUT))), gw],
            capture_output=True,
            text=True,
            timeout=GW_TIMEOUT + 2,
        )
        ms = int(round((time.perf_counter() - t0) * 1000))
        if proc.returncode == 0:
            m = re.search(r"time[=<]([\d.]+)\s*ms", proc.stdout)
            if m:
                ms = int(round(float(m.group(1))))
            return True, ms, ""
        return False, None, (proc.stderr or proc.stdout or "ping failed")[:120]
    except Exception as ex:
        return False, None, str(ex)[:120]


def _checker(gw: str) -> None:
    while True:
        ok, ms, err = _ping_gateway(gw)
        with _lock:
            _state["ok"] = ok
            _state["ts"] = time.time()
            _state["gateway"] = gw
            _state["last_ms"] = ms
            _state["err"] = err
        time.sleep(CHECK_INTERVAL)


def _gateway_live() -> bool:
    with _lock:
        if not _state["ok"]:
            return False
        return (time.time() - _state["ts"]) <= STALE_SECS


class Handler(BaseHTTPRequestHandler):
    server_version = "vpn-probe-stub/1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in PROBE_PATHS and not path.endswith("/generate_204"):
            self.send_error(404, "not a probe path")
            return
        if not _gateway_live():
            with _lock:
                err = _state.get("err") or "gateway down"
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Probe-Stub", "gateway-down")
            self.end_headers()
            self.wfile.write(f"gateway unavailable: {err}\n".encode())
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("X-Probe-Stub", "ok")
        self.end_headers()

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()


def main() -> None:
    gw = _detect_gateway()
    host, port_s = LISTEN.rsplit(":", 1)
    port = int(port_s)
    threading.Thread(target=_checker, args=(gw,), daemon=True).start()
    ok, ms, err = _ping_gateway(gw)
    with _lock:
        _state.update({"ok": ok, "ts": time.time(), "gateway": gw, "last_ms": ms, "err": err})
    if not ok:
        print(f"warn: gateway {gw} not reachable at start: {err}", flush=True)
    else:
        print(f"gateway {gw} ok ({ms} ms)", flush=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"probe_stub listen {host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
