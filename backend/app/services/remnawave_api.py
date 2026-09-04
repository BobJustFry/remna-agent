"""Минимальный клиент API панели Remnawave (профили, ноды)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config import settings


class RemnawaveApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _configured() -> bool:
    return bool(settings.remnawave_panel_url.strip() and settings.remnawave_api_token.strip())


def rw_api(path: str, method: str = "GET", body: dict | None = None) -> object:
    if not _configured():
        raise RemnawaveApiError(
            "REMNAWAVE_PANEL_URL и REMNAWAVE_API_TOKEN не заданы в .env панели"
        )
    base = settings.remnawave_panel_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.remnawave_api_token.strip()}"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RemnawaveApiError(f"Remnawave API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RemnawaveApiError(f"Remnawave API: {exc.reason}") from exc
    payload = json.loads(raw)
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


def rw_nodes() -> list[dict]:
    out = rw_api("/api/nodes")
    return out if isinstance(out, list) else []


def rw_users_online_by_address() -> dict[str, int]:
    """Users the Xray core reports online, keyed by node address.

    The nodes run no gRPC stats API of their own, so this is the only place the
    core's own count is available: RemnaNode reports it to the panel, and the
    panel hands it to us. Returns {} when Remnawave is not configured or errors —
    an unavailable panel must not blank out the whole agents view.
    """
    if not _configured():
        return {}
    try:
        nodes = rw_nodes()
    except RemnawaveApiError:
        return {}
    out: dict[str, int] = {}
    for n in nodes:
        addr = (n.get("address") or "").strip()
        if not addr:
            continue
        try:
            out[addr] = int(n.get("usersOnline") or 0)
        except (TypeError, ValueError):
            continue
    return out


def rw_node_by_name(name: str) -> dict:
    key = name.strip().lower()
    nodes = rw_nodes()
    for n in nodes:
        if (n.get("name") or "").lower() == key or n.get("address") == name:
            return n
    for n in nodes:
        if key in (n.get("name") or "").lower():
            return n
    raise RemnawaveApiError(f"нода {name!r} не найдена в Remnawave")
