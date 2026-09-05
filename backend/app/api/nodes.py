import asyncio
import json
import secrets
import threading
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, get_db
from app.deps import require_user
from app.models import AppSetting, AuthType, Hosting, Node
from app.schemas import (
    AgentNodeStatus,
    DestLoopbackRequest,
    DestScanRequest,
    HaproxyDiagOut,
    HaproxyHistoryPointOut,
    HaproxyLiveStatsOut,
    HaproxyParsedOut,
    HaproxyPreviewOut,
    HaproxyPreviewRequest,
    HaproxyRunRequest,
    HaproxySessionOut,
    HaproxyStatRowOut,
    HaproxyStatusOut,
    NodeCreate,
    NodeOnlineStatus,
    NodeOut,
    NodeRebootOut,
    NodeSecretOut,
    NodesAgentResponse,
    NodesMetricsResponse,
    NodesOnlineResponse,
    NodeUpdate,
    RemnaScriptRunRequest,
    SshCheckOut,
    VpsCapacityOut,
    ProbeStubInstallRequest,
    WarpInstallRequest,
)
from app.services.agent_install import (
    AGENT_PORT_DEFAULT,
    AgentInstallCancelled,
    AgentInstallError,
    install_agent_via_ssh,
)
from app.services.agent_metrics import (
    AgentStatus,
    clear_agent_status_cache,
    fetch_agent_status,
    is_token_mismatch,
    peek_cached_status,
)
from app.services.agent_token_sync import TokenSyncError, repair_agent_auth_via_ssh
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.metrics_store import MetricRange, clear_node_samples, fetch_series
from app.services.ping import check_many
from app.services.node_reboot import NodeRebootError, reboot_node_via_ssh
from app.services.remnanode_script import (
    RemnaScriptError,
    RemnaScriptParams,
    run_remnanode_script_via_ssh,
)
from app.services.ssh_check import check_ssh_auth
from app.services.haproxy_script import (
    HaproxyParams,
    HaproxyRoute,
    HaproxyScriptError,
    fetch_haproxy_diag,
    fetch_haproxy_status,
    render_haproxy_cfg,
    run_haproxy_script_via_ssh,
)
from app.services.haproxy_stats import fetch_haproxy_stats
from app.services.vps_capacity import fetch_vps_capacity
from app.services.probe_stub_script import ProbeStubScriptError, run_probe_stub_via_ssh
from app.services.warp_script import WarpScriptError, run_warp_script_via_ssh
from app.services.ssh_keys import normalize_private_key
from app.services.dest_pick import DestPickError, run_dest_loopback, run_dest_scan

router = APIRouter(prefix="/nodes", tags=["nodes"])


