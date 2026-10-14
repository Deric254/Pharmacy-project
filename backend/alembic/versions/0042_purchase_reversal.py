"""purchase reversal: reversed status columns and the reversal permission

Revision ID: 0042_purchase_reversal
Revises: 0041_supplier_payment_safety
Create Date: 2026-10-12

COMPATIBILITY NOTE: purely additive (two nullable columns, one permission).
Nothing is dropped or rewritten. See README.md.

purchase_orders.reversed_at / reversal_reason
    Set together with status = 'REVERSED' (a value that fits the existing
    VARCHAR(8) column, so the table is not rebuilt) when a purchase is
    reversed. NULL on every existing row.

purchasing.reverse_purchase
    Its own permission, granted to ChemistOwner only. Reversing a purchase
    removes stock and cancels a supplier debt, so it is deliberately
    separate from `purchasing.create_po`.

The statements live in app.core.purchase_reversal_schema and are idempotent,
because the desktop launcher also applies them when migration 0039 refuses a
shop's existing data and the app has to keep running on the older schema.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core import purchase_reversal_schema

revision: str = "0042_purchase_reversal"
down_revision: str | None = "0041_supplier_payment_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    def run(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        result = conn.exec_driver_sql(sql, params)
        return list(result.fetchall()) if result.returns_rows else []

    purchase_reversal_schema.apply(run)


def downgrade() -> None:
    conn = op.get_bind()
    reversed_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM purchase_orders WHERE status = 'REVERSED'")
    ).scalar_one()
    if reversed_count:
        # An older build has no REVERSED status and would crash reading these
        # rows, and dropping the columns would erase who/why. Refuse instead.
        raise RuntimeError(
            f"Cannot downgrade: {reversed_count} reversed purchase(s) exist and the older "
            "version does not understand them."
        )
    conn.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = "
            "(SELECT id FROM permissions WHERE code = :code)"
        ),
        {"code": purchase_reversal_schema.PERMISSION_CODE},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"),
        {"code": purchase_reversal_schema.PERMISSION_CODE},
    )
    with op.batch_alter_table("purchase_orders") as batch:
        batch.drop_column("reversal_reason")
        batch.drop_column("reversed_at")
