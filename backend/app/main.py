from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import auth, hostings, nodes, remnawave, settings as settings_api
from app.config import assert_secure_settings, settings
from app.services.metrics_sampler import run_metrics_sampler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    assert_secure_settings()
    task = asyncio.create_task(run_metrics_sampler(), name="metrics-sampler")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# https://fastapi.tiangolo.com/how-to/conditional-openapi/
app = FastAPI(
    title="Remna Agent",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url=settings.openapi_url or None,
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
if hosts and hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

if settings.trust_proxy:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.include_router(auth.router, prefix="/api")
app.include_router(hostings.router, prefix="/api")
app.include_router(nodes.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(remnawave.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
