from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_bytes_with_passphrase, hash_password
from app.models.role import Role
from app.models.setup_lock import SetupLock
from app.models.user import User
from app.schemas.backup import RestoreResult
from app.schemas.setup import FirstUserCreate, SetupStatusOut
from app.services.backup.dump_restore import (
    compute_manifest,
    deserialize_dump,
    restore_all_tables,
)

FIRST_USER_ROLE = "ChemistOwner"


class SetupService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def status(self) -> SetupStatusOut:
        return SetupStatusOut(needs_setup=not await self._any_user_exists())

    async def create_first_user(self, payload: FirstUserCreate) -> None:
        if await self._any_user_exists():
            raise HTTPException(
                status_code=409,
                detail="Setup has already been completed. Log in instead.",
            )

        role_result = await self.db.execute(select(Role).where(Role.name == FIRST_USER_ROLE))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Role '{FIRST_USER_ROLE}' not found -- migrations may not "
                    "have run correctly."
                ),
            )

        # The actual guarantee, not the COUNT(*) check above (which is
        # a real safety net for the common case, but has a genuine
        # race window between two near-simultaneous requests -- proven
        # by actually running them concurrently, not just reasoned
        # about: both passed that check and both got a 204 before this
        # fix). SetupLock.id is always 1; a primary-key conflict on
        # this insert is atomic on every backend, including SQLite,
        # unlike row-locking (unreliable there -- see
        # stock_selection_service.py's docstring for the fuller
        # explanation of that specific limitation).
        self.db.add(SetupLock(id=1))
        self.db.add(
            User(
                full_name=payload.full_name,
                username=payload.username,
                hashed_password=hash_password(payload.password),
                role_id=role.id,
                security_question=payload.security_question,
                security_answer_hash=hash_password(payload.security_answer),
            )
        )
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Setup has already been completed. Log in instead.",
            ) from exc

    async def _any_user_exists(self) -> bool:
        count = await self.db.scalar(select(func.count()).select_from(User))
        return bool(count and count > 0)

    async def restore_from_migration_file(
        self, encrypted_bytes: bytes, passphrase: str
    ) -> RestoreResult:
        """
        The actual disaster-recovery / new-device path -- deliberately
        reachable before any login exists, because on a genuinely
        fresh install there is no user yet to log in as. Uses a
        passphrase the owner chose and remembers, not anything stored
        on this or the old machine, which is what makes this work on
        hardware that has never seen this data before. Restores the
        real users table directly, so afterward the person logs in
        with their real, original username and password -- not a
        fresh-install placeholder account.
        """
        if await self._any_user_exists():
            raise HTTPException(
                status_code=409,
                detail="Setup has already been completed. Log in instead.",
            )

        try:
            plaintext = decrypt_bytes_with_passphrase(encrypted_bytes, passphrase)
        except Exception as exc:  # noqa: BLE001 - any decrypt failure means one thing to the user
            raise HTTPException(
                status_code=400,
                detail="Could not open this backup -- wrong passphrase, or the file is corrupted.",
            ) from exc

        try:
            dump = deserialize_dump(plaintext)
        except Exception as exc:  # noqa: BLE001 - malformed content after a successful decrypt
            raise HTTPException(
                status_code=400,
                detail="The backup file's contents are not valid -- it may be corrupted.",
            ) from exc

        manifest = compute_manifest(dump)
        if "users" not in dump or not dump["users"]:
            raise HTTPException(
                status_code=400,
                detail="This file has no user accounts -- it doesn't look like a real backup.",
            )

        total_rows = await restore_all_tables(self.db, dump)
        await self.db.commit()

        return RestoreResult(
            backup_log_id=0,  # not from a logged backup -- an externally supplied file
            tables_restored=len(manifest),
            total_rows_restored=total_rows,
            manifest_matched=True,
        )
