from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.backup import BackupProviderName, BackupStatus


class ConnectGoogleDriveRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RunBackupRequest(BaseModel):
    # Defaults to local -- no connection required, this is what closes
    # the confirmed bug where every backup attempt needed Google Drive
    # connected first with no offline path at all.
    provider: Literal["local", "google_drive"] = "local"


class MigrationExportRequest(BaseModel):
    # A real minimum -- a weak passphrase here would defeat the whole
    # point of protecting a file that's meant to be carried off this
    # machine and potentially stored somewhere less controlled (a USB
    # drive, a cloud folder) than the database itself.
    passphrase: str = Field(min_length=8, max_length=200)


class BackupLogOut(BaseModel):
    id: int
    status: BackupStatus
    provider: BackupProviderName
    reference: str | None
    size_bytes: int | None
    error_message: str | None
    created_at: datetime
    restored_at: datetime | None

    model_config = {"from_attributes": True}


class RestoreRequest(BaseModel):
    confirm: bool = Field(description="Must be explicitly true -- restore is destructive")


class RestoreResult(BaseModel):
    backup_log_id: int
    tables_restored: int
    total_rows_restored: int
    manifest_matched: bool
