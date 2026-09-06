"""add batches.correct_expiry permission

Revision ID: 0035_batch_expiry_correction_permission
Revises: 0034_sale_item_qty_refunded
Create Date: 2026-09-06

COMPATIBILITY NOTE: never drop a column/table in the same migration
that stops using it. Deprecate first, remove later. See README.md.

Adds the permission gating expiry-date correction -- see
BatchService.correct_expiry_date. Deliberately its own permission,
separate from `batches.correct_cost` and the general `inventory.adjust`
used for ordinary quantity write-offs: this one is categorically
different in what it can be misused for. A cost correction can only
ever affect valuation and future margin; a quantity write-off can only
ever remove stock. Backdating or extending a batch's expiry date,
though, could make an already-expired batch look valid again --
letting it back into FEFO sale selection, which is not a bookkeeping
error, it's a route to selling expired medicine. That risk profile
earns its own independently grantable/revocable permission rather than
riding on either existing one. Granted to the same roles as
`batches.correct_cost` (Administrator, ChemistOwner) so behaviour is
unchanged for existing installs -- this migration only makes the
ability separately grantable going forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_batch_expiry_correction_permission"
down_revision: str | None = "0034_sale_item_qty_refunded"
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
                "code": "batches.correct_expiry",
                "description": "Correct a batch's expiry date (e.g. a data-entry error)",
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
        sa.text("SELECT id FROM permissions WHERE code = 'batches.correct_expiry'")
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
            "(SELECT id FROM permissions WHERE code = 'batches.correct_expiry')"
        )
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'batches.correct_expiry'"))
