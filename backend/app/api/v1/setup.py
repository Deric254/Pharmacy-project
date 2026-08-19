from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import redis_client
from app.schemas.backup import RestoreResult
from app.schemas.setup import FirstUserCreate, SetupStatusOut
from app.services.setup_service import SetupService

router = APIRouter(prefix="/setup", tags=["setup"])

_RESTORE_RATE_LIMIT = 3
_RESTORE_RATE_WINDOW = 60 * 60
_MAX_MIGRATION_FILE_BYTES = 50 * 1024 * 1024


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
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str, Form()],
) -> RestoreResult:
    """
    The real disaster-recovery entry point: no login required, because
    a genuinely fresh device has no user to log in as yet. The real
    users table comes back from the file itself, so the owner logs in
    afterward with their actual original credentials, not a
    fresh-install placeholder.
    """
    client_ip = request.client.host if request.client else None
    rate_limit_key = f"restore_attempts:{client_ip or 'unknown'}"
    attempt_count = await redis_client.get(rate_limit_key)
    if attempt_count is not None and int(attempt_count) >= _RESTORE_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many restore attempts. Try again later.",
        )

    await redis_client.incr(rate_limit_key)
    await redis_client.expire(rate_limit_key, _RESTORE_RATE_WINDOW)
    if file.size is not None and file.size > _MAX_MIGRATION_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Backup file is too large")
    content = await file.read()
    if len(content) > _MAX_MIGRATION_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Backup file is too large")
    result = await SetupService(db).restore_from_migration_file(content, passphrase)
    return result
