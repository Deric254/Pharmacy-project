from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import require_permission
from app.models.user import User
from app.schemas.role import PermissionOut, RoleCreate, RoleDetailOut, RoleUpdate
from app.services.role_service import RoleService

router = APIRouter(tags=["roles"], dependencies=[Depends(require_permission("roles.manage"))])


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(db: Annotated[AsyncSession, Depends(get_db)]) -> list[PermissionOut]:
    return await RoleService(db).list_permissions()


@router.get("/roles", response_model=list[RoleDetailOut])
async def list_roles(db: Annotated[AsyncSession, Depends(get_db)]) -> list[RoleDetailOut]:
    return await RoleService(db).list_roles()


@router.get("/roles/{role_id}", response_model=RoleDetailOut)
async def get_role(role_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> RoleDetailOut:
    return await RoleService(db).get_role(role_id)


@router.post("/roles", response_model=RoleDetailOut, status_code=201)
async def create_role(
    payload: RoleCreate,
    current_user: Annotated[User, Depends(require_permission("roles.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleDetailOut:
    return await RoleService(db).create_role(payload, created_by=current_user)


@router.patch("/roles/{role_id}", response_model=RoleDetailOut)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    current_user: Annotated[User, Depends(require_permission("roles.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleDetailOut:
    return await RoleService(db).update_role(role_id, payload, updated_by=current_user)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    current_user: Annotated[User, Depends(require_permission("roles.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await RoleService(db).delete_role(role_id, deleted_by=current_user)
