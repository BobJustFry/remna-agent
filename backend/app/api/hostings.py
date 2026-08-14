import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_user
from app.models import Hosting
from app.schemas import HostingCreate, HostingOut, HostingUpdate
from app.services.favicon import fetch_favicon_data_url, normalize_website_url

router = APIRouter(prefix="/hostings", tags=["hostings"])


async def _apply_favicon(hosting: Hosting) -> None:
    if not hosting.website_url:
        hosting.favicon_data = None
        return
    hosting.favicon_data = await fetch_favicon_data_url(hosting.website_url)


@router.get("", response_model=list[HostingOut])
async def list_hostings(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[Hosting]:
    result = await db.execute(select(Hosting).order_by(Hosting.name.asc()))
    return list(result.scalars().all())


@router.post("", response_model=HostingOut, status_code=status.HTTP_201_CREATED)
async def create_hosting(
    body: HostingCreate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Hosting:
    website_url = normalize_website_url(body.website_url)
    if body.website_url and body.website_url.strip() and not website_url:
        raise HTTPException(status_code=400, detail="Некорректный URL сайта")

    hosting = Hosting(
        name=body.name.strip(),
        website_url=website_url,
        notes=body.notes,
    )
    await _apply_favicon(hosting)
    db.add(hosting)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Хостинг с таким именем уже есть") from exc
    await db.refresh(hosting)
    return hosting


@router.get("/{hosting_id}", response_model=HostingOut)
async def get_hosting(
    hosting_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Hosting:
    hosting = await db.get(Hosting, hosting_id)
    if not hosting:
        raise HTTPException(status_code=404, detail="Хостинг не найден")
    return hosting


@router.patch("/{hosting_id}", response_model=HostingOut)
async def update_hosting(
    hosting_id: uuid.UUID,
    body: HostingUpdate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Hosting:
    hosting = await db.get(Hosting, hosting_id)
    if not hosting:
        raise HTTPException(status_code=404, detail="Хостинг не найден")

    url_changed = False
    if body.name is not None:
        hosting.name = body.name.strip()
    if "website_url" in body.model_fields_set:
        website_url = normalize_website_url(body.website_url)
        if body.website_url and body.website_url.strip() and not website_url:
            raise HTTPException(status_code=400, detail="Некорректный URL сайта")
        if website_url != hosting.website_url:
            url_changed = True
        hosting.website_url = website_url
    if "notes" in body.model_fields_set:
        hosting.notes = body.notes

    if url_changed:
        await _apply_favicon(hosting)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Хостинг с таким именем уже есть") from exc
    await db.refresh(hosting)
    return hosting


@router.post("/{hosting_id}/refresh-favicon", response_model=HostingOut)
async def refresh_favicon(
    hosting_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Hosting:
    hosting = await db.get(Hosting, hosting_id)
    if not hosting:
        raise HTTPException(status_code=404, detail="Хостинг не найден")
    if not hosting.website_url:
        raise HTTPException(status_code=400, detail="У хостинга не указан URL сайта")
    await _apply_favicon(hosting)
    await db.commit()
    await db.refresh(hosting)
    return hosting


@router.delete("/{hosting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hosting(
    hosting_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    hosting = await db.get(Hosting, hosting_id)
    if not hosting:
        raise HTTPException(status_code=404, detail="Хостинг не найден")
    await db.delete(hosting)
    await db.commit()
