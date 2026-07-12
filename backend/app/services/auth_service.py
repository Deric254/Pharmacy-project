"""
Auth service layer. Routes stay thin (parse request -> call service ->
return response); all logic and DB transaction boundaries live here.
This separation is what makes the module testable without spinning up
FastAPI at all — services are plain async functions/classes.
"""

import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.core.security import (
    create_token,
    hash_password,
    verify_password,
)
from app.models.audit_log import AuditLog
from app.models.user import User, UserSession

MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def authenticate(self, username: str, password: str, ip_address: str | None) -> User:
        rate_limit_key = f"login_attempts:{ip_address or 'unknown'}:{username}"
        attempt_count = await redis_client.get(rate_limit_key)
        if attempt_count is not None and int(attempt_count) >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again in a few minutes.",
            )

        result = await self.db.execute(
            select(User).where(User.username == username, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()

        if user is None or not verify_password(password, user.hashed_password):
            await self._record_failed_attempt(rate_limit_key)
            # Log failed attempts too — repeated failures are a security signal.
            self.db.add(
                AuditLog(
                    user_id=user.id if user else None,
                    action="login.failed",
                    entity_type="user",
                    entity_id=username,
                    ip_address=ip_address,
                )
            )
            await self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
            )

        await redis_client.delete(rate_limit_key)  # legitimate login clears any prior strikes
        self.db.add(
            AuditLog(
                user_id=user.id,
                action="login.success",
                entity_type="user",
                entity_id=str(user.id),
                ip_address=ip_address,
            )
        )
        await self.db.commit()
        return user

    @staticmethod
    async def _record_failed_attempt(rate_limit_key: str) -> None:
        new_count = await redis_client.incr(rate_limit_key)
        if new_count == 1:
            # Only set the expiry on the first strike in a fresh window,
            # so the window is a rolling 15 minutes from the first
            # failure, not extended indefinitely by each retry.
            await redis_client.expire(rate_limit_key, LOGIN_ATTEMPT_WINDOW_SECONDS)

    async def issue_tokens(
        self, user: User, device_label: str | None, ip_address: str | None
    ) -> dict[str, str]:
        jti = str(uuid.uuid4())
        access_token = create_token(subject=str(user.id), token_type="access")
        refresh_token = create_token(
            subject=str(user.id), token_type="refresh", extra_claims={"jti": jti}
        )

        self.db.add(
            UserSession(
                user_id=user.id,
                refresh_token_jti=jti,
                device_label=device_label,
                ip_address=ip_address,
            )
        )
        await self.db.commit()
        return {"access_token": access_token, "refresh_token": refresh_token}

    async def reset_password_via_security_question(
        self, username: str, security_answer: str, new_password: str
    ) -> None:
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        generic_error = HTTPException(
            status_code=400, detail="Unable to reset password with the provided details"
        )
        if user is None or user.security_answer_hash is None:
            # Deliberately generic error — don't reveal whether the username exists.
            raise generic_error

        if not verify_password(security_answer, user.security_answer_hash):
            raise generic_error

        user.hashed_password = hash_password(new_password)
        self.db.add(
            AuditLog(
                user_id=user.id,
                action="password.self_reset",
                entity_type="user",
                entity_id=str(user.id),
            )
        )
        await self.db.commit()

    async def admin_reset_password(
        self, admin: User, target_user_id: int, new_password: str
    ) -> None:
        result = await self.db.execute(select(User).where(User.id == target_user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        user.hashed_password = hash_password(new_password)
        self.db.add(
            AuditLog(
                user_id=admin.id,
                action="password.admin_reset",
                entity_type="user",
                entity_id=str(user.id),
                new_value=f"reset_by_admin_id={admin.id}",
            )
        )
        await self.db.commit()

    @staticmethod
    def generate_temp_password() -> str:
        return secrets.token_urlsafe(12)
