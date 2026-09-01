from fastapi import Request

from app.config import settings


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
        real = request.headers.get("x-real-ip", "")
        if real.strip():
            return real.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
