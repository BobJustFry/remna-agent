import base64
import re
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 250_000
TIMEOUT = 8.0
USER_AGENT = "RemnaAgent/0.1 (+favicon)"

LINK_RE = re.compile(
    r'<link[^>]+rel=["\'](?:shortcut icon|icon|apple-touch-icon(?:-precomposed)?)["\'][^>]*>',
    re.I,
)
HREF_RE = re.compile(r'''href=["']([^"']+)["']''', re.I)
CONTENT_TYPE_MAP = {
    "image/x-icon": "image/x-icon",
    "image/vnd.microsoft.icon": "image/x-icon",
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/gif": "image/gif",
    "image/svg+xml": "image/svg+xml",
    "image/webp": "image/webp",
}


def normalize_website_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        return None
    return value.rstrip("/")


async def fetch_favicon_data_url(website_url: str) -> str | None:
    base = normalize_website_url(website_url)
    if not base:
        return None

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=TIMEOUT,
        headers=headers,
        verify=True,
    ) as client:
        candidates: list[str] = []
        try:
            page = await client.get(base)
            if page.status_code < 400 and "text/html" in page.headers.get("content-type", ""):
                for match in LINK_RE.finditer(page.text[:200_000]):
                    href_m = HREF_RE.search(match.group(0))
                    if href_m:
                        candidates.append(urljoin(str(page.url), href_m.group(1)))
        except Exception:
            pass

        parsed = urlparse(base)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        candidates.extend(
            [
                urljoin(origin + "/", "favicon.ico"),
                urljoin(origin + "/", "favicon.png"),
                urljoin(origin + "/", "apple-touch-icon.png"),
            ]
        )

        seen: set[str] = set()
        for icon_url in candidates:
            if icon_url in seen:
                continue
            seen.add(icon_url)
            data_url = await _download_as_data_url(client, icon_url)
            if data_url:
                return data_url
    return None


async def _download_as_data_url(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        content = resp.content
        if not content or len(content) > MAX_BYTES:
            return None
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in CONTENT_TYPE_MAP and not ctype.startswith("image/"):
            # some servers return octet-stream for ico
            if url.lower().endswith(".ico"):
                ctype = "image/x-icon"
            elif url.lower().endswith(".png"):
                ctype = "image/png"
            elif url.lower().endswith(".svg"):
                ctype = "image/svg+xml"
            else:
                return None
        mime = CONTENT_TYPE_MAP.get(ctype, ctype)
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None
