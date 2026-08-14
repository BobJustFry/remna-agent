"""Cached Remnawave release versions from GitHub."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

PANEL_RELEASES_URL = "https://api.github.com/repos/remnawave/panel/releases/latest"
NODE_RELEASES_URL = "https://api.github.com/repos/remnawave/node/releases/latest"
CACHE_TTL_SEC = 30 * 60
_HTTP_TIMEOUT = 12.0


@dataclass
class RemnawaveVersions:
    panel_version: str | None
    node_version: str | None
    panel_url: str | None
    node_url: str | None
    checked_at: float
    error: str | None = None


_cache: RemnawaveVersions | None = None
_lock = asyncio.Lock()


def normalize_version(raw: str | None) -> str | None:
    if not raw:
        return None
    v = raw.strip()
    if v.lower().startswith("v") and len(v) > 1 and v[1].isdigit():
        v = v[1:]
    return v or None


def versions_differ(installed: str | None, latest: str | None) -> bool:
    a = normalize_version(installed)
    b = normalize_version(latest)
    if not a or not b:
        return False
    return a != b


async def _fetch_tag(client: httpx.AsyncClient, url: str) -> tuple[str | None, str | None]:
    resp = await client.get(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "remna-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    tag = normalize_version(data.get("tag_name") or data.get("name"))
    html = data.get("html_url")
    return tag, html if isinstance(html, str) else None


async def get_remnawave_versions(*, force: bool = False) -> RemnawaveVersions:
    global _cache
    now = time.time()
    if not force and _cache is not None and (now - _cache.checked_at) < CACHE_TTL_SEC:
        return _cache

    async with _lock:
        now = time.time()
        if not force and _cache is not None and (now - _cache.checked_at) < CACHE_TTL_SEC:
            return _cache

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                (panel_v, panel_url), (node_v, node_url) = await asyncio.gather(
                    _fetch_tag(client, PANEL_RELEASES_URL),
                    _fetch_tag(client, NODE_RELEASES_URL),
                )
            _cache = RemnawaveVersions(
                panel_version=panel_v,
                node_version=node_v,
                panel_url=panel_url,
                node_url=node_url,
                checked_at=time.time(),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001
            if _cache is not None:
                _cache = RemnawaveVersions(
                    panel_version=_cache.panel_version,
                    node_version=_cache.node_version,
                    panel_url=_cache.panel_url,
                    node_url=_cache.node_url,
                    checked_at=_cache.checked_at,
                    error=str(exc),
                )
            else:
                _cache = RemnawaveVersions(
                    panel_version=None,
                    node_version=None,
                    panel_url=None,
                    node_url=None,
                    checked_at=time.time(),
                    error=str(exc),
                )
        return _cache
