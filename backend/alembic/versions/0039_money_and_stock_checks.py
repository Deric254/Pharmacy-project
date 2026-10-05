"""enforce stock and money invariants with CHECK constraints

Revision ID: 0039_money_and_stock_checks
Revises: 0038_products_category_id_index
Create Date: 2026-10-04

Until now "stock never goes negative", "a sale item can't be refunded more
than it was sold" and "no negative prices" held only because every code path
that writes those columns happens to be careful. The database itself would
accept a violating row from a future endpoint, a bad import or a hand-edited
restore file. These constraints make the database refuse it, whatever wrote it.

Values are compared as stored (integer cents for money columns).

DATA SAFETY: this migration never changes or "repairs" existing rows. It
first counts rows that would violate each rule and, if there are any, stops
with a message naming the rule and the count BEFORE touching the schema. A
real violation means the shop's numbers are already wrong and someone needs
to look at them -- silently clamping a negative stock quantity or price would
hide exactly the kind of loss these checks exist to catch. The app takes a
pre-upgrade snapshot of the database before running migrations, so a refusal
here leaves the data exactly as it was.

SQLite cannot add a constraint in place, so each table is rebuilt through
Alembic's batch mode (the same mechanism migration 0036 already used).

ATOMIC: Alembic treats SQLite DDL as non-transactional, and left alone a
failure (or power cut) part-way through the four table rebuilds leaves a
half-upgraded database -- some tables constrained, the revision still at
0038, and leftover _alembic_tmp_* tables that make every retry fail. So the
whole migration runs inside ONE explicit SQLite transaction (SQLite DDL is
transactional): it either completes entirely, version bump included, or the
database is exactly as it was. Nothing in between can be left behind.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_money_and_stock_checks"
down_revision: str | None = "0038_products_category_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> [(constraint name, rule)]. The rule is what must be TRUE for a row.
CHECKS: dict[str, list[tuple[str, str]]] = {
    "medicine_batches": [
        ("ck_medicine_batches_qty_remaining_nonneg", "qty_remaining >= 0"),
        ("ck_medicine_batches_qty_received_nonneg", "qty_received >= 0"),
        ("ck_medicine_batches_prices_nonneg", "cost_price >= 0 AND selling_price >= 0"),
    ],
    "sales": [
        (
            "ck_sales_amounts_nonneg",
            "subtotal >= 0 AND discount_amount >= 0 AND total_amount >= 0",
        ),
    ],
    "sale_items": [
        ("ck_sale_items_quantity_positive", "quantity > 0"),
        (
            "ck_sale_items_qty_refunded_in_range",
            "qty_refunded >= 0 AND qty_refunded <= quantity",
        ),
        (
            "ck_sale_items_amounts_nonneg",
            "unit_price >= 0 AND unit_cost >= 0 AND line_total >= 0",
        ),
    ],
    "payments": [
        ("ck_payments_amount_positive", "amount > 0"),
    ],
    "refunds": [
        ("ck_refunds_total_nonneg", "total_amount >= 0"),
    ],
    "refund_items": [
        ("ck_refund_items_quantity_positive", "quantity > 0"),
        ("ck_refund_items_amounts_nonneg", "unit_price >= 0 AND line_total >= 0"),
    ],
    "purchase_order_items": [
        ("ck_purchase_order_items_ordered_positive", "quantity_ordered > 0"),
        ("ck_purchase_order_items_received_nonneg", "quantity_received >= 0"),
        (
            "ck_purchase_order_items_costs_nonneg",
            "unit_cost_expected >= 0 AND unit_cost_actual >= 0",
        ),
    ],
    "stock_take_items": [
        (
            "ck_stock_take_items_quantities_nonneg",
            "expected_qty >= 0 AND physical_qty >= 0",
        ),
        ("ck_stock_take_items_cost_nonneg", "unit_cost_at_close >= 0"),
    ],
}


def _refuse_if_existing_rows_violate() -> None:
    conn = op.get_bind()
    problems: list[str] = []
    for table, checks in CHECKS.items():
        for name, rule in checks:
            # NULL satisfies a CHECK in SQL, so only rows where the rule is
            # definitely false count as violations.
            count = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table} WHERE NOT ({rule})")  # noqa: S608
            ).scalar_one()
            if count:
                problems.append(f"  - {table}: {count} row(s) break the rule '{rule}' ({name})")
    if problems:
        raise RuntimeError(
            "Cannot add the data-integrity rules from migration 0039 because "
            "existing records already break them:\n"
            + "\n".join(problems)
            + "\nNothing was changed. These records need to be reviewed and "
            "corrected before upgrading (a pre-upgrade snapshot of the database "
            "was taken). To list the exact offending rows, run: "
            "python scripts/check_data_integrity.py <path to pharmacy.db>"
        )


def _begin_atomic() -> None:
    # Taken before the first write so every table rebuild and Alembic's own
    # version bump share one transaction. IMMEDIATE takes the write lock up
    # front, so a concurrent writer can't interleave. If this cannot start,
    # the migration fails here, before anything has changed.
    op.get_bind().exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _refuse_if_existing_rows_violate()
    _begin_atomic()
    for table, checks in CHECKS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, rule in checks:
                batch_op.create_check_constraint(name, rule)


def downgrade() -> None:
    _begin_atomic()
    for table, checks in CHECKS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, _rule in checks:
                batch_op.drop_constraint(name, type_="check")
