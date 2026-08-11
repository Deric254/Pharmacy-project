from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.backup import RestoreResult
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


@router.post("/restore-from-file", response_model=RestoreResult)
async def restore_from_migration_file(
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile,
    passphrase: Annotated[str, Form()],
) -> RestoreResult:
    """
    The real disaster-recovery entry point: no login required, because
    a genuinely fresh device has no user to log in as yet. The real
    users table comes back from the file itself, so the owner logs in
    afterward with their actual original credentials, not a
    fresh-install placeholder.
    """
    content = await file.read()
    return await SetupService(db).restore_from_migration_file(content, passphrase)
