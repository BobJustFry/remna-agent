"""Оператор, город и тип (мобильный/проводной) по клиентскому адресу.

Что было до этого: asn_lookup (Team Cymru по DNS) даёт только номер сети и
страну регистрации, кеш в памяти на сутки, без названия оператора, города и
признака «мобильный». Его мы используем как запасной источник номера сети.

Город и тип берём у бесплатного пакетного API (по умолчанию ip-api.com/batch,
до 100 адресов за запрос, только HTTP). Правила:
- вызывается только при сборке отчёта, из сканов — никогда;
- не чаще одного пакета в минуту на процесс;
- результат кладётся в таблицу ip_geo_cache на 30 дней (неудача — на сутки);
- нет данных — поля пустые, ничего не выдумываем.
Отключить: SHARING_GEO_URL="" в окружении.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IpGeoCache
from app.services import asn_lookup

log = logging.getLogger("remna.geo")

GEO_URL = os.environ.get("SHARING_GEO_URL", "http://ip-api.com/batch")
FIELDS = "status,message,query,countryCode,regionName,city,lat,lon,isp,as,mobile,hosting"
BATCH = 100
MIN_INTERVAL = 60.0
TTL_OK = timedelta(days=30)
TTL_FAIL = timedelta(days=1)

_last_call = 0.0


def _public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast)


def _row_to_info(r: IpGeoCache) -> dict[str, Any]:
    return {"ip": r.ip, "asn": r.asn, "isp": r.isp, "city": r.city, "region": r.region,
            "country": r.country, "lat": r.lat, "lon": r.lon, "mobile": r.mobile,
            "hosting": r.hosting, "ok": r.ok}


def parse_item(item: dict) -> dict[str, Any] | None:
    """Один элемент ответа пакета → запись кеша. None — адрес без ответа."""
    ip = str(item.get("query") or "")
    if not ip:
        return None
    if item.get("status") != "success":
        return {"ip": ip, "ok": False}
    m = re.match(r"AS(\d+)", str(item.get("as") or ""))

    def num(v: Any) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def flag(v: Any) -> bool | None:
        return v if isinstance(v, bool) else None

    return {
        "ip": ip,
        "asn": int(m.group(1)) if m else None,
        "isp": (str(item.get("isp") or "").strip() or None),
        "city": (str(item.get("city") or "").strip() or None),
        "region": (str(item.get("regionName") or "").strip() or None),
        "country": (str(item.get("countryCode") or "").strip() or None),
        "lat": num(item.get("lat")),
        "lon": num(item.get("lon")),
        "mobile": flag(item.get("mobile")),
        "hosting": flag(item.get("hosting")),
        "ok": True,
    }


async def _fetch_batch(ips: list[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(f"{GEO_URL}?fields={FIELDS}&lang=ru", json=ips[:BATCH])
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _cymru_asn(ips: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ip in ips:
        try:
            key = asn_lookup.network_of(ip)
        except Exception:  # noqa: BLE001
            continue
        if key.startswith("as:") and key[3:].isdigit():
            out[ip] = int(key[3:])
    return out


async def lookup(db: AsyncSession, ips: list[str], fetch: bool = True) -> dict[str, dict[str, Any]]:
    """ip → {asn, isp, city, mobile, hosting, lat, lon, ...}. Коммит — на вызывающем."""
    global _last_call
    wanted = list(dict.fromkeys(ip for ip in ips if ip and _public(ip)))
    if not wanted:
        return {}
    now = datetime.now(timezone.utc)
    res = await db.execute(select(IpGeoCache).where(IpGeoCache.ip.in_(wanted)))
    cached = {r.ip: r for r in res.scalars()}
    result: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    for ip in wanted:
        r = cached.get(ip)
        if r is None:
            stale.append(ip)
            continue
        result[ip] = _row_to_info(r)
        if now - r.fetched_at > (TTL_OK if r.ok else TTL_FAIL):
            stale.append(ip)
    if fetch and GEO_URL and stale and time.time() - _last_call >= MIN_INTERVAL:
        _last_call = time.time()
        try:
            items = await _fetch_batch(stale)
        except Exception as exc:  # noqa: BLE001
            log.warning("ip_geo: пакет не получен: %s", exc)
            items = []
        for item in items:
            info = parse_item(item) if isinstance(item, dict) else None
            if not info:
                continue
            row = cached.get(info["ip"])
            if row is None:
                row = IpGeoCache(ip=info["ip"])
                db.add(row)
                cached[info["ip"]] = row
            for k in ("asn", "isp", "city", "region", "country", "lat", "lon",
                      "mobile", "hosting"):
                setattr(row, k, info.get(k))
            row.ok = bool(info.get("ok"))
            row.fetched_at = now
            result[info["ip"]] = dict(info)
        await db.flush()
    # Номер сети из Cymru там, где API его не дал.
    need = [ip for ip in wanted if not (result.get(ip) or {}).get("asn")]
    if need:
        asns = await asyncio.to_thread(_cymru_asn, need)
        for ip, asn in asns.items():
            result.setdefault(ip, {"ip": ip})["asn"] = asn
    return result
