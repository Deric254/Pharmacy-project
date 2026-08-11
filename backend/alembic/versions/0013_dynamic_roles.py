"""add role.is_system and roles.manage permission

Revision ID: 0013_dynamic_roles
Revises: 0012_profit_permission
Create Date: 2026-07-17

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

This is the migration that makes roles genuinely admin-configurable
instead of a fixed set baked in at install time. Two things:

1. `roles.is_system` (backfilled true for Employee/Administrator/
   ChemistOwner) -- protects those three from deletion, since deleting
   whichever role holds users.manage/roles.manage could permanently
   lock everyone out of access management. Their name, description,
   and permission grants remain fully editable; only deletion is
   blocked, and only for these three.

2. A new `roles.manage` permission, distinct from `users.manage`.
   Creating a cashier account (users.manage) and redefining what
   "Administrator" is allowed to do system-wide (roles.manage) are not
   the same trust level -- bundling them would mean anyone who can
   onboard staff could also silently grant themselves more power.
   Granted to ChemistOwner only by default; an owner can create a new
   role that includes it if they want to delegate that.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_dynamic_roles"
down_revision: str | None = "0012_profit_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_ROLE_NAMES = ("Employee", "Administrator", "ChemistOwner")


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch_op:
        batch_op.add_column(
            sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false())
        )

    conn = op.get_bind()
    placeholders = ", ".join(f"'{name}'" for name in SYSTEM_ROLE_NAMES)
    conn.execute(sa.text(f"UPDATE roles SET is_system = true WHERE name IN ({placeholders})"))

    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        permissions_table,
        [
            {
                "code": "roles.manage",
                "description": "Create, edit, and delete roles and their permission grants",
            }
        ],
    )

    role_ids = {
        row.name: row.id
        for row in conn.execute(sa.text("SELECT id, name FROM roles WHERE name = 'ChemistOwner'"))
    }
    if "ChemistOwner" not in role_ids:
        return

    perm_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'roles.manage'")
    ).scalar_one()
    role_permissions_table = sa.table(
        "role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer)
    )
    op.bulk_insert(
        role_permissions_table,
        [{"role_id": role_ids["ChemistOwner"], "permission_id": perm_id}],
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = "
            "(SELECT id FROM permissions WHERE code = 'roles.manage')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'roles.manage'"))
    with op.batch_alter_table("roles") as batch_op:
        batch_op.drop_column("is_system")
