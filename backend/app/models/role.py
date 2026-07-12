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
    # Values: Employee | Administrator | ChemistOwner
    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin"
    )
