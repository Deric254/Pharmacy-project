from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.role import Role
from app.models.setup_lock import SetupLock
from app.models.user import User
from app.schemas.setup import FirstUserCreate, SetupStatusOut

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
