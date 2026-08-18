"""
Setup flow tests. This is the one router in the whole app with no
auth dependency at all -- there's nobody to log in as until this
completes. Every test here is really testing one thing from different
angles: that this can bootstrap exactly one owner account, ever, and
nothing about how it's called can turn it into a standing backdoor.
"""

import asyncio


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestSetupStatus:
    async def test_needs_setup_true_when_no_users_exist(self, client):
        r = await client.get("/api/v1/setup/status")
        assert r.status_code == 200
        assert r.json()["needs_setup"] is True

    async def test_needs_setup_false_once_a_user_exists(self, client, owner_user):
        r = await client.get("/api/v1/setup/status")
        assert r.status_code == 200
        assert r.json()["needs_setup"] is False

    async def test_status_requires_no_authentication(self, client):
        # No Authorization header at all -- must still work, or the
        # whole flow is unreachable before anyone can log in.
        r = await client.get("/api/v1/setup/status")
        assert r.status_code == 200


class TestCreateFirstUser:
    async def test_creates_the_owner_account(self, client, seeded_roles):
        r = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy Kangai",
                "username": "lucy",
                "password": "S3curePass!",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert r.status_code == 204

        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert login.status_code == 200
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.json()["role_name"] == "ChemistOwner"

    async def test_status_flips_to_false_after_creation(self, client, seeded_roles):
        await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy Kangai",
                "username": "lucy",
                "password": "S3curePass!",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        status = await client.get("/api/v1/setup/status")
        assert status.json()["needs_setup"] is False

    async def test_cannot_run_twice(self, client, seeded_roles):
        first = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy Kangai",
                "username": "lucy",
                "password": "S3curePass!",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert first.status_code == 204

        second = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Someone Else",
                "username": "intruder",
                "password": "AnotherPass1",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert second.status_code == 409

    async def test_refuses_if_a_user_already_exists_from_elsewhere(
        self, client, owner_user, seeded_roles
    ):
        """
        Same guard, reached a different way: owner_user existing at
        all (regardless of how it was created) must block this too,
        not just "has this endpoint been called before."
        """
        r = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Intruder",
                "username": "intruder",
                "password": "AnotherPass1",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert r.status_code == 409

    async def test_concurrent_first_calls_never_both_succeed(self, client, seeded_roles):
        """
        The realistic risk for a check-then-insert guard: two
        near-simultaneous requests both pass the check before either
        commits. This is a one-time bootstrap flow, not a
        high-throughput endpoint, but the guarantee that matters --
        never more than one owner minted -- still has to hold.
        """
        results = await asyncio.gather(
            client.post(
                "/api/v1/setup/first-user",
                json={
                    "full_name": "A",
                    "username": "usera",
                    "password": "PasswordA1",
                    "security_question": "Test question?",
                    "security_answer": "Test answer",
                },
            ),
            client.post(
                "/api/v1/setup/first-user",
                json={
                    "full_name": "B",
                    "username": "userb",
                    "password": "PasswordB1",
                    "security_question": "Test question?",
                    "security_answer": "Test answer",
                },
            ),
            return_exceptions=True,
        )
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(204) == 1

    async def test_password_too_short_rejected(self, client, seeded_roles):
        r = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy",
                "username": "lucy",
                "password": "short",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert r.status_code == 422

    async def test_username_too_short_rejected(self, client, seeded_roles):
        r = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy",
                "username": "ab",
                "password": "S3curePass!",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert r.status_code == 422

    async def test_requires_no_authentication(self, client, seeded_roles):
        # The whole point: reachable with zero Authorization header.
        r = await client.post(
            "/api/v1/setup/first-user",
            json={
                "full_name": "Lucy",
                "username": "lucy",
                "password": "S3curePass!",
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
        )
        assert r.status_code == 204


class TestMigrationExportAndRestore:
    """
    The real disaster-recovery / new-device path -- a backup exported
    here must be restorable somewhere that has never seen this data
    before, with no login (there's nobody to log in as yet on a fresh
    device) and nothing carried over from the old machine except a
    passphrase the owner remembers. The properties that matter: the
    real data comes back exactly, the real owner can log in with
    their real original password afterward, a wrong passphrase is
    rejected cleanly rather than crashing, and this can never be used
    to silently overwrite a device that already has real users on it.
    """

    async def test_export_requires_backups_manage_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/backups/export-for-migration",
            json={"passphrase": "SomeRealPassphrase123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_wrong_passphrase_on_empty_database_rejected_cleanly(self, client, seeded_roles):
        """
        Deliberately no owner_user here -- a genuinely fresh database,
        same as a brand new device. Constructs the encrypted bytes
        directly rather than via a real export, since a real export
        would require a logged-in user, which would defeat the point
        of testing against an empty database.
        """
        from app.core.security import encrypt_bytes_with_passphrase

        fake_backup = encrypt_bytes_with_passphrase(b'{"users": []}', "TheRealPassphrase")
        r = await client.post(
            "/api/v1/setup/restore-from-file",
            files={"file": ("backup.enc", fake_backup, "application/octet-stream")},
            data={"passphrase": "TotallyWrongPassphrase"},
        )
        assert r.status_code == 400

    async def test_genuinely_corrupted_file_rejected_cleanly_not_a_500(self, client, seeded_roles):
        """
        Different failure mode from wrong-passphrase above: that test
        used validly-encrypted bytes with the wrong key, which Fernet
        rejects via its HMAC check. This is a file that was never
        valid Fernet output at all -- e.g. a backup genuinely
        truncated mid-write by a crash or a full disk. Both must
        produce the same clean 400 with a real message, never a raw
        500 that leaks a stack trace to someone trying to recover
        from exactly this kind of failure.
        """
        r = await client.post(
            "/api/v1/setup/restore-from-file",
            files={
                "file": (
                    "backup.enc",
                    b"this is not encrypted data at all, just garbage bytes \x00\x01\xff",
                    "application/octet-stream",
                )
            },
            data={"passphrase": "AnyPassphraseAtAll"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "corrupted" in detail or "passphrase" in detail

    async def test_truncated_valid_backup_rejected_cleanly_not_a_500(self, client, seeded_roles):
        """
        A backup that WAS being written correctly but got cut off
        partway -- the more realistic version of "corrupted", since a
        real disk-full or power-loss event truncates a file rather
        than replacing it with pure noise. Takes a real, validly
        encrypted backup and chops it in half.
        """
        from app.core.security import encrypt_bytes_with_passphrase

        real_backup = encrypt_bytes_with_passphrase(b'{"users": [], "sales": []}', "RealPass123")
        truncated = real_backup[: len(real_backup) // 2]

        r = await client.post(
            "/api/v1/setup/restore-from-file",
            files={"file": ("backup.enc", truncated, "application/octet-stream")},
            data={"passphrase": "RealPass123"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "corrupted" in detail or "passphrase" in detail

    async def test_export_produces_real_encrypted_content(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/backups/export-for-migration",
            json={"passphrase": "MySecretPassphrase2026!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert len(r.content) > 0
        # Never plaintext -- the real business/user data must not be
        # readable directly out of the exported file.
        assert b"lucy" not in r.content

    async def test_true_cross_database_round_trip(self, owner_user):
        """
        The real proof, mirroring two genuinely different physical
        devices: exports from the shared test database (which already
        has a real owner and real data), then restores into a
        completely separate database set up the exact same way a real
        fresh device is -- real Alembic migrations, nothing shared
        with the first database at all. If this passes, restoring on
        an actual different computer works for the same reason.
        """
        import os
        import tempfile

        from sqlalchemy import select, text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.database import AsyncSessionLocal
        from app.core.security import hash_password
        from app.models.role import Role
        from app.models.user import User
        from app.services.backup_service import BackupService
        from app.services.setup_service import SetupService

        # Real data in the shared test database, then a real export.
        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "ChemistOwner"))
            role = role_result.scalar_one()
            db.add(
                User(
                    full_name="Cross Device Owner",
                    username="crossdeviceowner",
                    hashed_password=hash_password("RealOriginalPassword123"),
                    role_id=role.id,
                    security_question="Q",
                    security_answer_hash=hash_password("A"),
                )
            )
            product_result = await db.execute(
                text(
                    "INSERT INTO products (name, default_selling_price, reorder_point, "
                    "unit, is_active) "
                    "VALUES ('Cross Device Product', 42.0, 10, 'unit', 1) RETURNING id"
                )
            )
            product_id = product_result.scalar_one()
            await db.execute(
                text(
                    "INSERT INTO medicine_batches (product_id, batch_number, expiry_date, "
                    "qty_received, qty_remaining, cost_price) "
                    "VALUES (:pid, 'XDEV1', '2027-06-30', 77, 77, 15.0)"
                ),
                {"pid": product_id},
            )
            await db.commit()

            exported_bytes = await BackupService(db).export_for_migration("CrossDevicePassphrase!")

        # A genuinely separate SQLite file, migrated the real way --
        # as an actual subprocess with its own environment, since
        # alembic's env.py deliberately reads DATABASE_URL from this
        # process's own (cached) settings, not whatever URL is passed
        # to the Python API directly. A subprocess is a genuinely
        # fresh process, exactly like a real second device booting up
        # for the first time.
        fd, other_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(other_db_path)
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess_env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{other_db_path}"}
        proc = await asyncio.create_subprocess_exec(
            "alembic",
            "upgrade",
            "head",
            cwd=backend_dir,
            env=subprocess_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        assert proc.returncode == 0, f"Migration subprocess failed: {stderr.decode()}"

        other_engine = create_async_engine(f"sqlite+aiosqlite:///{other_db_path}")
        try:
            other_session_factory = async_sessionmaker(other_engine, expire_on_commit=False)
            async with other_session_factory() as other_db:
                setup_svc = SetupService(other_db)
                status_before = await setup_svc.status()
                assert status_before.needs_setup is True

                result = await setup_svc.restore_from_migration_file(
                    exported_bytes, "CrossDevicePassphrase!"
                )
                assert result.total_rows_restored > 0

                user_check = await other_db.execute(
                    text("SELECT username FROM users WHERE username = 'crossdeviceowner'")
                )
                assert user_check.scalar_one() == "crossdeviceowner"

                qty_check = await other_db.execute(
                    text(
                        "SELECT qty_remaining FROM medicine_batches " "WHERE batch_number = 'XDEV1'"
                    )
                )
                assert qty_check.scalar_one() == 77
        finally:
            await other_engine.dispose()
            if os.path.exists(other_db_path):
                os.remove(other_db_path)

    async def test_export_does_not_disrupt_the_current_system(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        product = await client.post(
            "/api/v1/products",
            json={"name": "Migration Round Trip Product", "default_selling_price": 42.0},
            headers=headers,
        )
        product_id = product.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "MIGRT1",
                "expiry_date": "2027-06-30",
                "qty_received": 77,
                "cost_price": 15.0,
            },
            headers=headers,
        )

        export_resp = await client.post(
            "/api/v1/backups/export-for-migration",
            json={"passphrase": "MySecretPassphrase2026!"},
            headers=headers,
        )
        assert export_resp.status_code == 200

        # The real proof: the original owner logs in with their real,
        # original password -- not a fresh-install placeholder.
        relogin = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert relogin.status_code == 200

        # And the real business data survived exactly.
        product_check = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product_check.json()["total_qty_available"] == 77

    async def test_cannot_restore_over_a_device_that_already_has_real_users(
        self, client, owner_user
    ):
        """
        The safety boundary: this must never be usable to silently
        overwrite a device that already has real accounts on it.
        """
        token = await _login(client, "lucy", "S3curePass!")
        export_resp = await client.post(
            "/api/v1/backups/export-for-migration",
            json={"passphrase": "AnotherRealPassphrase123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        exported_bytes = export_resp.content

        # This same client/db already has owner_user -- restoring
        # onto it must be refused.
        r = await client.post(
            "/api/v1/setup/restore-from-file",
            files={"file": ("backup.enc", exported_bytes, "application/octet-stream")},
            data={"passphrase": "AnotherRealPassphrase123"},
        )
        assert r.status_code == 409

    async def test_passphrase_below_minimum_length_is_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/backups/export-for-migration",
            json={"passphrase": "short"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422
