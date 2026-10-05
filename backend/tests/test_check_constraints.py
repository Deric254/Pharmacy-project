"""
Migration 0039: the database itself refuses impossible stock and money.

Covers four things:
  1. A database built by running every migration rejects each violation
     (raw SQL, so it proves the database -- not application code -- does it).
  2. The models declare the same rules, so every create_all-built test
     database enforces them too.
  3. The migration REFUSES to run over existing violating data, and leaves
     that data and the recorded revision untouched.
  4. Valid existing data (including indexes) survives upgrade, and downgrade
     removes the rules again.
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal, Base
from app.core.integrity_rules import INVARIANTS
from app.models import medicine_batch, purchase_order, refund, sale, stock_take  # noqa: F401
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product

BACKEND_DIR = Path(__file__).resolve().parent.parent

BATCH = {
    "product_id": 1,
    "batch_number": "B1",
    "expiry_date": "2097-01-01",
    "qty_received": 10,
    "qty_remaining": 10,
    "cost_price": 400,
    "selling_price": 1000,
}
SALE = {"id": 1, "cashier_user_id": 1, "subtotal": 1000, "discount_amount": 0, "total_amount": 1000}
SALE_ITEM = {
    "id": 1,
    "sale_id": 1,
    "product_id": 1,
    "batch_id": 1,
    "quantity": 2,
    "unit_price": 500,
    "unit_cost": 200,
    "line_total": 1000,
    "qty_refunded": 0,
}
PAYMENT = {"sale_id": 1, "method": "CASH", "amount": 1000}
REFUND = {
    "id": 1,
    "sale_id": 1,
    "processed_by_user_id": 1,
    "reason": "CUSTOMER_RETURN",
    "method": "CASH",
    "total_amount": 500,
}
REFUND_ITEM = {
    "id": 1,
    "refund_id": 1,
    "sale_item_id": 1,
    "product_id": 1,
    "batch_id": 1,
    "quantity": 1,
    "unit_price": 500,
    "line_total": 500,
}
PO_ITEM = {
    "id": 1,
    "purchase_order_id": 1,
    "product_id": 1,
    "quantity_ordered": 5,
    "unit_cost_expected": 300,
}
STOCK_TAKE_ITEM = {
    "id": 1,
    "stock_take_id": 1,
    "batch_id": 1,
    "product_id": 1,
    "expected_qty": 5,
}

# (table, valid baseline, column overrides that must be rejected, id for the report)
VIOLATIONS = [
    ("medicine_batches", BATCH, {"qty_remaining": -1}, "negative stock"),
    ("medicine_batches", BATCH, {"qty_received": -1}, "negative received"),
    ("medicine_batches", BATCH, {"cost_price": -1}, "negative cost"),
    ("medicine_batches", BATCH, {"selling_price": -1}, "negative selling price"),
    ("sales", SALE, {"subtotal": -1}, "negative subtotal"),
    ("sales", SALE, {"discount_amount": -1}, "negative discount"),
    ("sales", SALE, {"total_amount": -1}, "negative total"),
    ("sale_items", SALE_ITEM, {"quantity": 0}, "zero quantity sold"),
    ("sale_items", SALE_ITEM, {"qty_refunded": -1}, "negative refunded"),
    ("sale_items", SALE_ITEM, {"qty_refunded": 3}, "refunded more than sold"),
    ("sale_items", SALE_ITEM, {"unit_price": -1}, "negative unit price"),
    ("sale_items", SALE_ITEM, {"unit_cost": -1}, "negative unit cost"),
    ("sale_items", SALE_ITEM, {"line_total": -1}, "negative line total"),
    ("payments", PAYMENT, {"amount": 0}, "zero payment"),
    ("payments", PAYMENT, {"amount": -5}, "negative payment"),
    ("refunds", REFUND, {"total_amount": -1}, "negative refund total"),
    ("refund_items", REFUND_ITEM, {"quantity": 0}, "zero refund quantity"),
    ("refund_items", REFUND_ITEM, {"unit_price": -1}, "negative refund unit price"),
    ("refund_items", REFUND_ITEM, {"line_total": -1}, "negative refund line total"),
    ("purchase_order_items", PO_ITEM, {"quantity_ordered": 0}, "zero quantity ordered"),
    ("purchase_order_items", PO_ITEM, {"quantity_received": -1}, "negative quantity received"),
    ("purchase_order_items", PO_ITEM, {"unit_cost_expected": -1}, "negative expected cost"),
    ("purchase_order_items", PO_ITEM, {"unit_cost_actual": -1}, "negative actual cost"),
    ("stock_take_items", STOCK_TAKE_ITEM, {"expected_qty": -1}, "negative expected count"),
    ("stock_take_items", STOCK_TAKE_ITEM, {"physical_qty": -1}, "negative physical count"),
    ("stock_take_items", STOCK_TAKE_ITEM, {"unit_cost_at_close": -1}, "negative close cost"),
]


def _alembic(db_path: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _insert(connection: sqlite3.Connection, table: str, values: dict) -> None:
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(values.values()))


def _seed_parents(connection: sqlite3.Connection) -> None:
    _insert(connection, "products", {"id": 1, "name": "Seed Product"})


def _revision(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    (version,) = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    connection.close()
    return str(version)


def _check_names(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    ddl = " ".join(
        row[0] or ""
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name IN "
            "(" + ", ".join(f"'{table}'" for table in INVARIANTS) + ")"
        )
    )
    connection.close()
    return {name for name in _all_constraint_names() if name in ddl}


def _migration_checks() -> dict[str, list[tuple[str, str]]]:
    spec = importlib.util.spec_from_file_location(
        "m0039", BACKEND_DIR / "alembic" / "versions" / "0039_money_and_stock_checks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.CHECKS)


def _all_constraint_names() -> set[str]:
    return {name for checks in _migration_checks().values() for name, _rule in checks}


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("checks") / "migrated.db"
    _alembic(db_path, "upgrade", "head")
    return db_path


@pytest.mark.parametrize(
    ("table", "baseline", "override", "label"), VIOLATIONS, ids=[v[3] for v in VIOLATIONS]
)
def test_a_migrated_database_rejects_the_violation(migrated_db, table, baseline, override, label):
    connection = sqlite3.connect(migrated_db)
    connection.execute("PRAGMA foreign_keys=OFF")  # parents are irrelevant to a CHECK
    try:
        _insert(connection, table, baseline)  # the baseline itself is valid...
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert(connection, table, {**baseline, **override})  # ...the violation is not
    finally:
        connection.rollback()
        connection.close()


async def test_models_enforce_the_same_rules_in_a_model_built_database():
    async with AsyncSessionLocal() as db:
        product = Product(name="Check Product")
        db.add(product)
        await db.flush()
        db.add(
            MedicineBatch(
                product_id=product.id,
                batch_number="NEG",
                expiry_date=date(2097, 1, 1),
                qty_received=5,
                qty_remaining=-1,
                cost_price=1.0,
                selling_price=2.0,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


def test_the_migration_refuses_existing_violating_data_and_changes_nothing(tmp_path):
    db_path = tmp_path / "dirty.db"
    _alembic(db_path, "upgrade", "0038_products_category_id_index")
    connection = sqlite3.connect(db_path)
    _seed_parents(connection)
    _insert(connection, "medicine_batches", {**BATCH, "qty_remaining": -3})
    connection.commit()
    connection.close()

    result = _alembic(db_path, "upgrade", "head", check=False)

    assert result.returncode != 0
    assert "medicine_batches" in result.stderr and "qty_remaining >= 0" in result.stderr
    assert _revision(db_path) == "0038_products_category_id_index"
    connection = sqlite3.connect(db_path)
    (remaining,) = connection.execute("SELECT qty_remaining FROM medicine_batches").fetchone()
    connection.close()
    assert remaining == -3  # not clamped, not repaired: left exactly as found
    assert _check_names(db_path) == set()


def test_valid_data_and_indexes_survive_upgrade_and_downgrade_removes_the_rules(tmp_path):
    db_path = tmp_path / "valid.db"
    _alembic(db_path, "upgrade", "0038_products_category_id_index")
    connection = sqlite3.connect(db_path)
    _seed_parents(connection)
    _insert(connection, "medicine_batches", {"id": 1, **BATCH})
    _insert(connection, "sales", {**SALE, "idempotency_key": "KEY-1"})
    _insert(connection, "sale_items", SALE_ITEM)
    _insert(connection, "payments", PAYMENT)
    connection.commit()
    indexes_before = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    connection.close()

    _alembic(db_path, "upgrade", "head")

    connection = sqlite3.connect(db_path)
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("medicine_batches", "sales", "sale_items", "payments")
    }
    indexes_after = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    # The idempotency key must still be unique after the sales table was rebuilt.
    with pytest.raises(sqlite3.IntegrityError):
        _insert(connection, "sales", {**SALE, "id": 2, "idempotency_key": "KEY-1"})
    connection.rollback()
    connection.close()

    assert counts == {"medicine_batches": 1, "sales": 1, "sale_items": 1, "payments": 1}
    assert indexes_before <= indexes_after
    assert _check_names(db_path) == _all_constraint_names()

    _alembic(db_path, "downgrade", "0038_products_category_id_index")

    assert _check_names(db_path) == set()
    connection = sqlite3.connect(db_path)
    _insert(
        connection, "medicine_batches", {**BATCH, "id": 2, "qty_remaining": -1}
    )  # allowed again
    connection.rollback()
    connection.close()


def test_a_failure_part_way_through_the_upgrade_leaves_the_database_exactly_as_it_was(tmp_path):
    """
    Fault injection: a stray _alembic_tmp_payments table makes the LAST of the
    four table rebuilds fail, after three tables have already been rebuilt.
    Without the migration's single transaction that left some tables
    constrained, the revision still at 0038 and temp tables blocking every
    retry -- a half-upgraded database a shop could never start from.
    """
    db_path = tmp_path / "faulted.db"
    _alembic(db_path, "upgrade", "0038_products_category_id_index")
    connection = sqlite3.connect(db_path)
    _seed_parents(connection)
    _insert(connection, "medicine_batches", {"id": 1, **BATCH})
    connection.execute("CREATE TABLE _alembic_tmp_payments (x INTEGER)")  # the injected fault
    connection.commit()
    connection.close()

    result = _alembic(db_path, "upgrade", "head", check=False)

    assert result.returncode != 0
    assert "_alembic_tmp_payments already exists" in result.stderr
    assert _revision(db_path) == "0038_products_category_id_index"
    assert _check_names(db_path) == set()  # not even the tables rebuilt before the failure
    connection = sqlite3.connect(db_path)
    leftovers = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'"
        )
    }
    (remaining,) = connection.execute("SELECT qty_remaining FROM medicine_batches").fetchone()
    connection.execute("DROP TABLE _alembic_tmp_payments")  # remove the fault
    connection.commit()
    connection.close()
    assert leftovers == {"_alembic_tmp_payments"}  # only the injected one; nothing new
    assert remaining == BATCH["qty_remaining"]

    _alembic(db_path, "upgrade", "head")  # a clean retry now succeeds in full

    assert _revision(db_path) == "0039_money_and_stock_checks"
    assert _check_names(db_path) == _all_constraint_names()


def _normalised(sql: str) -> str:
    return " ".join(str(sql).split())


def test_rule_list_models_and_migration_agree_exactly():
    """
    Three places state these rules: the shared list (app/core/integrity_rules.py),
    the CheckConstraints on the models, and the frozen copy inside migration
    0039. If any one is edited without the others, this fails -- so the
    database a customer upgrades to can never differ from the one the tests
    (built from the models) run against.
    """
    from sqlalchemy import CheckConstraint

    from_models: dict[str, list[tuple[str, str]]] = {}
    for table in Base.metadata.tables.values():
        rules = sorted(
            (str(c.name), _normalised(str(c.sqltext)))
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name and str(c.name).startswith("ck_")
        )
        if rules:
            from_models[table.name] = rules

    def as_sorted(rules: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, str]]]:
        return {t: sorted((n, _normalised(r)) for n, r in rs) for t, rs in rules.items()}

    assert as_sorted(INVARIANTS) == from_models
    assert as_sorted(INVARIANTS) == as_sorted(_migration_checks())
