"""
The additive schema behind supplier payment safety (migration 0041), written
so it can be applied more than once and from two places:

  * migration 0041 itself, and
  * the desktop launcher, when migration 0039 refuses a shop's existing data
    and the app must keep running on the older schema (see desktop_main).

The second case is why this lives here and not inline in the migration: the
app code needs these columns, so a shop kept on the pre-0039 schema would
otherwise crash on the first supplier request. Every statement checks first,
so running it again -- including when Alembic reaches 0041 later -- is a no-op.

`run(sql, params)` is any callable that executes one statement (values go in
`params`, never into the SQL text) and returns its rows, so the same code works
on a SQLAlchemy connection and on a plain sqlite3 one.
"""

from typing import Any, Protocol

PERMISSION_CODE = "purchasing.reverse_payment"
PERMISSION_DESCRIPTION = "Reverse a recorded supplier payment (e.g. a wrong amount)"
OWNER_ROLE = "ChemistOwner"
IDEMPOTENCY_INDEX = "ix_supplier_transactions_idempotency_key"
REVERSES_INDEX = "ix_supplier_transactions_reverses_transaction_id"


class Runner(Protocol):
    def __call__(self, sql: str, params: tuple[Any, ...] = (), /) -> Any: ...


def _columns(run: Runner) -> set[str]:
    return {row[1] for row in run("PRAGMA table_info(supplier_transactions)")}


def apply(run: Runner) -> None:
    existing = _columns(run)
    # Plain ALTER TABLE ADD COLUMN: SQLite supports it for a nullable column,
    # including one that REFERENCES another table, and it leaves every
    # existing row and constraint untouched (no table rebuild).
    if "idempotency_key" not in existing:
        run("ALTER TABLE supplier_transactions ADD COLUMN idempotency_key VARCHAR(64)")
    if "reverses_transaction_id" not in existing:
        run(
            "ALTER TABLE supplier_transactions ADD COLUMN reverses_transaction_id "
            "INTEGER REFERENCES supplier_transactions (id)"
        )
    run(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {IDEMPOTENCY_INDEX} "
        "ON supplier_transactions (idempotency_key)"
    )
    run(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {REVERSES_INDEX} "
        "ON supplier_transactions (reverses_transaction_id)"
    )
    run(
        "INSERT INTO permissions (code, description) SELECT ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = ?)",
        (PERMISSION_CODE, PERMISSION_DESCRIPTION, PERMISSION_CODE),
    )
    run(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT r.id, p.id FROM roles r, permissions p "
        "WHERE r.name = ? AND p.code = ? "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp "
        "WHERE rp.role_id = r.id AND rp.permission_id = p.id)",
        (OWNER_ROLE, PERMISSION_CODE),
    )
