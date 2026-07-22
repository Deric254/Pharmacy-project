from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.user import User
from app.schemas.user import RoleOut, UserCreate, UserListItemOut


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_user(self, payload: UserCreate, created_by: User | None) -> UserListItemOut:
        existing = await self.db.execute(select(User).where(User.username == payload.username))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Username already taken")

        role_result = await self.db.execute(select(Role).where(Role.id == payload.role_id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=400, detail="Unknown role_id")

        user = User(
            full_name=payload.full_name,
            username=payload.username,
            hashed_password=hash_password(payload.password),
            role_id=role.id,
            security_question=payload.security_question,
            security_answer_hash=(
                hash_password(payload.security_answer) if payload.security_answer else None
            ),
        )
        self.db.add(user)
        await self.db.flush()

        self.db.add(
            AuditLog(
                user_id=created_by.id if created_by else None,
                user_name_snapshot=created_by.full_name if created_by else None,
                action="user.created",
                entity_type="user",
                entity_id=str(user.id),
                new_value=f"username={user.username} role={role.name}",
            )
        )
        await self.db.commit()
        await self.db.refresh(user)
        return UserListItemOut(
            id=user.id,
            full_name=user.full_name,
            username=user.username,
            role_name=role.name,
            is_active=user.is_active,
        )

    async def list_users(self) -> list[UserListItemOut]:
        result = await self.db.execute(
            select(User).options(selectinload(User.role)).order_by(User.full_name)
        )
        return [
            UserListItemOut(
                id=u.id,
                full_name=u.full_name,
                username=u.username,
                role_name=u.role.name,
                is_active=u.is_active,
            )
            for u in result.scalars().all()
        ]

    async def deactivate_user(self, user_id: int, deactivated_by: User) -> None:
        if user_id == deactivated_by.id:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.is_active = False
        self.db.add(
            AuditLog(
                user_id=deactivated_by.id,
                user_name_snapshot=deactivated_by.full_name,
                action="user.deactivated",
                entity_type="user",
                entity_id=str(user_id),
            )
        )
        await self.db.commit()

    async def list_roles(self) -> list[RoleOut]:
        result = await self.db.execute(select(Role).order_by(Role.name))
        return [RoleOut.model_validate(r) for r in result.scalars().all()]
