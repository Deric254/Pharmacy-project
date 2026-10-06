"""
The data-integrity rules the database enforces with CHECK constraints
(migration 0039), as one readable list.

This is the single reference for three things that must never drift apart,
and tests/test_check_constraints.py asserts they agree:
  * the CheckConstraints declared on the models,
  * the (frozen) copy of the rules inside migration 0039,
  * the read-only checker, scripts/check_data_integrity.py, which uses
    find_violations() below to scan a database for rows that break a rule.

Values are compared as stored (integer cents for money columns). SQL NULL
satisfies a CHECK, so optional columns (e.g. quantity_received) are only
judged once they hold a value.
"""

import sqlite3

SAMPLE_ROWS = 10


class DataIntegrityViolation(RuntimeError):
    """
    Existing rows already break a rule that a migration is about to enforce.
    Raised BEFORE the migration changes anything, so the database is exactly
    as it was. The desktop launcher recognises this one type (and nothing
    else) so it can keep the shop running instead of refusing to start.
    """


# table -> [(constraint name, rule that must be TRUE for every row)]
INVARIANTS: dict[str, list[tuple[str, str]]] = {
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


def find_violations(connection: sqlite3.Connection) -> list[tuple[str, str, str, int]]:
    """
    Returns (table, constraint name, rule, number of offending rows) for every
    rule that at least one existing row breaks. Read-only: only SELECTs.
    """
    found: list[tuple[str, str, str, int]] = []
    for table, rules in INVARIANTS.items():
        for name, rule in rules:
            # table and rule come only from the constant INVARIANTS above, never
            # from user input, so building the statement with an f-string is safe.
            (count,) = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE NOT ({rule})"  # noqa: S608  # nosec B608
            ).fetchone()
            if count:
                found.append((table, name, rule, int(count)))
    return found


def describe_violations(
    connection: sqlite3.Connection, violations: list[tuple[str, str, str, int]]
) -> str:
    """Human-readable lines naming each broken rule and a sample of offending rowids."""
    lines: list[str] = []
    for table, name, rule, count in violations:
        lines.append(f"{table}: {count} row(s) break '{rule}' ({name})")
        rows = connection.execute(
            f"SELECT rowid FROM {table} WHERE NOT ({rule}) LIMIT {SAMPLE_ROWS}"  # noqa: S608  # nosec B608
        ).fetchall()
        shown = ", ".join(str(row[0]) for row in rows)
        more = "" if count <= SAMPLE_ROWS else f" (first {SAMPLE_ROWS} of {count})"
        lines.append(f"    rowid(s): {shown}{more}")
    return "\n".join(lines)