def _to_out(node: Node) -> NodeOut:
    return NodeOut(
        id=node.id,
        name=node.name,
        host=node.host,
        ssh_port=node.ssh_port,
        ssh_user=node.ssh_user,
        auth_type=node.auth_type,  # type: ignore[arg-type]
        has_password=bool(node.password_enc),
        has_private_key=bool(node.private_key_enc),
        hosting_id=node.hosting_id,
        hosting_name=node.hosting.name if node.hosting else None,
        hosting_website_url=node.hosting.website_url if node.hosting else None,
        hosting_favicon_data=node.hosting.favicon_data if node.hosting else None,
        country_code=node.country_code,
        notes=node.notes,
        agent_configured=bool(node.agent_token_enc),
        agent_port=node.agent_port or AGENT_PORT_DEFAULT,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _validate_secrets(auth_type: str, password: str | None, private_key: str | None, *, required: bool) -> None:
    if auth_type == AuthType.password.value:
        if required and not password:
            raise HTTPException(status_code=400, detail="Для типа password нужен пароль")
    elif auth_type == AuthType.private_key.value:
        if required and not private_key:
            raise HTTPException(status_code=400, detail="Для типа private_key нужен приватный ключ")
    else:
        raise HTTPException(status_code=400, detail="Неизвестный auth_type")


async def _ensure_hosting(db: AsyncSession, hosting_id: uuid.UUID | None) -> None:
    if hosting_id is None:
        return
    hosting = await db.get(Hosting, hosting_id)
    if not hosting:
        raise HTTPException(status_code=400, detail="Хостинг не найден")


async def _get_node(db: AsyncSession, node_id: uuid.UUID) -> Node:
    result = await db.execute(
        select(Node).options(selectinload(Node.hosting)).where(Node.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    return node


@router.get("", response_model=list[NodeOut])
async def list_nodes(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[NodeOut]:
    result = await db.execute(
        select(Node).options(selectinload(Node.hosting)).order_by(Node.name.asc())
    )
    nodes = result.scalars().all()
    return [_to_out(n) for n in nodes]


@router.get("/online", response_model=NodesOnlineResponse)
async def nodes_online(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodesOnlineResponse:
    result = await db.execute(select(Node.id, Node.host, Node.ssh_port))
    rows = result.all()
    items = [(str(row.id), row.host, row.ssh_port) for row in rows]
    checked = await check_many(items)
    statuses = {}
    for node_id, item in checked.items():
        cached = peek_cached_status(node_id)
        cf_ms = cached.cf204_ms if cached and cached.cf204_ok else None
        statuses[node_id] = NodeOnlineStatus(
            online=item.online,
            latency_ms=cf_ms,
            method="cf204" if cf_ms is not None else item.method,
        )
    return NodesOnlineResponse(statuses=statuses)


@router.get("/metrics", response_model=NodesMetricsResponse)
async def nodes_metrics(
    range_key: MetricRange = Query(default="day", alias="range"),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodesMetricsResponse:
    step, from_ts, to_ts, series = await fetch_series(db, range_key=range_key)
    return NodesMetricsResponse(range=range_key, step_sec=step, from_ts=from_ts, to_ts=to_ts, series=series)


# Avoid hammering SSH when token stays broken (seconds).
_TOKEN_SYNC_COOLDOWN_SEC = 60.0
_token_sync_last_try: dict[str, float] = {}


def _node_kind(st: AgentStatus) -> str:
    """XRAY node or plain HAProxy proxy, as the agent sees the box.

    RemnaNode present wins: a box that also fronts with HAProxy is still an Xray
    node. Without an agent we cannot tell, and say so rather than guessing.
    """
    if not st.present:
        return "unknown"
    if st.remnanode_running or st.remnanode_version:
        return "xray"
    if st.haproxy_present:
        return "proxy"
    return "unknown"


def _status_to_schema(
    st: AgentStatus,
    hosting_bandwidth_mbps: int | None = None,
    xray_online: int | None = None,
) -> AgentNodeStatus:
    return AgentNodeStatus(
        present=st.present,
        configured=st.configured,
        capacity_comfort=st.capacity_comfort,
        capacity_ceiling=st.capacity_ceiling,
        capacity_limiter=st.capacity_limiter,
        net_rx_bps=st.net_rx_bps,
        net_tx_bps=st.net_tx_bps,
        net_iface=st.net_iface,
        net_link_mbps=st.net_link_mbps,
        hosting_bandwidth_mbps=hosting_bandwidth_mbps,
        xray_online=xray_online,
        kind=_node_kind(st),
        version=st.version,
        remnanode_version=st.remnanode_version,
        remnanode_running=st.remnanode_running,
        warp_present=st.warp_present,
        warp_up=st.warp_up,
        warp_healthy=st.warp_healthy,
        warp_handshake_sec=st.warp_handshake_sec,
        warp_egress_ok=st.warp_egress_ok,
        warp_interface=st.warp_interface,
        warp_method=st.warp_method,
        warp_version=st.warp_version,
        warp_ipv4=st.warp_ipv4,
        haproxy_present=st.haproxy_present,
        haproxy_up=st.haproxy_up,
        haproxy_version=st.haproxy_version,
        haproxy_listen=st.haproxy_listen,
        proxy_peers=st.proxy_peers,
        proxy_conns=st.proxy_conns,
        cpu_percent=st.cpu_percent,
        mem_percent=st.mem_percent,
        disk_percent=st.disk_percent,
        loadavg=st.loadavg,
        cf204_ok=st.cf204_ok,
        cf204_ms=st.cf204_ms,
        error=st.error,
    )


async def _probe_one_node(
    node: Node, online_by_address: dict[str, int] | None = None
) -> tuple[str, AgentNodeStatus]:
    node_id = str(node.id)
    token = decrypt_secret(node.agent_token_enc) if node.agent_token_enc else None
    st = await fetch_agent_status(
        host=node.host,
        token=token,
        agent_port=node.agent_port,
        node_id=node_id,
    )

    if is_token_mismatch(st):
        now = asyncio.get_running_loop().time()
        last = _token_sync_last_try.get(node_id, 0.0)
        if now - last >= _TOKEN_SYNC_COOLDOWN_SEC:
            _token_sync_last_try[node_id] = now
            password = decrypt_secret(node.password_enc) if node.password_enc else None
            private_key = (
                decrypt_secret(node.private_key_enc) if node.private_key_enc else None
            )
            try:
                remote_token = await asyncio.to_thread(
                    repair_agent_auth_via_ssh,
                    host=node.host,
                    ssh_port=node.ssh_port,
                    username=node.ssh_user,
                    auth_type=node.auth_type,
                    password=password,
                    private_key=private_key,
                    agent_port=node.agent_port or AGENT_PORT_DEFAULT,
                )
                # Own session — gather() must not commit the shared request session.
                async with SessionLocal() as sync_db:
                    row = await sync_db.get(Node, node.id)
                    if row:
                        row.agent_token_enc = encrypt_secret(remote_token)
                        await sync_db.commit()
                clear_agent_status_cache(node_id)
                st = await fetch_agent_status(
                    host=node.host,
                    token=remote_token,
                    agent_port=node.agent_port,
                    node_id=node_id,
                )
                if is_token_mismatch(st):
                    st.error = (
                        "Токен с VPS прочитан и агент перезапущен, но /metrics "
                        "всё ещё отклоняет запрос — переустановите агент"
                    )
            except TokenSyncError as exc:
                st.error = (
                    f"Токен не совпадает; автосинхронизация по SSH не удалась: {exc.message}"
                )
            except Exception as exc:  # noqa: BLE001
                st.error = (
                    f"Токен не совпадает; автосинхронизация по SSH не удалась: {exc}"
                )

    hosting_bw = node.hosting.bandwidth_mbps if node.hosting is not None else None
    online = (online_by_address or {}).get(node.host)
    return node_id, _status_to_schema(st, hosting_bw, online)


@router.get("/agents", response_model=NodesAgentResponse)
async def nodes_agents(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodesAgentResponse:
    result = await db.execute(select(Node).options(selectinload(Node.hosting)))
    nodes = list(result.scalars().all())
    from app.services.agent_version import get_bundled_agent_version
    from app.services.remnawave_api import rw_users_online_by_address
    from app.services.wgcf_releases import get_latest_wgcf_version

    by_address = await asyncio.to_thread(rw_users_online_by_address)
    pairs = await asyncio.gather(*(_probe_one_node(n, by_address) for n in nodes))
    xray_online = {
        str(n.id): by_address[n.host] for n in nodes if n.host in by_address
    }

    return NodesAgentResponse(
        statuses=dict(pairs),
        latest_agent_version=get_bundled_agent_version(),
        latest_wgcf_version=await get_latest_wgcf_version(),
        xray_online=xray_online,
    )


@router.get("/agents/stream")
async def nodes_agents_stream(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Same probes as GET /agents, but emit each node as soon as it answers."""
    result = await db.execute(select(Node).options(selectinload(Node.hosting)))
    nodes = list(result.scalars().all())
    for node in nodes:
        db.expunge(node)

    async def event_stream() -> AsyncIterator[bytes]:
        from app.services.remnawave_api import rw_users_online_by_address

        by_address = await asyncio.to_thread(rw_users_online_by_address)
        tasks = [asyncio.create_task(_probe_one_node(n, by_address)) for n in nodes]
        try:
            for fut in asyncio.as_completed(tasks):
                node_id, st = await fut
                yield (
                    json.dumps(
                        {
                            "type": "node",
                            "id": node_id,
                            "status": st.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")
            from app.services.agent_version import get_bundled_agent_version
            from app.services.wgcf_releases import get_latest_wgcf_version

            yield (
                json.dumps(
                    {
                        "type": "done",
                        "latest_agent_version": get_bundled_agent_version(),
                        "latest_wgcf_version": await get_latest_wgcf_version(),
                        "xray_online": {
                            str(n.id): by_address[n.host]
                            for n in nodes
                            if n.host in by_address
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: NodeCreate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeOut:
    _validate_secrets(body.auth_type, body.password, body.private_key, required=True)
    await _ensure_hosting(db, body.hosting_id)
    private_key_enc = None
    if body.private_key:
        normalized = normalize_private_key(body.private_key, body.private_key_passphrase)
        private_key_enc = encrypt_secret(normalized)
    node = Node(
        name=body.name.strip(),
        host=body.host.strip(),
        ssh_port=body.ssh_port,
        ssh_user=body.ssh_user.strip(),
        auth_type=body.auth_type,
        password_enc=encrypt_secret(body.password) if body.password else None,
        private_key_enc=private_key_enc,
        hosting_id=body.hosting_id,
        country_code=body.country_code.upper().strip() if body.country_code else None,
        notes=body.notes,
    )
    db.add(node)
    await db.commit()
    return _to_out(await _get_node(db, node.id))


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeOut:
    return _to_out(await _get_node(db, node_id))


@router.get("/{node_id}/metrics", response_model=NodesMetricsResponse)
async def node_metrics(
    node_id: uuid.UUID,
    range_key: MetricRange = Query(default="day", alias="range"),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodesMetricsResponse:
    await _get_node(db, node_id)
    step, from_ts, to_ts, series = await fetch_series(db, range_key=range_key, node_id=node_id)
    return NodesMetricsResponse(range=range_key, step_sec=step, from_ts=from_ts, to_ts=to_ts, series=series)


@router.post("/{node_id}/reset-metrics")
async def reset_node_metrics(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Стереть историю метрик одной ноды, не трогая саму ноду и доступы."""
    await _get_node(db, node_id)
    deleted = await clear_node_samples(db, node_id)
    return {"deleted": deleted}


@router.patch("/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: uuid.UUID,
    body: NodeUpdate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    data = body.model_dump(exclude_unset=True)
    auth_type = data.get("auth_type", node.auth_type)

    if "name" in data and data["name"] is not None:
        node.name = data["name"].strip()
    if "host" in data and data["host"] is not None:
        node.host = data["host"].strip()
    if "ssh_port" in data and data["ssh_port"] is not None:
        node.ssh_port = data["ssh_port"]
    if "ssh_user" in data and data["ssh_user"] is not None:
        node.ssh_user = data["ssh_user"].strip()
    if "auth_type" in data and data["auth_type"] is not None:
        node.auth_type = data["auth_type"]
    if body.clear_hosting:
        node.hosting_id = None
    elif "hosting_id" in data:
        await _ensure_hosting(db, data["hosting_id"])
        node.hosting_id = data["hosting_id"]
    if "country_code" in data:
        node.country_code = data["country_code"].upper().strip() if data["country_code"] else None
    if "notes" in data:
        node.notes = data["notes"]

    # Secrets: empty / omitted fields must NOT wipe stored credentials.
    # Clear only via explicit clear_password / clear_private_key.
    if body.clear_password:
        node.password_enc = None
    elif "password" in data:
        pw = data["password"]
        if isinstance(pw, str) and pw.strip():
            node.password_enc = encrypt_secret(pw)

    if body.clear_private_key:
        node.private_key_enc = None
    elif "private_key" in data:
        key = data["private_key"]
        if isinstance(key, str) and key.strip():
            normalized = normalize_private_key(key, body.private_key_passphrase)
            node.private_key_enc = encrypt_secret(normalized)

    if auth_type == AuthType.password.value and not node.password_enc:
        raise HTTPException(status_code=400, detail="Для типа password нужен пароль")
    if auth_type == AuthType.private_key.value and not node.private_key_enc:
        raise HTTPException(status_code=400, detail="Для типа private_key нужен приватный ключ")

    await db.commit()
    return _to_out(await _get_node(db, node_id))


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    await db.delete(node)
    await db.commit()


@router.get("/{node_id}/secret", response_model=NodeSecretOut)
async def get_node_secret(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeSecretOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    if node.auth_type == AuthType.password.value:
        if not node.password_enc:
            raise HTTPException(status_code=404, detail="Пароль не задан")
        return NodeSecretOut(auth_type="password", secret=decrypt_secret(node.password_enc))

    if not node.private_key_enc:
        raise HTTPException(status_code=404, detail="Ключ не задан")
    return NodeSecretOut(auth_type="private_key", secret=decrypt_secret(node.private_key_enc))


@router.post("/{node_id}/ssh-check", response_model=SshCheckOut)
async def ssh_check_node(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SshCheckOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None

    result = await asyncio.to_thread(
        check_ssh_auth,
        host=node.host,
        port=node.ssh_port,
        username=node.ssh_user,
        auth_type=node.auth_type,
        password=password,
        private_key=private_key,
    )
    return SshCheckOut(ok=result.ok, message=result.message)


@router.post("/{node_id}/reboot", response_model=NodeRebootOut)
async def reboot_node(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeRebootOut:
    """SSH reboot (systemctl reboot / reboot / shutdown -r now)."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None

    try:
        result = await asyncio.to_thread(
            reboot_node_via_ssh,
            host=node.host,
            ssh_port=node.ssh_port,
            username=node.ssh_user,
            auth_type=node.auth_type,
            password=password,
            private_key=private_key,
        )
    except NodeRebootError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    return NodeRebootOut(ok=result.ok, message=result.message)


async def _persist_agent_credentials(
    node_id: uuid.UUID,
    *,
    token: str,
    agent_port: int,
    ssh_user: str | None = None,
    ssh_password: str | None = None,
) -> NodeOut:
    """Own session — request-scoped db may be closed during StreamingResponse."""
    async with SessionLocal() as session:
        node = await session.get(Node, node_id)
        if not node:
            raise RuntimeError("Нода не найдена при сохранении токена агента")
        node.agent_token_enc = encrypt_secret(token)
        node.agent_port = agent_port
        if ssh_user and ssh_user != node.ssh_user:
            node.ssh_user = ssh_user
        if ssh_password:
            node.password_enc = encrypt_secret(ssh_password)
            node.auth_type = AuthType.password.value
        await session.commit()
        return _to_out(await _get_node(session, node_id))


async def _persist_ssh_password(node_id: uuid.UUID, password: str) -> None:
    async with SessionLocal() as session:
        node = await session.get(Node, node_id)
        if not node:
            return
        node.password_enc = encrypt_secret(password)
        node.auth_type = AuthType.password.value
        await session.commit()


@router.post("/{node_id}/agent/install")
async def install_node_agent(
    node_id: uuid.UUID,
    install_deps: bool = Query(
        False,
        description="If true, install missing runtime deps (python3) via package manager",
    ),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON stream: {type:log|done|error, ...} — live VPS install output."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    token = secrets.token_urlsafe(32)
    agent_port = node.agent_port or AGENT_PORT_DEFAULT
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type

    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def worker() -> None:
            try:
                resolved: list[str] = []
                resolved_password: list[str] = []
                for line in install_agent_via_ssh(
                    host=host,
                    ssh_port=ssh_port,
                    username=username,
                    auth_type=auth_type,
                    password=password,
                    private_key=private_key,
                    token=token,
                    agent_port=agent_port,
                    install_deps=install_deps,
                    resolved_username=resolved,
                    resolved_password=resolved_password,
                    cancel=cancel,
                ):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": line})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                # Persist before client disconnects — token must match VPS env.
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        _persist_agent_credentials(
                            node_id,
                            token=token,
                            agent_port=agent_port,
                            ssh_user=resolved[0] if resolved else None,
                            ssh_password=resolved_password[0] if resolved_password else None,
                        ),
                        loop,
                    )
                    node_out = fut.result(timeout=60)
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "done",
                            "ok": True,
                            "message": f"Агент установлен и запущен на порту {agent_port}",
                            "agent_port": agent_port,
                            "node": node_out.model_dump(mode="json"),
                        },
                    )
                except AgentInstallCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "error",
                            "message": (
                                f"Агент на ноде запущен, но токен не сохранён в панели: {exc}. "
                                "Переустановите агент."
                            ),
                        },
                    )
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except AgentInstallError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Неизвестная ошибка установки"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{node_id}/scripts/run")
async def run_node_script(
    node_id: uuid.UUID,
    body: RemnaScriptRunRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON stream for RemnaNode install/reinstall/tune scripts."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    secret_key = (body.secret_key or "").strip()
    if body.action in ("install", "reinstall") and not secret_key:
        row = await db.get(AppSetting, "remna_secret_key")
        if row and row.value_enc:
            secret_key = decrypt_secret(row.value_enc)
    if body.action in ("install", "reinstall") and not secret_key:
        raise HTTPException(
            status_code=400,
            detail="Укажите SECRET_KEY в форме или сохраните его в настройках панели",
        )

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type

    params = RemnaScriptParams(
        action=body.action,
        node_port=body.node_port,
        secret_key=secret_key,
        additional_ports=body.additional_ports or "",
        mtu_ddos=body.mtu_ddos,
        gaming=body.gaming,
        swap=body.swap,
        swap_size=body.swap_size or "1G",
        cache_size=body.cache_size or "1G",
        disable_ipv6=body.disable_ipv6,
        use_origin=body.use_origin,
        origin_domain=(body.origin_domain or "").strip(),
        tune_mtu=body.tune_mtu,
        tune_gaming=body.tune_gaming,
        tune_swap=body.tune_swap,
        tune_ports=body.tune_ports,
        tune_ipv6=body.tune_ipv6,
        skip_system_update=body.skip_system_update,
        cf_204_stub=body.cf_204_stub,
    )

    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def worker() -> None:
            ssh_password = password
            try:
                resolved_password: list[str] = []
                for line in run_remnanode_script_via_ssh(
                    host=host,
                    ssh_port=ssh_port,
                    username=username,
                    auth_type=auth_type,
                    password=ssh_password,
                    private_key=private_key,
                    params=params,
                    cancel=cancel,
                    resolved_password=resolved_password,
                ):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": line})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                if resolved_password:
                    try:
                        fut = asyncio.run_coroutine_threadsafe(
                            _persist_ssh_password(node_id, resolved_password[0]),
                            loop,
                        )
                        fut.result(timeout=30)
                    except Exception as exc:  # noqa: BLE001
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {
                                "type": "log",
                                "line": f"✗ Не удалось сохранить новый SSH-пароль в панели: {exc}",
                            },
                        )
                    else:
                        ssh_password = resolved_password[0]
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {
                                "type": "log",
                                "line": "✓ Новый SSH-пароль сохранён в панели",
                            },
                        )
                if body.action in ("install", "reinstall") and body.cf_204_stub:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "log", "line": ""},
                    )
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "log", "line": "── Заглушка cf_204 ──"},
                    )
                    for line in run_probe_stub_via_ssh(
                        host=host,
                        ssh_port=ssh_port,
                        username=username,
                        auth_type=auth_type,
                        password=ssh_password,
                        private_key=private_key,
                        node_name=node.name,
                        patch_profile=True,
                        cancel=cancel,
                    ):
                        if cancel.is_set():
                            raise AgentInstallCancelled()
                        loop.call_soon_threadsafe(
                            queue.put_nowait, {"type": "log", "line": line}
                        )
                if cancel.is_set():
                    raise AgentInstallCancelled()
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "done",
                        "ok": True,
                        "message": f"Скрипт «{body.action}» выполнен на {host}",
                    },
                )
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except RemnaScriptError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Ошибка скрипта"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{node_id}/warp/install")
async def install_node_warp(
    node_id: uuid.UUID,
    body: WarpInstallRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON stream: wgcf + wg-quick iface `warp` (no default route)."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type
    force = body.force
    node_id_str = str(node.id)
    from app.services.wgcf_releases import WGCF_FALLBACK_VERSION, get_latest_wgcf_version

    wgcf_version = (await get_latest_wgcf_version()) or WGCF_FALLBACK_VERSION

    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def worker() -> None:
            try:
                for line in run_warp_script_via_ssh(
                    host=host,
                    ssh_port=ssh_port,
                    username=username,
                    auth_type=auth_type,
                    password=password,
                    private_key=private_key,
                    force=force,
                    wgcf_version=wgcf_version,
                    cancel=cancel,
                ):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": line})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                clear_agent_status_cache(node_id_str)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "done",
                        "ok": True,
                        "message": f"WARP установлен на {host}",
                    },
                )
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except WarpScriptError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Ошибка установки WARP"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{node_id}/probe-stub/install")
async def install_node_probe_stub(
    node_id: uuid.UUID,
    body: ProbeStubInstallRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON stream: vpn-probe-stub + routing в профиле Remnawave."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type
    patch_profile = body.patch_profile

    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def worker() -> None:
            try:
                for line in run_probe_stub_via_ssh(
                    host=host,
                    ssh_port=ssh_port,
                    username=username,
                    auth_type=auth_type,
                    password=password,
                    private_key=private_key,
                    node_name=node.name,
                    patch_profile=patch_profile,
                    cancel=cancel,
                ):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": line})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "done",
                        "ok": True,
                        "message": f"Заглушка cf_204 установлена на {host}",
                    },
                )
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except ProbeStubScriptError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Ошибка установки заглушки"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/haproxy-preview", response_model=HaproxyPreviewOut)
async def preview_haproxy_config(
    body: HaproxyPreviewRequest,
    _: str = Depends(require_user),
) -> HaproxyPreviewOut:
    params = HaproxyParams(
        template=body.template,
        bind_port=body.bind_port,
        backend=body.backend,
        path_prefix=body.path_prefix,
        proxy_protocol=body.proxy_protocol,
        routes=[HaproxyRoute(listen=r.listen, backend=r.backend) for r in body.routes],
    )
    try:
        config = render_haproxy_cfg(params)
    except HaproxyScriptError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return HaproxyPreviewOut(
        config=config,
        template=body.template,
        bind_port=body.bind_port,
        backend=body.backend,
        path_prefix=body.path_prefix,
        proxy_protocol=body.proxy_protocol,
        routes=body.routes,
    )


@router.get("/{node_id}/haproxy", response_model=HaproxyStatusOut)
async def get_node_haproxy(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> HaproxyStatusOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    status = await asyncio.to_thread(
        fetch_haproxy_status,
        host=node.host,
        ssh_port=node.ssh_port,
        username=node.ssh_user,
        auth_type=node.auth_type,
        password=password,
        private_key=private_key,
    )
    parsed = None
    if status.parsed is not None:
        parsed = HaproxyParsedOut(
            template=status.parsed.template,
            bind_port=status.parsed.bind_port,
            backend=status.parsed.backend,
            path_prefix=status.parsed.path_prefix,
            proxy_protocol=status.parsed.proxy_protocol,
            routes=[
                {"listen": r.listen, "backend": r.backend} for r in status.parsed.routes
            ],
        )
    return HaproxyStatusOut(
        installed=status.installed,
        running=status.running,
        enabled=status.enabled,
        version=status.version,
        config=status.config,
        listen=status.listen,
        valid=status.valid,
        error=status.error,
        parsed=parsed,
    )


@router.get("/{node_id}/haproxy-diag", response_model=HaproxyDiagOut)
async def get_node_haproxy_diag(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> HaproxyDiagOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    result = await asyncio.to_thread(
        fetch_haproxy_diag,
        host=node.host,
        ssh_port=node.ssh_port,
        username=node.ssh_user,
        auth_type=node.auth_type,
        password=password,
        private_key=private_key,
    )
    return HaproxyDiagOut(lines=result.lines, error=result.error)


def _haproxy_stats_out(node: Node, stats) -> HaproxyLiveStatsOut:
    return HaproxyLiveStatsOut(
        node_id=str(node.id),
        node_name=node.name,
        host=node.host,
        uptime=stats.uptime,
        curr_conns=stats.curr_conns,
        cum_conns=stats.cum_conns,
        conn_rate=stats.conn_rate,
        bin=stats.bin,
        bout=stats.bout,
        rows=[
            HaproxyStatRowOut(
                pxname=r.pxname,
                svname=r.svname,
                scur=r.scur,
                smax=r.smax,
                stot=r.stot,
                bin=r.bin,
                bout=r.bout,
                rate=r.rate,
                rate_max=r.rate_max,
                status=r.status,
                ereq=r.ereq,
                econ=r.econ,
                eresp=r.eresp,
                wretr=r.wretr,
                wredis=r.wredis,
                lastsess=r.lastsess,
            )
            for r in stats.rows
        ],
        sessions=[
            HaproxySessionOut(
                raw=s.raw,
                src=s.src,
                frontend=s.frontend,
                backend=s.backend,
                age=s.age,
            )
            for s in stats.sessions
        ],
        history=[
            HaproxyHistoryPointOut(
                ts=p.ts,
                curr_conns=p.curr_conns,
                conn_rate=p.conn_rate,
                bin=p.bin,
                bout=p.bout,
            )
            for p in stats.history
        ],
        errors=stats.errors,
        error=stats.error,
    )


@router.get("/{node_id}/haproxy-stats", response_model=HaproxyLiveStatsOut)
async def get_node_haproxy_stats(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> HaproxyLiveStatsOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    stats = await asyncio.to_thread(
        fetch_haproxy_stats,
        host=node.host,
        ssh_port=node.ssh_port,
        username=node.ssh_user,
        auth_type=node.auth_type,
        password=password,
        private_key=private_key,
        node_id=str(node.id),
    )
    return _haproxy_stats_out(node, stats)


@router.get("/{node_id}/capacity", response_model=VpsCapacityOut)
async def get_node_capacity(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> VpsCapacityOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    result = await asyncio.to_thread(
        fetch_vps_capacity,
        host=node.host,
        ssh_port=node.ssh_port,
        username=node.ssh_user,
        auth_type=node.auth_type,
        password=password,
        private_key=private_key,
    )
    return VpsCapacityOut(
        hostname=result.hostname,
        os=result.os,
        virt=result.virt,
        cpu_cores=result.cpu_cores,
        cpu_model=result.cpu_model,
        ram_total_mb=result.ram_total_mb,
        ram_avail_mb=result.ram_avail_mb,
        disk_total_gb=result.disk_total_gb,
        disk_free_gb=result.disk_free_gb,
        loadavg=result.loadavg,
        haproxy=result.haproxy,
        haproxy_up=result.haproxy_up,
        docker=result.docker,
        remnanode=result.remnanode,
        tcp_estab=result.tcp_estab,
        conntrack_max=result.conntrack_max,
        conntrack_count=result.conntrack_count,
        comfort=result.comfort,
        ceiling=result.ceiling,
        panel_users=result.panel_users,
        limiter=result.limiter,
        summary=result.summary,
        notes=result.notes,
        error=result.error,
    )


@router.post("/{node_id}/haproxy")
async def run_node_haproxy(
    node_id: uuid.UUID,
    body: HaproxyRunRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON stream: install / apply / reload / start / stop / uninstall HAProxy."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type
    node_id_str = str(node.id)
    params = HaproxyParams(
        action=body.action,
        force=body.force,
        template=body.template,
        bind_port=body.bind_port,
        backend=body.backend,
        path_prefix=body.path_prefix,
        proxy_protocol=body.proxy_protocol,
        routes=[HaproxyRoute(listen=r.listen, backend=r.backend) for r in body.routes],
        config=body.config,
    )
    from app.services.haproxy_script import validate_params

    try:
        validate_params(params)
    except HaproxyScriptError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    done_msg = {
        "install": f"HAProxy установлен на {host}",
        "apply": f"Конфиг HAProxy применён на {host}",
        "reload": f"HAProxy reload на {host}",
        "start": f"HAProxy запущен на {host}",
        "stop": f"HAProxy остановлен на {host}",
        "uninstall": f"HAProxy удалён с {host}",
    }.get(body.action, f"HAProxy: {body.action} на {host}")

    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def worker() -> None:
            try:
                for line in run_haproxy_script_via_ssh(
                    host=host,
                    ssh_port=ssh_port,
                    username=username,
                    auth_type=auth_type,
                    password=password,
                    private_key=private_key,
                    params=params,
                    cancel=cancel,
                ):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": line})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                clear_agent_status_cache(node_id_str)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "done", "ok": True, "message": done_msg},
                )
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except HaproxyScriptError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Ошибка HAProxy"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _ndjson_line(item: dict) -> bytes:
    return (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")


def _ndjson_dest_job(worker) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel = threading.Event()

        def run() -> None:
            try:
                payload: dict | None = None
                for item in worker(cancel):
                    if cancel.is_set():
                        raise AgentInstallCancelled()
                    if isinstance(item, dict):
                        payload = item
                    else:
                        loop.call_soon_threadsafe(queue.put_nowait, {"type": "log", "line": item})
                if cancel.is_set():
                    raise AgentInstallCancelled()
                if payload is None:
                    raise DestPickError("нода не вернула результат")
                done = {"type": "done", "ok": True, "message": "готово", **payload}
                loop.call_soon_threadsafe(queue.put_nowait, done)
            except AgentInstallCancelled:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": AgentInstallCancelled.message},
                )
            except (DestPickError, HaproxyScriptError, RemnaScriptError) as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": exc.message},
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": str(exc) or "Ошибка подбора прикрытия"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # First bytes immediately — otherwise nginx/uvicorn sit silent until SSH.
        yield _ndjson_line({"type": "log", "line": "панель: поток открыт, стартую SSH…"})

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _ndjson_line(item)
        except (asyncio.CancelledError, GeneratorExit):
            cancel.set()
            raise
        finally:
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (TimeoutError, asyncio.CancelledError):
                pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{node_id}/dest-scan")
async def dest_scan_node(
    node_id: uuid.UUID,
    body: DestScanRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON: подбор REALITY dest с ноды. ru_only — только российские ресурсы."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    extra = [s.strip() for s in body.extra if s and s.strip()]
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type

    def worker(cancel: threading.Event):
        return run_dest_scan(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            ru_only=body.ru_only,
            scan_subnet=body.scan_subnet,
            extra=extra,
            limit=body.limit,
            country_code=node.country_code,
            node_name=node.name,
            cancel=cancel,
        )

    return _ndjson_dest_job(worker)


@router.post("/{node_id}/dest-loopback")
async def dest_loopback_node(
    node_id: uuid.UUID,
    body: DestLoopbackRequest,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """NDJSON: петля REALITY на свободном порту ноды."""
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    password = decrypt_secret(node.password_enc) if node.password_enc else None
    private_key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    host = node.host
    ssh_port = node.ssh_port
    username = node.ssh_user
    auth_type = node.auth_type

    def worker(cancel: threading.Event):
        return run_dest_loopback(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            dests=body.dests,
            port=body.port,
            cancel=cancel,
        )

    return _ndjson_dest_job(worker)
