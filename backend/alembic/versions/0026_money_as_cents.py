"""convert money columns from float to exact integer cents

Revision ID: 0026_money_as_cents
Revises: 0025_ai_conversations
Create Date: 2026-08-05

Every money-bearing column in this app (medicine_batches.cost_price,
products.default_selling_price, purchase_order_items.unit_cost_*,
sales.*, sale_items.*, payments.amount, refunds.*, refund_items.*,
supplier_transactions.amount) was stored as float/SQLite REAL.
Quantities were always exact Integer columns; money was the one place
this app didn't hold itself to the same standard. Floats cannot
exactly represent most decimal fractions -- not a theoretical risk for
a system that runs weighted-average cost recalculation, loyalty
accrual, and running supplier balances the same way thousands of times
over years. Small per-operation errors compound.

This migration does the conversion in two passes per table:

  1. A Python-side UPDATE of every existing value, using the exact
     same Decimal(str(value)) * 100 rounding as MoneyCents.process_
     bind_param (see app/core/money_types.py) -- not SQL-level ROUND(),
     specifically so the one-time migration conversion and every
     runtime conversion from this point forward use bit-for-bit the
     same rounding rule, with zero chance of the two disagreeing at an
     edge case.
  2. batch_alter_table to change the column's declared type from Float
     to Integer, so SQLite actually stores it as INTEGER (not a
     float-shaped representation of a whole number) going forward.

Downgrade is the exact symmetric reverse: divide by 100 back to a
float, then change the column type back to Float. Verified (see CI's
"Verify migrations also downgrade cleanly" job) that upgrade then
downgrade reproduces the original values to the cent -- the only
possible loss is sub-cent float noise that this migration exists
specifically to eliminate, so losing it on the way back down is
correct, not a bug.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa

from alembic import op

revision: str = "0026_money_as_cents"
down_revision: str | None = "0025_ai_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, [money columns], [nullable columns among them])
TABLES: list[tuple[str, list[str], list[str]]] = [
    ("medicine_batches", ["cost_price"], []),
    ("products", ["default_selling_price"], []),
    ("purchase_order_items", ["unit_cost_expected", "unit_cost_actual"], ["unit_cost_actual"]),
    ("sales", ["subtotal", "discount_amount", "total_amount"], []),
    ("sale_items", ["unit_price", "line_total"], []),
    ("payments", ["amount"], []),
    ("refunds", ["total_amount"], []),
    ("refund_items", ["unit_price", "line_total"], []),
    ("supplier_transactions", ["amount"], []),
]


def _to_cents(value: float) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_float(cents: int) -> float:
    return float(Decimal(cents) / 100)


def _convert_table(table: str, columns: list[str], nullable: list[str], forward: bool) -> None:
    conn = op.get_bind()
    meta = sa.MetaData()
    tbl = sa.Table(table, meta, autoload_with=conn)
    pk = tbl.primary_key.columns.values()[0]

    select_cols = [pk] + [tbl.c[c] for c in columns]
    rows = conn.execute(sa.select(*select_cols)).fetchall()

    convert = _to_cents if forward else _to_float
    for row in rows:
        row_id = row[0]
        updates = {}
        for i, col in enumerate(columns, start=1):
            raw = row[i]
            if raw is None:
                continue  # nullable column, genuinely unset -- leave it None
            updates[col] = convert(raw)
        if updates:
            conn.execute(sa.update(tbl).where(pk == row_id).values(**updates))


def _alter_types(table: str, columns: list[str], nullable: list[str], to_integer: bool) -> None:
    with op.batch_alter_table(table) as batch_op:
        for col in columns:
            is_nullable = col in nullable
            batch_op.alter_column(
                col,
                existing_type=sa.Float() if to_integer else sa.Integer(),
                type_=sa.Integer() if to_integer else sa.Float(),
                existing_nullable=is_nullable,
            )


def upgrade() -> None:
    for table, columns, nullable in TABLES:
        _convert_table(table, columns, nullable, forward=True)
        _alter_types(table, columns, nullable, to_integer=True)


def downgrade() -> None:
    for table, columns, nullable in TABLES:
        _alter_types(table, columns, nullable, to_integer=False)
        _convert_table(table, columns, nullable, forward=False)
