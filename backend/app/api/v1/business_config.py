from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.business_config import BusinessConfigOut, BusinessConfigUpdate
from app.services.business_config_service import BusinessConfigService

router = APIRouter(prefix="/config", tags=["business-config"])


@router.get("", response_model=BusinessConfigOut)
async def get_business_config(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BusinessConfigOut:
    # Deliberately NOT permission-gated: the login screen and receipt
    # rendering need branding before a user is authenticated.
    return await BusinessConfigService(db).get()


@router.patch("", response_model=BusinessConfigOut)
async def update_business_config(
    payload: BusinessConfigUpdate,
    admin: Annotated[User, Depends(require_permission("config.edit"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BusinessConfigOut:
    return await BusinessConfigService(db).update(admin, payload)
