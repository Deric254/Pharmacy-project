"""
Backup service.

`provider_override` is injectable specifically so tests can exercise
run_backup()/restore_backup() with an in-memory fake provider instead
of a real network call (same constraint as the AI module's third-party
APIs). Production code leaves it unset and the service picks the real
provider based on what was requested (run_backup) or what a specific
backup was actually saved with (restore_backup).

Local file is the default provider -- no connection, no setup, closes
a confirmed bug where every backup required Google Drive connected
first, with no offline path at all, directly contradicting this app's
whole design (one computer, no network dependency). Google Drive stays
available as an optional additional layer for anyone who wants an
off-site copy too.

Restore is confirmation-gated (the request must explicitly set
confirm=true) and verifies the downloaded manifest matches what was
recorded at backup time before touching a single row -- a mismatch
aborts before any DELETE runs.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import BackupFailedEvent, publish
from app.core.security import decrypt_bytes, encrypt_bytes, encrypt_bytes_with_passphrase
from app.models.audit_log import AuditLog
from app.models.backup import BackupLog, BackupOAuthToken, BackupProviderName, BackupStatus
from app.models.business_config import BusinessConfig
from app.models.user import User
from app.schemas.backup import BackupLogOut, ConnectGoogleDriveRequest, RestoreResult
from app.services.backup.base import BackupProvider, BackupProviderError
from app.services.backup.dump_restore import (
    compute_manifest,
    deserialize_dump,
    dump_all_tables,
    restore_all_tables,
    serialize_dump,
)
from app.services.backup.google_drive import GoogleDriveBackupProvider
from app.services.backup.local_file import LocalFileBackupProvider

_settings = get_settings()

_PROVIDER_REQUEST_MAP = {
    "local": BackupProviderName.LOCAL_FILE,
    "google_drive": BackupProviderName.GOOGLE_DRIVE,
}


class BackupService:
    def __init__(self, db: AsyncSession, provider_override: BackupProvider | None = None) -> None:
        self.db = db
        self._provider_override = provider_override

    async def connect_google_drive(self, user: User, payload: ConnectGoogleDriveRequest) -> None:
        result = await self.db.execute(
            select(BackupOAuthToken).where(
                BackupOAuthToken.provider == BackupProviderName.GOOGLE_DRIVE
            )
        )
        existing = result.scalar_one_or_none()
        encrypted = encrypt_bytes(payload.refresh_token.encode())

        if existing is not None:
            existing.encrypted_refresh_token = encrypted
            existing.connected_by_user_id = user.id
        else:
            self.db.add(
                BackupOAuthToken(
                    provider=BackupProviderName.GOOGLE_DRIVE,
                    encrypted_refresh_token=encrypted,
                    connected_by_user_id=user.id,
                )
            )
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="backup.google_drive_connected",
                entity_type="backup",
                entity_id="google_drive",
            )
        )
        await self.db.commit()

    async def export_for_migration(self, passphrase: str, user: User) -> bytes:
        """
        A deliberately separate path from run_backup(): encrypted with
        a passphrase the owner chooses and remembers, not this
        machine's own auto-generated key, and returned directly as
        bytes rather than stored in this system's own backup history
        -- this file is meant to leave the system entirely, carried to
        a new device for setup's restore-from-file flow to read back.
        A backup encrypted with this machine's own key could never be
        opened on different hardware; this is what makes that
        possible.
        """
        dump = await dump_all_tables(self.db)
        plaintext = serialize_dump(dump)
        encrypted = encrypt_bytes_with_passphrase(plaintext, passphrase)
        # The passphrase itself is never logged -- only that the
        # export happened, by whom, and how big the resulting file
        # was. This is a full, unencrypted-at-rest-in-plaintext-form
        # copy of every table leaving the system; who did it and when
        # is a non-negotiable, same as backup.restored below.
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="backup.exported_for_migration",
                entity_type="backup",
                entity_id="migration_export",
                new_value=f"size_bytes={len(encrypted)}",
            )
        )
        await self.db.commit()
        return encrypted

    async def run_backup(self, user: User, provider_choice: str = "local") -> BackupLogOut:
        provider_name = _PROVIDER_REQUEST_MAP[provider_choice]

        dump = await dump_all_tables(self.db)
        manifest = compute_manifest(dump)
        plaintext = serialize_dump(dump)
        encrypted_payload = encrypt_bytes(plaintext)

        provider = await self._resolve_provider(provider_name)
        filename = f"pharmacy-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.enc"

        try:
            reference = await provider.upload(filename, encrypted_payload)
        except BackupProviderError as exc:
            log = BackupLog(
                status=BackupStatus.FAILED,
                provider=provider_name,
                error_message=str(exc),
                created_by_user_id=user.id,
            )
            self.db.add(log)
            self.db.add(
                AuditLog(
                    user_id=user.id,
                    user_name_snapshot=user.full_name,
                    action="backup.run_failed",
                    entity_type="backup",
                    entity_id="pending",
                    new_value=f"provider={provider_choice} error={exc}",
                )
            )
            await self.db.commit()
            await self.db.refresh(log)
            await publish(BackupFailedEvent(reason=str(exc)))
            return BackupLogOut.model_validate(log)

        log = BackupLog(
            status=BackupStatus.SUCCESS,
            provider=provider_name,
            reference=reference,
            manifest_json=json.dumps(manifest),
            size_bytes=len(encrypted_payload),
            created_by_user_id=user.id,
        )
        self.db.add(log)
        await self.db.flush()
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="backup.run",
                entity_type="backup",
                entity_id=str(log.id),
                new_value=f"provider={provider_choice} size_bytes={log.size_bytes}",
            )
        )
        await self.db.commit()
        await self.db.refresh(log)
        return BackupLogOut.model_validate(log)

    async def list_backups(self) -> list[BackupLogOut]:
        result = await self.db.execute(select(BackupLog).order_by(BackupLog.created_at.desc()))
        return [BackupLogOut.model_validate(b) for b in result.scalars().all()]

    async def restore_backup(self, backup_log_id: int, confirm: bool, user: User) -> RestoreResult:
        if not confirm:
            raise HTTPException(
                status_code=400,
                detail="Restore requires explicit confirm=true - this is destructive",
            )

        result = await self.db.execute(select(BackupLog).where(BackupLog.id == backup_log_id))
        log = result.scalar_one_or_none()
        if log is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        if log.status != BackupStatus.SUCCESS or log.reference is None:
            raise HTTPException(status_code=400, detail="Only a successful backup can be restored")

        # Restoring uses whatever provider this specific backup was
        # actually saved with -- not a fresh choice. A backup made
        # locally must be read back from local disk regardless of
        # whether Google Drive happens to be connected right now.
        provider = await self._resolve_provider(log.provider)
        try:
            encrypted_payload = await provider.download(log.reference)
        except BackupProviderError as exc:
            raise HTTPException(
                status_code=502, detail=f"Could not download backup: {exc}"
            ) from exc

        plaintext = decrypt_bytes(encrypted_payload)
        dump = deserialize_dump(plaintext)

        recorded_manifest: dict[str, int] = json.loads(log.manifest_json or "{}")
        actual_manifest = compute_manifest(dump)
        manifest_matched = recorded_manifest == actual_manifest
        if not manifest_matched:
            # Committed before raising, not left for the exception to
            # discard -- a refused restore due to a corrupted or
            # tampered file is exactly the kind of event that must
            # survive on the record even though nothing else in this
            # request landed.
            self.db.add(
                AuditLog(
                    user_id=user.id,
                    user_name_snapshot=user.full_name,
                    action="backup.restore_refused",
                    entity_type="backup",
                    entity_id=str(log.id),
                    new_value="manifest mismatch - possible corruption or tampering",
                )
            )
            await self.db.commit()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Downloaded backup's manifest does not match what was recorded at "
                    "backup time - refusing to restore. The file may be corrupted or "
                    "tampered with."
                ),
            )

        total_rows = await restore_all_tables(self.db, dump)

        log.restored_at = datetime.now(UTC)
        # The single most consequential action in this system -- it
        # overwrites the live database. Same audit non-negotiable as
        # everything else that moves real stock, real money, or (as
        # here) the entire dataset at once.
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="backup.restored",
                entity_type="backup",
                entity_id=str(log.id),
                new_value=f"tables_restored={len(actual_manifest)} rows_restored={total_rows}",
            )
        )
        await self.db.commit()

        return RestoreResult(
            backup_log_id=log.id,
            tables_restored=len(actual_manifest),
            total_rows_restored=total_rows,
            manifest_matched=manifest_matched,
        )

    async def _resolve_provider(self, provider_name: BackupProviderName) -> BackupProvider:
        if self._provider_override is not None:
            return self._provider_override

        if provider_name == BackupProviderName.LOCAL_FILE:
            config_result = await self.db.execute(select(BusinessConfig).limit(1))
            config_row = config_result.scalar_one_or_none()
            override = config_row.local_backup_dir_override if config_row else None
            backup_dir = Path(override) if override else _settings.local_backup_dir
            return LocalFileBackupProvider(backup_dir)

        result = await self.db.execute(
            select(BackupOAuthToken).where(
                BackupOAuthToken.provider == BackupProviderName.GOOGLE_DRIVE
            )
        )
        token_row = result.scalar_one_or_none()
        if token_row is None:
            raise HTTPException(
                status_code=400,
                detail="Google Drive is not connected yet - connect it in Backup settings first",
            )

        refresh_token = decrypt_bytes(token_row.encrypted_refresh_token).decode()
        return GoogleDriveBackupProvider(
            refresh_token=refresh_token,
            client_id=_settings.google_oauth_client_id,
            client_secret=_settings.google_oauth_client_secret,
        )
