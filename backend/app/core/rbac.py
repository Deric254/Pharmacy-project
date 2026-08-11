"""
RBAC enforcement.

`get_current_user` decodes the JWT and loads the user (with role +
permissions eager-loaded). `require_permission(...)` is a dependency
factory used on every protected route:

    @router.post("/sales", dependencies=[Depends(require_permission("sales.create"))])

No route handler ever contains `if user.role == "admin"` — permissions
are data (role_permissions table), checked in one place, so the owner
can regrant/revoke access from the UI without a code change.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import JWTError, decode_token
from app.models.role import Role
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_error
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_error
    except JWTError as exc:
        raise credentials_error from exc

    result = await db.execute(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == int(user_id), User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


def require_permission(permission_code: str) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _check(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        user_permission_codes = {p.code for p in current_user.role.permissions}
        if permission_code not in user_permission_codes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission_code}",
            )
        return current_user

    return _check
