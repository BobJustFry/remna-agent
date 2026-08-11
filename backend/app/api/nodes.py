import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_user
from app.models import AuthType, Node
from app.schemas import (
    NodeCreate,
    NodeOnlineStatus,
    NodeOut,
    NodeSecretOut,
    NodesOnlineResponse,
    NodeUpdate,
)
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.ping import check_many

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
        provider=node.provider,
        country_code=node.country_code,
        notes=node.notes,
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


@router.get("", response_model=list[NodeOut])
async def list_nodes(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[NodeOut]:
    result = await db.execute(select(Node).order_by(Node.name.asc()))
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
    statuses = {
        node_id: NodeOnlineStatus(
            online=item.online,
            latency_ms=item.latency_ms,
            method=item.method,
        )
        for node_id, item in checked.items()
    }
    return NodesOnlineResponse(statuses=statuses)


@router.post("", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: NodeCreate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeOut:
    _validate_secrets(body.auth_type, body.password, body.private_key, required=True)
    node = Node(
        name=body.name.strip(),
        host=body.host.strip(),
        ssh_port=body.ssh_port,
        ssh_user=body.ssh_user.strip(),
        auth_type=body.auth_type,
        password_enc=encrypt_secret(body.password) if body.password else None,
        private_key_enc=encrypt_secret(body.private_key) if body.private_key else None,
        provider=body.provider.strip() if body.provider else None,
        country_code=body.country_code.upper().strip() if body.country_code else None,
        notes=body.notes,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return _to_out(node)


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeOut:
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    return _to_out(node)


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
    if "provider" in data:
        node.provider = data["provider"].strip() if data["provider"] else None
    if "country_code" in data:
        node.country_code = data["country_code"].upper().strip() if data["country_code"] else None
    if "notes" in data:
        node.notes = data["notes"]

    if body.clear_password:
        node.password_enc = None
    elif body.password:
        node.password_enc = encrypt_secret(body.password)

    if body.clear_private_key:
        node.private_key_enc = None
    elif body.private_key:
        node.private_key_enc = encrypt_secret(body.private_key)

    if auth_type == AuthType.password.value and not node.password_enc:
        raise HTTPException(status_code=400, detail="Для типа password нужен пароль")
    if auth_type == AuthType.private_key.value and not node.private_key_enc:
        raise HTTPException(status_code=400, detail="Для типа private_key нужен приватный ключ")

    await db.commit()
    await db.refresh(node)
    return _to_out(node)


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
