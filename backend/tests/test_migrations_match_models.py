"""
A database built by running every Alembic migration must match what the
models declare. Every other test builds its schema straight from the
models (`create_all`), so a migration that drifts from them -- an index
lost when a later migration rebuilt a table, a column left nullable --
is otherwise invisible until it bites a real install.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.database import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Differences that are known and accepted, each for a stated reason. A
# NEW difference is not on this list and fails the test.
#
# Nullability: these columns were created without NOT NULL (they all have
# a server default, and no code path ever writes NULL) while the models
# declare them NOT NULL. Tightening them would mean rebuilding eighteen
# tables holding customers' live data, which costs more risk than the
# constraint buys.
NULLABLE_IN_DATABASE_ONLY = frozenset(
    {
        ("ai_provider_keys", "created_at"),
        ("audit_logs", "created_at"),
        ("backup_logs", "created_at"),
        ("backup_oauth_tokens", "created_at"),
        ("business_config", "updated_at"),
        ("customers", "created_at"),
        ("medicine_batches", "created_at"),
        ("products", "created_at"),
        ("purchase_orders", "created_at"),
        ("refunds", "created_at"),
        ("roles", "description"),
        ("sales", "created_at"),
        ("stock_movements", "created_at"),
        ("stock_takes", "started_at"),
        ("supplier_transactions", "created_at"),
        ("suppliers", "created_at"),
        ("user_sessions", "created_at"),
        ("users", "created_at"),
    }
)
# Deprecated by migration 0036 and deliberately left in place until a later
# migration drops it (never drop a column in the release that stops using it).
DEPRECATED_COLUMNS = frozenset({("products", "default_selling_price")})
# An expression index (COLLATE NOCASE) is not something SQLite's reflection
# can round-trip, so alembic always reports it as changed. It is verified
# directly in test_the_product_name_index_is_case_insensitive instead.
UNREFLECTABLE_INDEXES = frozenset({"ix_products_name_active_unique"})


def _run_alembic(db_path: Path, *arguments: str) -> None:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    subprocess.run(  # noqa: S603 - fixed argv, no untrusted input
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
    )


def _flatten(diffs: list) -> list[tuple]:
    flat: list[tuple] = []
    for diff in diffs:
        flat.extend(diff if isinstance(diff, list) else [diff])
    return flat


def _is_accepted(diff: tuple) -> bool:
    kind = diff[0]
    if kind == "modify_nullable":
        return (diff[2], diff[3]) in NULLABLE_IN_DATABASE_ONLY
    if kind == "remove_column":
        return (diff[2], diff[3].name) in DEPRECATED_COLUMNS
    if kind in ("add_index", "remove_index"):
        return diff[1].name in UNREFLECTABLE_INDEXES
    return False


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("migrated") / "migrated.db"
    _run_alembic(db_path, "upgrade", "head")
    return db_path


def test_migrations_and_models_declare_the_same_schema(migrated_db):
    engine = create_engine(f"sqlite:///{migrated_db}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = _flatten(compare_metadata(context, Base.metadata))
    engine.dispose()

    unexpected = [d for d in differences if not _is_accepted(d)]
    assert unexpected == []


def test_the_product_name_index_is_case_insensitive(migrated_db):
    connection = sqlite3.connect(migrated_db)
    (ddl,) = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'ix_products_name_active_unique'"
    ).fetchone()
    assert "COLLATE NOCASE" in ddl

    connection.execute("INSERT INTO products (name) VALUES ('Aspirin')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO products (name) VALUES ('aspirin')")
    connection.close()


def test_upgrading_renames_existing_case_variant_duplicates_instead_of_failing(tmp_path):
    db_path = tmp_path / "old.db"
    _run_alembic(db_path, "upgrade", "0036_batch_selling_price_required")
    connection = sqlite3.connect(db_path)
    # The state the lost index allowed on a real install.
    connection.executemany(
        "INSERT INTO products (id, name) VALUES (?, ?)",
        [(1, "Aspirin"), (2, "aspirin"), (3, "ASPIRIN"), (4, "Ibuprofen")],
    )
    connection.commit()
    connection.close()

    _run_alembic(db_path, "upgrade", "head")

    connection = sqlite3.connect(db_path)
    names = dict(connection.execute("SELECT id, name FROM products"))
    connection.close()
    assert names == {
        1: "Aspirin",
        2: "aspirin (duplicate #2)",
        3: "ASPIRIN (duplicate #3)",
        4: "Ibuprofen",
    }
