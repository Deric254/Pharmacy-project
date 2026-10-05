"""scripts/check_data_integrity.py: a read-only scan that names offending rows."""

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND_DIR / "scripts" / "check_data_integrity.py"
LAST_UNCONSTRAINED = "0038_products_category_id_index"


def _run(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def old_schema_db_factory(tmp_path_factory):
    """A database at the last revision BEFORE the rules exist, so bad rows can be stored."""
    import os

    def make(name: str) -> Path:
        path = tmp_path_factory.mktemp("checker") / name
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", LAST_UNCONSTRAINED],
            cwd=BACKEND_DIR,
            env=env,
            check=True,
            capture_output=True,
        )
        return path

    return make


def test_a_clean_database_passes(old_schema_db_factory):
    db = old_schema_db_factory("clean.db")
    result = _run(str(db))
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_it_names_each_broken_rule_and_the_offending_rows_and_changes_nothing(
    old_schema_db_factory,
):
    db = old_schema_db_factory("dirty.db")
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO products(id, name) VALUES (1, 'P')")
    connection.execute(
        "INSERT INTO medicine_batches(id, product_id, batch_number, expiry_date, qty_received,"
        " qty_remaining, cost_price, selling_price)"
        " VALUES (7, 1, 'B', '2097-01-01', 10, -4, 100, 200)"
    )
    connection.commit()
    connection.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    result = _run(str(db))

    assert result.returncode == 1
    assert "medicine_batches" in result.stdout
    assert "qty_remaining >= 0" in result.stdout
    assert "rowid(s): 7" in result.stdout
    assert "Nothing was changed" in result.stdout
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # read-only


def test_the_migration_error_points_to_the_checker(old_schema_db_factory):
    import os

    db = old_schema_db_factory("refused.db")
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO products(id, name) VALUES (1, 'P')")
    connection.execute(
        "INSERT INTO medicine_batches(id, product_id, batch_number, expiry_date, qty_received,"
        " qty_remaining, cost_price, selling_price)"
        " VALUES (1, 1, 'B', '2097-01-01', 10, -1, 100, 200)"
    )
    connection.commit()
    connection.close()
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "scripts/check_data_integrity.py" in result.stderr


def test_a_missing_file_is_a_clear_error(tmp_path):
    result = _run(str(tmp_path / "nope.db"))
    assert result.returncode == 2
    assert "no such file" in result.stdout


def test_a_file_that_is_not_a_pharmacy_database_is_a_clear_error(tmp_path):
    other = tmp_path / "other.db"
    sqlite3.connect(other).close()
    result = _run(str(other))
    assert result.returncode == 2
    assert "does not look like a Pharmacy ERP database" in result.stdout


def test_wrong_usage_prints_usage():
    result = _run()
    assert result.returncode == 2
    assert "usage" in result.stdout
