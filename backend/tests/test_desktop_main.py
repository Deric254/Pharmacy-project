"""
Desktop launcher tests: the pieces of desktop_main.py that decide what is
exposed to the network, whether the app can start again after a crash, and
whether the shop's data survives an upgrade.
"""

import ipaddress
import json
import socket
import sqlite3

import pytest

import desktop_main


class TestLoopbackOnly:
    def test_the_server_binds_only_the_loopback_interface(self):
        assert ipaddress.ip_address(desktop_main._BIND_HOST).is_loopback

    def test_the_port_probe_checks_the_interface_the_server_will_bind(self):
        with socket.socket() as holder:
            holder.bind((desktop_main._BIND_HOST, 0))
            holder.listen()
            port = holder.getsockname()[1]

            assert desktop_main._port_is_available(port) is False

        assert desktop_main._port_is_available(port) is True


class TestSecretsFile:
    def test_first_run_writes_a_complete_secrets_file_and_leaves_no_temp_file(self, tmp_path):
        generated = desktop_main._load_or_create_secrets(tmp_path)

        assert json.loads((tmp_path / "secrets.json").read_text()) == generated
        assert set(generated) == {"jwt_secret_key", "encryption_key"}
        assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]

    def test_the_same_secrets_are_reused_on_the_next_launch(self, tmp_path):
        first = desktop_main._load_or_create_secrets(tmp_path)

        assert desktop_main._load_or_create_secrets(tmp_path) == first

    def test_a_crash_while_saving_never_leaves_a_half_written_secrets_file(
        self, tmp_path, monkeypatch
    ):
        def power_cut(*args, **kwargs):
            raise OSError("simulated crash before the swap")

        monkeypatch.setattr(desktop_main.os, "replace", power_cut)
        with pytest.raises(OSError):
            desktop_main._load_or_create_secrets(tmp_path)
        assert not (tmp_path / "secrets.json").exists()

        monkeypatch.undo()
        # The next launch starts cleanly instead of choking on a broken file.
        assert set(desktop_main._load_or_create_secrets(tmp_path)) == {
            "jwt_secret_key",
            "encryption_key",
        }


def _database_at_revision(path, revision):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    connection.execute("CREATE TABLE shop (name TEXT)")
    connection.execute("INSERT INTO shop VALUES ('Real customer data')")
    connection.commit()
    connection.close()


def _snapshots(db_path):
    return sorted((db_path.parent / "pre-upgrade-snapshots").glob("*.db"))


class TestPreUpgradeSnapshot:
    def test_a_pending_upgrade_snapshots_the_current_data_first(self, tmp_path):
        db = tmp_path / "pharmacy.db"
        _database_at_revision(db, "0035")

        desktop_main._snapshot_before_upgrade(db, "0036")

        (snapshot,) = _snapshots(db)
        assert snapshot.name.endswith("-0035-to-0036.db")
        copy = sqlite3.connect(snapshot)
        assert copy.execute("SELECT name FROM shop").fetchall() == [("Real customer data",)]
        assert copy.execute("SELECT version_num FROM alembic_version").fetchall() == [("0035",)]
        copy.close()

    def test_no_snapshot_when_the_database_is_already_up_to_date(self, tmp_path):
        db = tmp_path / "pharmacy.db"
        _database_at_revision(db, "0036")

        desktop_main._snapshot_before_upgrade(db, "0036")

        assert _snapshots(db) == []

    def test_no_snapshot_on_a_first_run_with_no_database_yet(self, tmp_path):
        db = tmp_path / "pharmacy.db"

        desktop_main._snapshot_before_upgrade(db, "0036")

        assert _snapshots(db) == []

    def test_no_snapshot_of_an_empty_database_with_no_version_table(self, tmp_path):
        db = tmp_path / "pharmacy.db"
        sqlite3.connect(db).close()

        desktop_main._snapshot_before_upgrade(db, "0036")

        assert _snapshots(db) == []

    def test_only_the_newest_few_snapshots_are_kept(self, tmp_path):
        db = tmp_path / "pharmacy.db"
        _database_at_revision(db, "0001")

        for head in ("h1", "h2", "h3", "h4", "h5"):
            desktop_main._snapshot_before_upgrade(db, head)

        kept = [p.name for p in _snapshots(db)]
        assert len(kept) == desktop_main._SNAPSHOTS_TO_KEEP
        assert kept[-1].endswith("-to-h5.db")
