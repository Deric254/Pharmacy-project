"""add audit.view permission

Revision ID: 0016_audit_view_permission
Revises: 0015_audit_name_snapshot
Create Date: 2026-07-22

Audit logs have been written throughout this app since early in its
history, but nothing could ever read them back -- no API endpoint, no
frontend page. This is the permission gate for the endpoint/page that
closes that gap. Granted to ChemistOwner only by default: reading the
full history of who changed a price, who processed a refund, who reset
whose password is a genuinely sensitive, distinct capability from
users.manage or roles.manage, and the owner can always create a
custom role that includes it if they want to delegate that later.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_audit_view_permission"
down_revision: str | None = "0015_audit_name_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        permissions_table,
        [
            {
                "code": "audit.view",
                "description": "View the audit trail (who changed what, and when)",
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
        sa.text("SELECT id FROM permissions WHERE code = 'audit.view'")
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
            "(SELECT id FROM permissions WHERE code = 'audit.view')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'audit.view'"))
