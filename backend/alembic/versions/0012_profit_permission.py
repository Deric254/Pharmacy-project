"""add reports.view_profit permission (ChemistOwner only)

Revision ID: 0012_profit_permission
Revises: 0011_business_theme
Create Date: 2026-07-16

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Fixes a real access-control gap: /reports/profit was gated on the
general `reports.view` permission, which Administrator also holds --
so Administrator could see profit despite the system's own stated
design ("ChemistOwner: Approves/pays orders, views profit and full
reports", and the client's original discovery requirement that only
the owner sees profit). This adds a distinct permission for it and
grants it to ChemistOwner only; Administrator keeps `reports.view` for
every other report.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_profit_permission"
down_revision: str | None = "0011_business_theme"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        permissions_table,
        [{"code": "reports.view_profit", "description": "View the profit report"}],
    )

    role_ids = {
        row.name: row.id
        for row in conn.execute(sa.text("SELECT id, name FROM roles WHERE name = 'ChemistOwner'"))
    }
    if "ChemistOwner" not in role_ids:
        # A deployment that somehow renamed/removed the ChemistOwner
        # role: the permission still needs to exist (other migrations
        # and the app assume it does), it just starts unassigned
        # rather than crashing the migration.
        return

    perm_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'reports.view_profit'")
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
            "(SELECT id FROM permissions WHERE code = 'reports.view_profit')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'reports.view_profit'"))
