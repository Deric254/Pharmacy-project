"""
The desktop launcher must never lock a shop out of its own app because of the
safety checks added around startup and migration 0039.

Two ways that could have happened, both covered here:
  * a stray machine-wide environment variable (ENVIRONMENT, REDIS_MODE,
    WEB_CONCURRENCY, JWT_SECRET_KEY, ENCRYPTION_KEY) left by unrelated
    software on the PC, rejected by startup validation;
  * existing records that already break a rule migration 0039 enforces.
"""

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import desktop_main
from app.core.config import (
    ALLOWED_ENVIRONMENTS,
    ALLOWED_REDIS_MODES,
    Settings,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
BEFORE_0039 = "0038_products_category_id_index"
HEAD_0039 = "0039_money_and_stock_checks"
VALID_JWT = "j" * 40
VALID_AES = "cGhhcm1hY3ktZXJwLXRlc3Qta2V5LTAxMjM0NTY3ODk="

BATCH = (
    "INSERT INTO medicine_batches(id, product_id, batch_number, expiry_date, qty_received,"
    " qty_remaining, cost_price, selling_price) VALUES (?, 1, 'B', '2097-01-01', 10, ?, 100, 200)"
)


# ---------------------------------------------------------------- stray settings


@pytest.fixture
def isolated_environment(monkeypatch):
    """A private copy of os.environ, so the launcher's writes cannot leak into other tests."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    return os.environ


def test_stray_machine_settings_are_ignored_instead_of_blocking_startup(
    isolated_environment, tmp_path
):
    isolated_environment.update(
        {
            "ENVIRONMENT": "Prod",
            "REDIS_MODE": "Memory",
            "WEB_CONCURRENCY": "8",
            "JWT_SECRET_KEY": "stray-short",
            "ENCRYPTION_KEY": "stray-not-a-key",
        }
    )

    desktop_main._configure_environment(tmp_path, 8000)
    settings = Settings(_env_file=None)  # would raise if anything unusable were left

    persisted = desktop_main._load_or_create_secrets(tmp_path)
    assert settings.environment == "production"
    assert settings.redis_mode == "memory"
    assert settings.jwt_secret_key == persisted["jwt_secret_key"]
    assert settings.encryption_key == persisted["encryption_key"]
    assert "WEB_CONCURRENCY" not in os.environ


def test_only_the_names_of_ignored_settings_are_logged_never_their_values(
    isolated_environment, tmp_path
):
    isolated_environment.update(
        {
            "JWT_SECRET_KEY": "stray-short",
            "ENCRYPTION_KEY": "stray-not-a-key",
            "ENVIRONMENT": "Prod",
        }
    )

    desktop_main._configure_environment(tmp_path, 8000)

    log = (tmp_path / "logs" / "backend.log").read_text()
    for name in ("JWT_SECRET_KEY", "ENCRYPTION_KEY", "ENVIRONMENT"):
        assert name in log
    for value in ("stray-short", "stray-not-a-key", "Prod"):
        assert value not in log


def test_settings_that_were_set_on_purpose_and_are_usable_are_left_alone(
    isolated_environment, tmp_path
):
    isolated_environment.update(
        {
            "ENVIRONMENT": "staging",
            "REDIS_MODE": "redis",
            "JWT_SECRET_KEY": VALID_JWT,
            "ENCRYPTION_KEY": VALID_AES,
        }
    )

    desktop_main._configure_environment(tmp_path, 8000)

    assert os.environ["ENVIRONMENT"] == "staging"
    assert os.environ["REDIS_MODE"] == "redis"
    assert os.environ["JWT_SECRET_KEY"] == VALID_JWT
    assert os.environ["ENCRYPTION_KEY"] == VALID_AES


def test_the_launchers_allowed_values_match_what_settings_really_accepts():
    from typing import get_args

    assert set(ALLOWED_ENVIRONMENTS) == set(
        get_args(Settings.model_fields["environment"].annotation)
    )
    assert set(ALLOWED_REDIS_MODES) == set(get_args(Settings.model_fields["redis_mode"].annotation))


# ---------------------------------------------------------- tolerated 0039 refusal


def _environment_for(db_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "JWT_SECRET_KEY": VALID_JWT,
        "ENCRYPTION_KEY": VALID_AES,
        "REDIS_MODE": "memory",
        "ENVIRONMENT": "development",
    }


def _alembic(db_path: Path, *arguments: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=_environment_for(db_path),
        check=True,
        capture_output=True,
    )


# Runs exactly what the launcher runs at startup, then proves the real app serves
# requests on whatever schema is left.
DRIVER = textwrap.dedent(
    """
    import asyncio, pathlib, sys
    import desktop_main

    desktop_main._run_migrations(pathlib.Path(sys.argv[1]))

    async def smoke():
        import httpx
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            status = await client.get("/api/v1/setup/status")
            return health.status_code, status.status_code

    print("SMOKE", asyncio.run(smoke()))
    """
)


def _run_launcher_migration(db_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", DRIVER, str(db_path)],
        cwd=BACKEND_DIR,
        env=_environment_for(db_path),
        capture_output=True,
        text=True,
    )


def _database_before_0039(tmp_path: Path, name: str, *, qty_remaining: int) -> Path:
    db_path = tmp_path / name
    _alembic(db_path, "upgrade", BEFORE_0039)
    connection = sqlite3.connect(db_path)
    connection.execute("INSERT INTO products(id, name) VALUES (1, 'P')")
    connection.execute(BATCH, (7, qty_remaining))
    connection.commit()
    connection.close()
    return db_path


def _revision(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    (version,) = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    connection.close()
    return str(version)


def test_bad_existing_data_does_not_lock_the_shop_out(tmp_path):
    db_path = _database_before_0039(tmp_path, "shop.db", qty_remaining=-3)

    result = _run_launcher_migration(db_path)

    assert result.returncode == 0, result.stderr  # the launcher carried on
    assert "SMOKE (200, 200)" in result.stdout  # and the real app serves requests
    assert _revision(db_path) == BEFORE_0039  # nothing was half-applied
    connection = sqlite3.connect(db_path)
    (stock,) = connection.execute("SELECT qty_remaining FROM medicine_batches").fetchone()
    connection.close()
    assert stock == -3  # data left exactly as found, never "repaired"


def test_the_owner_gets_a_report_naming_the_rows_to_fix(tmp_path):
    db_path = _database_before_0039(tmp_path, "shop.db", qty_remaining=-3)

    _run_launcher_migration(db_path)

    report = (tmp_path / "integrity-report.txt").read_text()
    assert "medicine_batches" in report
    assert "qty_remaining >= 0" in report
    assert "rowid(s): 7" in report
    assert "NOT changed" in report
    log = (tmp_path / "logs" / "backend.log").read_text()
    assert "upgrade-skipped-existing-data-breaks-rules" in log


def test_the_upgrade_completes_by_itself_once_the_rows_are_corrected(tmp_path):
    db_path = _database_before_0039(tmp_path, "shop.db", qty_remaining=-3)
    _run_launcher_migration(db_path)
    assert _revision(db_path) == BEFORE_0039

    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE medicine_batches SET qty_remaining = 0 WHERE id = 7")
    connection.commit()
    connection.close()
    result = _run_launcher_migration(db_path)

    assert result.returncode == 0, result.stderr
    assert _revision(db_path) == HEAD_0039


def test_clean_data_upgrades_normally_and_writes_no_report(tmp_path):
    db_path = _database_before_0039(tmp_path, "shop.db", qty_remaining=10)

    result = _run_launcher_migration(db_path)

    assert result.returncode == 0, result.stderr
    assert _revision(db_path) == HEAD_0039
    assert not (tmp_path / "integrity-report.txt").exists()


def test_any_other_migration_failure_is_still_fatal_and_leaves_nothing_behind(tmp_path):
    """Only the specific refusal is tolerated; a real failure must still stop startup."""
    db_path = _database_before_0039(tmp_path, "shop.db", qty_remaining=10)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE _alembic_tmp_payments (x INTEGER)")  # injected fault
    connection.commit()
    connection.close()

    result = _run_launcher_migration(db_path)

    assert result.returncode != 0
    assert _revision(db_path) == BEFORE_0039
    assert not (tmp_path / "integrity-report.txt").exists()


@pytest.mark.parametrize(
    ("head", "current", "tolerated"),
    [
        (HEAD_0039, BEFORE_0039, True),
        ("0040_something_later", BEFORE_0039, False),  # a later migration may need the schema
        (HEAD_0039, "0037_restore_lost_indexes", False),  # not the one revision known to be safe
        (HEAD_0039, None, False),
        (None, BEFORE_0039, False),
    ],
)
def test_the_refusal_is_tolerated_only_for_the_one_known_safe_upgrade(head, current, tolerated):
    assert desktop_main._refusal_may_be_tolerated(head, current) is tolerated
