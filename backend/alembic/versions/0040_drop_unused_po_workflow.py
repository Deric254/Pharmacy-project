"""drop the unused purchase-order workflow fields and statuses

Revision ID: 0040_drop_unused_po_workflow
Revises: 0039_money_and_stock_checks
Create Date: 2026-10-06

Stock only ever enters through quick_purchase, which creates every purchase
order already RECEIVED. The rest of the old order workflow can no longer be
reached from anywhere in the app, so it is removed:

  * statuses DRAFT, SENT, IN_TRANSIT and RECONCILED
  * columns sent_at, in_transit_at, reconciled_at and version

This is deliberately done in the same release that stops using them (the
README's usual "deprecate first" rule is waived for this change on purpose).

DATA SAFETY: an order that was never received (DRAFT, SENT or IN_TRANSIT)
cannot be turned into a received one without inventing stock that never
arrived, and deleting it would lose a record, so the migration stops with a
message BEFORE touching anything if one exists. A RECONCILED order is simply a
received order that was later ticked off, so it becomes RECEIVED. The app takes
a pre-upgrade snapshot of the database before running migrations.

ATOMIC: like 0039, the table rebuild runs inside one explicit SQLite
transaction, so it either completes entirely or leaves the database as it was.

downgrade() restores the columns and statuses. The dropped dates cannot be
recovered, so sent_at and in_transit_at are filled from received_at (which is
what quick_purchase used to write into all three).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040_drop_unused_po_workflow"
down_revision: str | None = "0039_money_and_stock_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUS = sa.Enum(
    "DRAFT", "SENT", "IN_TRANSIT", "RECEIVED", "RECONCILED", name="purchaseorderstatus"
)
_NEW_STATUS = sa.Enum("RECEIVED", name="purchaseorderstatus")


def _refuse_if_an_unreceived_order_exists() -> None:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id, status FROM purchase_orders "
                "WHERE status IN ('DRAFT', 'SENT', 'IN_TRANSIT') ORDER BY id"
            )
        )
        .all()
    )
    if rows:
        listing = ", ".join(f"#{row.id} ({row.status})" for row in rows)
        raise RuntimeError(
            "Cannot remove the unused purchase-order workflow because "
            f"{len(rows)} purchase order(s) were never received: {listing}. "
            "Nothing was changed. Review them (a pre-upgrade snapshot of the "
            "database was taken), then restart the app."
        )


def _begin_atomic() -> None:
    op.get_bind().exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _refuse_if_an_unreceived_order_exists()
    _begin_atomic()
    op.execute("UPDATE purchase_orders SET status = 'RECEIVED' WHERE status = 'RECONCILED'")
    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.drop_column("sent_at")
        batch_op.drop_column("in_transit_at")
        batch_op.drop_column("reconciled_at")
        batch_op.drop_column("version")
        batch_op.alter_column(
            "status",
            existing_type=_OLD_STATUS,
            type_=_NEW_STATUS,
            existing_nullable=False,
            existing_server_default="DRAFT",
            server_default="RECEIVED",
        )


def downgrade() -> None:
    _begin_atomic()
    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.add_column(sa.Column("sent_at", sa.DateTime, nullable=True))
        batch_op.add_column(sa.Column("in_transit_at", sa.DateTime, nullable=True))
        batch_op.add_column(sa.Column("reconciled_at", sa.DateTime, nullable=True))
        batch_op.add_column(sa.Column("version", sa.Integer, nullable=False, server_default="1"))
        batch_op.alter_column(
            "status",
            existing_type=_NEW_STATUS,
            type_=_OLD_STATUS,
            existing_nullable=False,
            existing_server_default="RECEIVED",
            server_default="DRAFT",
        )
    op.execute("UPDATE purchase_orders SET sent_at = received_at, in_transit_at = received_at")
