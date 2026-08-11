from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id"), primary_key=True),
)


class Permission(Base):
    """
    A single grantable action, e.g. 'sales.create', 'inventory.adjust',
    'config.edit'. Seeded once via migration; the matrix of who has what
    is entirely in `role_permissions`, editable from the Admin UI.
    """

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255))


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    # True for the 3 roles seeded at install time (Employee, Administrator,
    # ChemistOwner). Protects them from deletion -- deleting the role that
    # holds users.manage/roles.manage could permanently lock everyone out
    # of access management -- but NOT from having their name, description,
    # or permission set edited. A business can rename "Administrator" to
    # "Pharmacist-in-Charge" or strip its inventory.adjust grant; they
    # just can't delete the role entirely while it's a system role.
    is_system: Mapped[bool] = mapped_column(default=False)

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin"
    )
