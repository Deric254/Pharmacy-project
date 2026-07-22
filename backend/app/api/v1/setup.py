from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.setup import FirstUserCreate, SetupStatusOut
from app.services.setup_service import SetupService

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatusOut)
async def setup_status(db: Annotated[AsyncSession, Depends(get_db)]) -> SetupStatusOut:
    return await SetupService(db).status()


@router.post("/first-user", status_code=204)
async def create_first_user(
    payload: FirstUserCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> None:
    await SetupService(db).create_first_user(payload)
