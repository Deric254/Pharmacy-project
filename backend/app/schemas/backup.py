from datetime import datetime

from pydantic import BaseModel, Field

from app.models.backup import BackupProviderName, BackupStatus


class ConnectGoogleDriveRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


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
