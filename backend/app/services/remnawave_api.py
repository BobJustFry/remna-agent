"""Минимальный клиент API панели Remnawave (профили, ноды)."""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

from app.config import settings

_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_dns_cache: dict[str, tuple[float, str]] = {}
_DNS_TTL = 600.0


def _resolve(host: str) -> str:
    """A-запись хоста, с кэшем. Пустая строка, если не резолвится.

    Резолвим редко и кэшируем: функция зовётся на каждый тик сэмплера, а список
    доменов почти не меняется. Ошибку DNS считаем нормой — узел просто не получит
    второго ключа.
    """
    now = time.monotonic()
    hit = _dns_cache.get(host)
    if hit and now - hit[0] < _DNS_TTL:
        return hit[1]
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        ip = ""
    _dns_cache[host] = (now, ip)
    return ip


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


def rw_configured() -> bool:
    """Задана ли у рагента панель Remnawave."""
    return _configured()


def rw_fleet_by_address() -> dict[str, dict]:
    """Что панель знает о каждой ноде, по адресу ноды. Returns {} when Remnawave
    is not configured or errors — an unavailable panel must not blank out the
    whole agents view."""
    return rw_fleet() or {}


def rw_fleet() -> dict[str, dict] | None:
    """Что панель знает о каждой ноде, по адресу ноды.

    {адрес: {"users": онлайн ядра | None, "connected": bool | None, "disabled": bool}}.
    Один запрос на всё: и онлайн для графиков, и «видит ли панель ноду» для
    следилки за нодами. None — панель не настроена или не ответила: следилке
    важно отличать «панель ноду не видит» от «панель молчит».
    """
    if not _configured():
        return None
    try:
        nodes = rw_nodes()
    except RemnawaveApiError:
        return None
    out: dict[str, dict] = {}
    claims: dict[str, int] = {}
    for n in nodes:
        addr = (n.get("address") or "").strip()
        if not addr:
            continue
        try:
            users: int | None = int(n.get("usersOnline") or 0)
        except (TypeError, ValueError):
            users = None
        connected = n.get("isConnected")
        up = n.get("xrayUptime")
        info = {
            "users": users,
            "connected": connected if isinstance(connected, bool) else None,
            "disabled": n.get("isDisabled") is True,
            # Для памятьсторожа: какой именно узел панели и сколько живёт его ядро.
            "uuid": n.get("uuid") if isinstance(n.get("uuid"), str) else None,
            "name": n.get("name") if isinstance(n.get("name"), str) else None,
            "xray_uptime": int(up) if isinstance(up, (int, float)) and not isinstance(up, bool) else None,
            "dup": False,
        }
        out[addr] = info
        claims[addr] = claims.get(addr, 0) + 1
        # Ноды за CDN панель адресует origin-доменом, а у нас та же машина
        # записана по IP (рагент ходит к ней по SSH, и завязывать его на
        # CDN-домен нельзя). Без второго ключа такая нода выглядит в карточке
        # как «нет данных», хотя онлайн у панели есть.
        if not _IPV4.match(addr):
            ip = _resolve(addr)
            if ip:
                claims[ip] = claims.get(ip, 0) + 1
                out.setdefault(ip, dict(info))
    # Один адрес занят двумя узлами панели — какой из них «наш», не знаем:
    # перезапускать по такому ключу нельзя.
    for key, n_claims in claims.items():
        if n_claims > 1 and key in out:
            out[key]["dup"] = True
    return out


def users_online(fleet: dict[str, dict]) -> dict[str, int]:
    """Онлайн ядра Xray по адресам — из ответа rw_fleet_by_address."""
    return {addr: info["users"] for addr, info in fleet.items() if info.get("users") is not None}


def rw_users_online_by_address() -> dict[str, int]:
    """Users the Xray core reports online, keyed by node address.

    The nodes run no gRPC stats API of their own, so this is the only place the
    core's own count is available: RemnaNode reports it to the panel, and the
    panel hands it to us. Returns {} when Remnawave is not configured or errors.
    """
    return users_online(rw_fleet_by_address())


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
