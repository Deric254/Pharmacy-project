from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.backup import (
    BackupLogOut,
    ConnectGoogleDriveRequest,
    MigrationExportRequest,
    RestoreRequest,
    RestoreResult,
    RunBackupRequest,
)
from app.services.backup_service import BackupService

router = APIRouter(prefix="/backups", tags=["backups"])

_DEFAULT_RUN_BACKUP_REQUEST = RunBackupRequest()


@router.post("/export-for-migration")
async def export_for_migration(
    payload: MigrationExportRequest,
    user: Annotated[User, Depends(require_permission("backups.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    content = await BackupService(db).export_for_migration(payload.passphrase)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="pharmacy-migration-backup.enc"'},
    )


@router.get("", response_model=list[BackupLogOut])
async def list_backups(
    _: Annotated[User, Depends(require_permission("backups.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[BackupLogOut]:
    return await BackupService(db).list_backups()


@router.post("/connect-google-drive", status_code=204)
async def connect_google_drive(
    payload: ConnectGoogleDriveRequest,
    user: Annotated[User, Depends(require_permission("backups.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await BackupService(db).connect_google_drive(user, payload)


@router.post("/run", response_model=BackupLogOut, status_code=201)
async def run_backup(
    user: Annotated[User, Depends(require_permission("backups.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: RunBackupRequest = _DEFAULT_RUN_BACKUP_REQUEST,
) -> BackupLogOut:
    return await BackupService(db).run_backup(user, payload.provider)


@router.post("/{backup_id}/restore", response_model=RestoreResult)
async def restore_backup(
    backup_id: int,
    payload: RestoreRequest,
    user: Annotated[User, Depends(require_permission("backups.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RestoreResult:
    return await BackupService(db).restore_backup(backup_id, payload.confirm, user)
