"""supplier payment safety: idempotency key, payment reversals, reversal permission

Revision ID: 0041_supplier_payment_safety
Revises: 0040_drop_unused_po_workflow
Create Date: 2026-10-12

COMPATIBILITY NOTE: purely additive (two nullable columns, two unique
indexes, one permission). Nothing is dropped or rewritten, so an older
build can still read the database. See README.md.

supplier_transactions.idempotency_key
    A client-generated token sent with each payment. A double click, a
    retried request or a flaky connection that repeats the same payment
    is recognised by the unique index and answered with the original
    result instead of paying twice. NULL for every existing row and for
    purchase charges (SQLite allows many NULLs in a unique index).

supplier_transactions.reverses_transaction_id
    Set on a reversal row and pointing at the payment it cancels. The
    ledger stays append-only: a wrong payment is never edited or
    deleted, an opposite entry is added. The unique index guarantees a
    payment can be reversed at most once, even under concurrent requests.

purchasing.reverse_payment
    Its own permission, granted to ChemistOwner only. Reversing a
    payment changes what the business believes it owes, so it is
    deliberately separate from `purchasing.approve_po` (which lets
    Administrators record payments).

The statements live in app.core.supplier_payment_schema and are idempotent,
because the desktop launcher also applies them when migration 0039 refuses a
shop's existing data and the app has to keep running on the older schema
(the app code needs these columns). Alembic reaching this revision later is
then a harmless no-op.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core import supplier_payment_schema

revision: str = "0041_supplier_payment_safety"
down_revision: str | None = "0040_drop_unused_po_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    def run(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        result = conn.exec_driver_sql(sql, params)
        return list(result.fetchall()) if result.returns_rows else []

    supplier_payment_schema.apply(run)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = "
            "(SELECT id FROM permissions WHERE code = :code)"
        ),
        {"code": supplier_payment_schema.PERMISSION_CODE},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"),
        {"code": supplier_payment_schema.PERMISSION_CODE},
    )
    op.drop_index(supplier_payment_schema.REVERSES_INDEX, "supplier_transactions")
    op.drop_index(supplier_payment_schema.IDEMPOTENCY_INDEX, "supplier_transactions")
    with op.batch_alter_table("supplier_transactions") as batch:
        batch.drop_column("reverses_transaction_id")
        batch.drop_column("idempotency_key")
