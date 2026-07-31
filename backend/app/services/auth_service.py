"""
Auth service layer. Routes stay thin (parse request -> call service ->
return response); all logic and DB transaction boundaries live here.
This separation is what makes the module testable without spinning up
FastAPI at all — services are plain async functions/classes.
"""

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.core.security import (
    JWTError,
    create_token,
    decode_token,
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
                    user_name_snapshot=user.full_name if user else None,
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
                user_name_snapshot=user.full_name,
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

    async def rotate_refresh_token(
        self, refresh_token: str, device_label: str | None, ip_address: str | None
    ) -> dict[str, str]:
        """
        Redeem a refresh token for a new access+refresh pair, rotating
        the session. A refresh token can only ever be used once:

        - Unknown jti (never issued, or the DB was reset) -> reject.
        - Session already revoked -> this exact token was already
          redeemed once before. Presenting it again means either the
          legitimate client double-submitted (harmless) or an attacker
          replayed a stolen token (not harmless). We can't tell which,
          so we treat it as compromise: revoke every other active
          session for this user too, forcing a real re-login
          everywhere. This is the "reuse detection" the rotation model
          exists for -- rotation alone is meaningless without it.
        """
        credentials_error = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )
        try:
            payload = decode_token(refresh_token)
            if payload.get("type") != "refresh":
                raise credentials_error
            jti = payload.get("jti")
            user_id = payload.get("sub")
            if jti is None or user_id is None:
                raise credentials_error
        except JWTError as exc:
            raise credentials_error from exc

        result = await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_jti == jti)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise credentials_error

        if session.revoked_at is not None:
            await self._revoke_all_sessions_for_user(int(user_id), reason="refresh_token_reuse")
            raise credentials_error

        user_result = await self.db.execute(
            select(User).where(User.id == int(user_id), User.is_active.is_(True))
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise credentials_error

        session.revoked_at = func.now()
        self.db.add(session)
        return await self.issue_tokens(user, device_label=device_label, ip_address=ip_address)

    async def _revoke_all_sessions_for_user(self, user_id: int, reason: str) -> None:
        result = await self.db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
        sessions = result.scalars().all()
        for session in sessions:
            session.revoked_at = func.now()
            self.db.add(session)
        user = await self.db.get(User, user_id)
        self.db.add(
            AuditLog(
                user_id=user_id,
                user_name_snapshot=user.full_name if user else None,
                action="auth.refresh_token_reuse_detected",
                entity_type="user",
                entity_id=str(user_id),
                new_value=reason,
            )
        )
        await self.db.commit()

    async def revoke_session_by_refresh_token(self, refresh_token: str) -> None:
        """Best-effort logout: revoke the session if the token is still
        decodable, but never raise -- an expired/garbled cookie on
        logout should still result in a 204, not a 401."""
        try:
            payload = decode_token(refresh_token)
            jti = payload.get("jti")
        except JWTError:
            return
        if jti is None:
            return
        result = await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_jti == jti)
        )
        session = result.scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = func.now()
            self.db.add(session)
            await self.db.commit()

    GENERIC_SECURITY_QUESTION = "Security question"

    async def get_security_question(self, username: str) -> str:
        """
        Never reveals whether a username exists or has a question set
        -- returns the same generic placeholder either way, exactly
        the same principle already applied in
        reset_password_via_security_question's error handling. A real
        question is only ever returned for an account that genuinely
        has one.
        """
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None or user.security_question is None:
            return self.GENERIC_SECURITY_QUESTION
        return user.security_question

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
        user.must_change_password = False
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="password.self_reset",
                entity_type="user",
                entity_id=str(user.id),
            )
        )
        await self.db.commit()

    async def admin_reset_password(self, admin: User, target_user_id: int) -> str:
        """
        Returns the generated temp password, for the admin to relay to
        the locked-out person out of band. The admin never chooses or
        learns their REAL password -- must_change_password forces a
        genuine change before the temp one is usable for anything else.
        """
        result = await self.db.execute(select(User).where(User.id == target_user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        if not await self._can_reset(admin, user):
            raise HTTPException(
                status_code=403,
                detail=(
                    "You can only reset passwords for accounts with fewer permissions "
                    "than your own."
                ),
            )

        temp_password = self.generate_temp_password()
        user.hashed_password = hash_password(temp_password)
        user.must_change_password = True
        self.db.add(
            AuditLog(
                user_id=admin.id,
                user_name_snapshot=admin.full_name,
                action="password.admin_reset",
                entity_type="user",
                entity_id=str(user.id),
                new_value=f"reset_by_admin_id={admin.id}",
            )
        )
        await self.db.commit()
        return temp_password

    async def _can_reset(self, actor: User, target: User) -> bool:
        """
        Owner-tier (holds roles.manage) can reset anyone, including
        other owner-tier accounts -- restricting that would create
        exactly the lockout scenario this whole feature exists to
        prevent, if a business ever has more than one owner account.
        Admin-tier (holds users.manage but not roles.manage) can reset
        anyone who ISN'T also admin-tier or above -- an Administrator
        resetting another Administrator or the Owner is exactly the
        gap this closes.

        Deliberately based on permissions actually held, not role name
        or role_id -- roles are admin-configurable (see role_service.py),
        so a hierarchy keyed to the literal strings "Administrator" or
        "Employee" would silently stop applying to any custom role.
        """
        actor_codes = {p.code for p in actor.role.permissions}
        target_codes = {p.code for p in target.role.permissions}

        if "roles.manage" in actor_codes:
            return True
        if "users.manage" in actor_codes:
            return "users.manage" not in target_codes
        return False

    async def change_own_password(
        self, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

        user.hashed_password = hash_password(new_password)
        user.must_change_password = False
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="password.changed",
                entity_type="user",
                entity_id=str(user.id),
            )
        )
        await self.db.commit()

    async def accept_terms(self, user: User) -> None:
        user.terms_accepted_at = datetime.now(UTC)
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="terms.accepted",
                entity_type="user",
                entity_id=str(user.id),
            )
        )
        await self.db.commit()

    @staticmethod
    def generate_temp_password() -> str:
        return secrets.token_urlsafe(12)
