from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import get_current_user, require_permission
from app.models.user import User
from app.schemas.auth import (
    AdminResetPasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user = await service.authenticate(payload.username, payload.password, client_ip)
    tokens = await service.issue_tokens(
        user, device_label=request.headers.get("user-agent"), ip_address=client_ip
    )
    return TokenResponse(access_token=tokens["access_token"])


@router.post("/forgot-password", status_code=204)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = AuthService(db)
    await service.reset_password_via_security_question(
        payload.username, payload.security_answer, payload.new_password
    )


@router.post(
    "/admin-reset-password",
    status_code=204,
    dependencies=[Depends(require_permission("users.manage"))],
)
async def admin_reset_password(
    payload: AdminResetPasswordRequest,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = AuthService(db)
    await service.admin_reset_password(admin, payload.user_id, payload.new_password)


@router.get("/me", response_model=UserOut)
async def read_current_user(current_user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut(
        id=current_user.id,
        full_name=current_user.full_name,
        username=current_user.username,
        role_name=current_user.role.name,
        is_active=current_user.is_active,
    )
