"""
Role management service.

The one deliberate asymmetry here: PERMISSIONS are a fixed vocabulary
defined by what `require_permission(...)` calls actually exist in the
code (seeded via migrations, never created through this API) -- a
permission that isn't checked anywhere would be a UI checkbox that
does nothing, which is worse than not offering it. ROLES are fully
dynamic: a business can rename them, redefine what they grant, add
new ones, or delete ones they created. This mirrors how real RBAC
systems separate "actions the system understands" from "named bundles
an admin assembles" (AWS IAM policies vs. roles is the same split).
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.role import Permission, Role
from app.models.user import User
from app.schemas.role import PermissionOut, RoleCreate, RoleDetailOut, RoleUpdate


class RoleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_permissions(self) -> list[PermissionOut]:
        result = await self.db.execute(select(Permission).order_by(Permission.code))
        return [PermissionOut.model_validate(p) for p in result.scalars().all()]

    async def list_roles(self) -> list[RoleDetailOut]:
        result = await self.db.execute(select(Role).order_by(Role.name))
        roles = result.scalars().all()
        return [await self._to_detail(r) for r in roles]

    async def get_role(self, role_id: int) -> RoleDetailOut:
        role = await self._get_role_or_404(role_id)
        return await self._to_detail(role)

    async def create_role(self, payload: RoleCreate, created_by: User) -> RoleDetailOut:
        existing = await self.db.execute(select(Role).where(Role.name == payload.name))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="A role with this name already exists")

        permissions = await self._resolve_permission_codes(payload.permission_codes)

        role = Role(
            name=payload.name,
            description=payload.description,
            is_system=False,
            permissions=permissions,
        )
        self.db.add(role)
        # The real INSERT -- and so the real point the UNIQUE
        # constraint gets checked -- happens right here at flush(), not
        # at the later commit() below. An earlier version of this fix
        # only wrapped commit() in the try/except further down, which
        # looked complete but left this exact line unprotected;
        # confirmed by the race test itself intermittently failing
        # against that version with this identical IntegrityError,
        # uncaught, before this fix moved the try/except up to here.
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="A role with this name already exists"
            ) from exc

        self.db.add(
            AuditLog(
                user_id=created_by.id,
                user_name_snapshot=created_by.full_name,
                action="role.created",
                entity_type="role",
                entity_id=str(role.id),
                new_value=f"name={role.name} permissions={sorted(payload.permission_codes)}",
            )
        )
        # This commit is the AuditLog's own write -- the role row
        # itself is already durably inserted above (flush alone does
        # not commit, but it's what surfaces the constraint violation;
        # the actual persistence still depends on this commit
        # succeeding, same as everywhere else in this codebase).
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="A role with this name already exists"
            ) from exc
        return await self._to_detail(role)

    async def update_role(
        self, role_id: int, payload: RoleUpdate, updated_by: User
    ) -> RoleDetailOut:
        role = await self._get_role_or_404(role_id)

        old_permissions = sorted(p.code for p in role.permissions)

        if payload.name is not None and payload.name != role.name:
            existing = await self.db.execute(
                select(Role).where(Role.name == payload.name, Role.id != role_id)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="A role with this name already exists")
            role.name = payload.name

        if payload.description is not None:
            role.description = payload.description

        if payload.permission_codes is not None:
            role.permissions = await self._resolve_permission_codes(payload.permission_codes)

        self.db.add(role)
        self.db.add(
            AuditLog(
                user_id=updated_by.id,
                user_name_snapshot=updated_by.full_name,
                action="role.updated",
                entity_type="role",
                entity_id=str(role.id),
                old_value=f"permissions={old_permissions}",
                new_value=f"permissions={sorted(p.code for p in role.permissions)}",
            )
        )
        # Same reasoning as create_role's own commit above -- the
        # duplicate-name SELECT a few lines up is a check-THEN-write,
        # not atomic, and Role.name's real database-level UNIQUE
        # constraint is what actually stops two concurrent renames to
        # the same name from both landing, not that earlier SELECT.
        # Without catching the IntegrityError here, the race's loser
        # got an unhandled 500 instead of the same clean 409 a
        # sequential duplicate-name attempt gets -- confirmed directly
        # with a real concurrent-rename test, not assumed safe just
        # because the SELECT check exists.
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="A role with this name already exists"
            ) from exc
        await self.db.refresh(role, attribute_names=["permissions"])
        return await self._to_detail(role)

    async def delete_role(self, role_id: int, deleted_by: User) -> None:
        role = await self._get_role_or_404(role_id)

        if role.is_system:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{role.name}' is a built-in role and can't be deleted. "
                    "Its permissions can still be edited freely."
                ),
            )

        user_count = await self.db.scalar(
            select(func.count()).select_from(User).where(User.role_id == role_id)
        )
        if user_count and user_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{user_count} user(s) still have this role. "
                    "Reassign them to a different role before deleting it."
                ),
            )

        self.db.add(
            AuditLog(
                user_id=deleted_by.id,
                user_name_snapshot=deleted_by.full_name,
                action="role.deleted",
                entity_type="role",
                entity_id=str(role.id),
                old_value=f"name={role.name}",
            )
        )
        await self.db.delete(role)
        await self.db.commit()

    async def _resolve_permission_codes(self, codes: list[str]) -> list[Permission]:
        if not codes:
            return []
        result = await self.db.execute(select(Permission).where(Permission.code.in_(codes)))
        found = result.scalars().all()
        found_codes = {p.code for p in found}
        unknown = set(codes) - found_codes
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown permission code(s): {sorted(unknown)}. "
                    "See GET /permissions for the full list of what the system actually enforces."
                ),
            )
        return list(found)

    async def _get_role_or_404(self, role_id: int) -> Role:
        result = await self.db.execute(select(Role).where(Role.id == role_id))
        role = result.scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return role

    async def _to_detail(self, role: Role) -> RoleDetailOut:
        user_count = await self.db.scalar(
            select(func.count()).select_from(User).where(User.role_id == role.id)
        )
        return RoleDetailOut(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=sorted(p.code for p in role.permissions),
            user_count=user_count or 0,
        )
