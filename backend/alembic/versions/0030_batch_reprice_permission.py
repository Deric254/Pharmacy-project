"""add batches.reprice permission (separate from batches.create)

Revision ID: 0030_batch_reprice_permission
Revises: 0029_batch_selling_price
Create Date: 2026-08-26

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Closes a real access-control gap: PATCH /products/{id}/batches/{id}
(changing a batch's selling price) was gated on `batches.create`, the
same permission that lets someone receive new stock -- so there was
no way to grant "can reprice near-expiry stock" without also granting
"can create batches out of thin air". This adds a distinct permission
for repricing and grants it to the same roles that already hold
`batches.create` (Administrator, ChemistOwner), so behaviour is
unchanged for existing installs -- this migration only makes the two
abilities separately grantable going forward, it doesn't revoke
anything anyone currently has.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_batch_reprice_permission"
down_revision: str | None = "0029_batch_selling_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES_TO_GRANT = ["Administrator", "ChemistOwner"]


def upgrade() -> None:
    conn = op.get_bind()

    permissions_table = sa.table(
        "permissions", sa.column("code", sa.String), sa.column("description", sa.String)
    )
    op.bulk_insert(
        permissions_table,
        [{"code": "batches.reprice", "description": "Change a batch's selling price"}],
    )

    role_ids = {
        row.name: row.id
        for row in conn.execute(
            sa.text("SELECT id, name FROM roles WHERE name IN ('Administrator', 'ChemistOwner')")
        )
    }
    perm_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'batches.reprice'")
    ).scalar_one()

    role_permissions_table = sa.table(
        "role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer)
    )
    rows = [
        {"role_id": role_ids[role_name], "permission_id": perm_id}
        for role_name in _ROLES_TO_GRANT
        if role_name in role_ids
    ]
    if rows:
        op.bulk_insert(role_permissions_table, rows)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = "
            "(SELECT id FROM permissions WHERE code = 'batches.reprice')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'batches.reprice'"))
