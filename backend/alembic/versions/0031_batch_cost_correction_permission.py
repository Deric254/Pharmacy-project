"""add batches.correct_cost permission

Revision ID: 0031_batch_cost_correction_permission
Revises: 0030_batch_reprice_permission
Create Date: 2026-08-27

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Adds the permission gating batch cost (buying price) correction --
see BatchService.correct_cost_price. Deliberately a distinct
permission from `batches.reprice` (selling price) and `batches.create`
(receiving new stock): correcting a mis-entered cost is a rarer,
higher-stakes action than either of those (it's only ever allowed
before a single unit of the batch has sold, precisely so it can never
rewrite the profit already recorded on a real sale), so it should be
independently grantable/revocable rather than piggybacking on an
existing permission whose scope means something narrower. Granted to
the same roles that already hold `batches.reprice` (Administrator,
ChemistOwner) so behaviour is unchanged for existing installs -- this
migration only makes the ability separately grantable going forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_batch_cost_correction_permission"
down_revision: str | None = "0030_batch_reprice_permission"
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
        [
            {
                "code": "batches.correct_cost",
                "description": "Correct a batch's buying price before anything has sold from it",
            }
        ],
    )

    role_ids = {
        row.name: row.id
        for row in conn.execute(
            sa.text("SELECT id, name FROM roles WHERE name IN ('Administrator', 'ChemistOwner')")
        )
    }
    perm_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'batches.correct_cost'")
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
            "(SELECT id FROM permissions WHERE code = 'batches.correct_cost')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'batches.correct_cost'"))
