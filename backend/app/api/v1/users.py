from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import get_current_user, require_permission
from app.models.user import User
from app.schemas.user import RoleOut, UserCreate, UserListItemOut
from app.services.user_service import UserService

router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_permission("users.manage"))]
)


@router.get("", response_model=list[UserListItemOut])
async def list_users(db: Annotated[AsyncSession, Depends(get_db)]) -> list[UserListItemOut]:
    return await UserService(db).list_users()


@router.post("", response_model=UserListItemOut, status_code=201)
async def create_user(
    payload: UserCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserListItemOut:
    return await UserService(db).create_user(payload, created_by=current_user)


@router.delete("/{user_id}", status_code=204)
async def deactivate_user(
    user_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await UserService(db).deactivate_user(user_id, deactivated_by=current_user)


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: Annotated[AsyncSession, Depends(get_db)]) -> list[RoleOut]:
    return await UserService(db).list_roles()
