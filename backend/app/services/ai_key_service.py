from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_secret
from app.models.ai_provider_key import AIProviderKey
from app.models.user import User
from app.schemas.ai import AIProviderKeyCreate, AIProviderKeyOut


class AIKeyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add_key(self, user: User, payload: AIProviderKeyCreate) -> AIProviderKeyOut:
        key_row = AIProviderKey(
            user_id=user.id,
            provider=payload.provider,
            encrypted_key=encrypt_secret(payload.api_key),
            key_hint=payload.api_key[-4:] if len(payload.api_key) >= 4 else payload.api_key,
            priority=payload.priority,
        )
        self.db.add(key_row)
        await self.db.commit()
        await self.db.refresh(key_row)
        return self._to_schema(key_row)

    async def list_keys(self, user: User) -> list[AIProviderKeyOut]:
        result = await self.db.execute(
            select(AIProviderKey)
            .where(AIProviderKey.user_id == user.id)
            .order_by(AIProviderKey.priority)
        )
        return [self._to_schema(k) for k in result.scalars().all()]

    async def delete_key(self, user: User, key_id: int) -> None:
        result = await self.db.execute(
            select(AIProviderKey).where(
                AIProviderKey.id == key_id, AIProviderKey.user_id == user.id
            )
        )
        key_row = result.scalar_one_or_none()
        if key_row is None:
            raise HTTPException(status_code=404, detail="API key not found")
        await self.db.delete(key_row)
        await self.db.commit()

    @staticmethod
    def _to_schema(key_row: AIProviderKey) -> AIProviderKeyOut:
        return AIProviderKeyOut(
            id=key_row.id,
            provider=key_row.provider,
            masked_key=f"••••{key_row.key_hint}",
            priority=key_row.priority,
            is_active=key_row.is_active,
            created_at=key_row.created_at,
            last_used_at=key_row.last_used_at,
        )
