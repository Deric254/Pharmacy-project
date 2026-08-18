from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import get_current_user, require_permission
from app.core.redis_client import redis_client
from app.core.security import decode_token
from app.models.user import User
from app.schemas.auth import (
    AdminResetPasswordRequest,
    AdminResetPasswordResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    SecurityQuestionOut,
    TokenResponse,
    UserOut,
)
from app.services.auth_service import MAX_LOGIN_ATTEMPTS, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.effective_cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user = await service.authenticate(payload.username, payload.password, client_ip)
    tokens = await service.issue_tokens(
        user, device_label=request.headers.get("user-agent"), ip_address=client_ip
    )
    _set_refresh_cookie(response, tokens["refresh_token"])
    return TokenResponse(
        access_token=tokens["access_token"],
        must_change_password=user.must_change_password,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token provided"
        )
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    tokens = await service.rotate_refresh_token(
        refresh_token, device_label=request.headers.get("user-agent"), ip_address=client_ip
    )
    _set_refresh_cookie(response, tokens["refresh_token"])
    try:
        payload = decode_token(refresh_token)
        user_id = int(payload.get("sub", 0))
        user_row = await db.get(User, user_id)
        must_change = user_row.must_change_password if user_row else False
    except Exception:
        must_change = False
    return TokenResponse(access_token=tokens["access_token"], must_change_password=must_change)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    if refresh_token is not None:
        await AuthService(db).revoke_session_by_refresh_token(refresh_token)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.get("/security-question", response_model=SecurityQuestionOut)
async def get_security_question(
    username: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> SecurityQuestionOut:
    question = await AuthService(db).get_security_question(username)
    return SecurityQuestionOut(question=question)


@router.post("/forgot-password", status_code=204)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    client_ip = request.client.host if request.client else None
    rate_limit_key = f"forgot_password_attempts:{client_ip or 'unknown'}:{payload.username}"
    attempt_count = await redis_client.get(rate_limit_key)
    if attempt_count is not None and int(attempt_count) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many password reset attempts. Try again in a few minutes.",
        )

    service = AuthService(db)
    try:
        await service.reset_password_via_security_question(
            payload.username, payload.security_answer, payload.new_password
        )
    except HTTPException:
        await AuthService._record_failed_attempt(rate_limit_key)
        raise


@router.post(
    "/admin-reset-password",
    response_model=AdminResetPasswordResponse,
    dependencies=[Depends(require_permission("users.manage"))],
)
async def admin_reset_password(
    payload: AdminResetPasswordRequest,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminResetPasswordResponse:
    service = AuthService(db)
    temp_password = await service.admin_reset_password(admin, payload.user_id)
    return AdminResetPasswordResponse(temp_password=temp_password)


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await AuthService(db).change_own_password(
        current_user, payload.current_password, payload.new_password
    )


@router.get("/me", response_model=UserOut)
async def read_current_user(current_user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut(
        id=current_user.id,
        full_name=current_user.full_name,
        username=current_user.username,
        role_name=current_user.role.name,
        permissions=sorted(p.code for p in current_user.role.permissions),
        is_active=current_user.is_active,
        must_change_password=current_user.must_change_password,
        terms_accepted=current_user.terms_accepted,
    )


@router.post("/accept-terms", status_code=204)
async def accept_terms(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await AuthService(db).accept_terms(current_user)
