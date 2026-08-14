from fastapi import APIRouter, Depends, Query

from app.deps import require_user
from app.schemas import RemnawaveVersionsOut
from app.services.remnawave_releases import get_remnawave_versions

router = APIRouter(prefix="/remnawave", tags=["remnawave"])


@router.get("/versions", response_model=RemnawaveVersionsOut)
async def remnawave_versions(
    force: bool = Query(default=False),
    _: str = Depends(require_user),
) -> RemnawaveVersionsOut:
    """Latest Remnawave Panel/Node tags from GitHub (cached ~30 min)."""
    data = await get_remnawave_versions(force=force)
    return RemnawaveVersionsOut(
        panel_version=data.panel_version,
        node_version=data.node_version,
        panel_url=data.panel_url,
        node_url=data.node_url,
        checked_at=data.checked_at,
        error=data.error,
    )
