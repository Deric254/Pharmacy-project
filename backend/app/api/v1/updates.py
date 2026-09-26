from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.rbac import get_current_user
from app.models.user import User
from app.schemas.update_check import ReleaseOptionOut, UpdateInfoOut
from app.services.update_check_service import UpdateCheckError, UpdateCheckService

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("/latest", response_model=UpdateInfoOut | None)
async def get_latest_update(
    _current_user: Annotated[User, Depends(get_current_user)],
    force: bool = False,
) -> dict[str, object] | None:
    # Login-gated only -- no specific permission required, since every
    # role should see the same "an update is available" banner.
    return await UpdateCheckService().get_latest(force=force)


@router.get("/releases", response_model=list[ReleaseOptionOut])
async def get_release_history(
    _current_user: Annotated[User, Depends(get_current_user)],
    force: bool = False,
) -> list[dict[str, object]]:
    try:
        return await UpdateCheckService().get_releases(force=force)
    except UpdateCheckError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
