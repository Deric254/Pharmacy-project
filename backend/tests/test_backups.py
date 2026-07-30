"""
Backup tests. The properties that matter:
  1. The refresh token is actually encrypted at rest, verified by
     querying the raw DB column.
  2. A backup failure is logged with a reason and publishes
     backup.failed - never silently swallowed.
  3. The big one: restore actually restores. Real data goes in, gets
     backed up, gets destroyed, gets restored, and is verified present
     again with correct values - not just "did the endpoint return 200".
  4. A manifest mismatch refuses to restore rather than silently
     applying a possibly-corrupted backup.
"""

import json

import httpx
from fastapi import HTTPException

from app.core.database import AsyncSessionLocal
from app.core.events import CHANNEL
from app.core.redis_client import redis_client
from app.core.security import decrypt_bytes, encrypt_bytes
from app.models.backup import BackupOAuthToken
from app.models.product import Product
from app.services.backup.base import BackupProvider, BackupProviderError
from app.services.backup.google_drive import GoogleDriveBackupProvider
from app.services.backup_service import BackupService


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class FakeBackupProvider(BackupProvider):
    """In-memory stand-in, used via provider_override -- exercises the
    real BackupService logic without touching real Google OAuth."""

    def __init__(self, fail_upload: bool = False, fail_download: bool = False) -> None:
        self.storage: dict[str, bytes] = {}
        self.fail_upload = fail_upload
        self.fail_download = fail_download
        self._counter = 0

    async def upload(self, filename: str, data: bytes) -> str:
        if self.fail_upload:
            raise BackupProviderError("simulated upload failure")
        self._counter += 1
        reference = f"fake-file-{self._counter}"
        self.storage[reference] = data
        return reference

    async def download(self, reference: str) -> bytes:
        if self.fail_download:
            raise BackupProviderError("simulated download failure")
        return self.storage[reference]


class TestConnectGoogleDrive:
    async def test_refresh_token_is_encrypted_in_database(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        raw_refresh_token = "1//real-looking-google-refresh-token-abc123"

        r = await client.post(
            "/api/v1/backups/connect-google-drive",
            json={"refresh_token": raw_refresh_token},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(BackupOAuthToken))
            token_row = result.scalar_one()
            assert token_row.encrypted_refresh_token != raw_refresh_token.encode()
            assert raw_refresh_token.encode() not in token_row.encrypted_refresh_token
            assert decrypt_bytes(token_row.encrypted_refresh_token).decode() == raw_refresh_token

    async def test_requires_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/backups/connect-google-drive",
            json={"refresh_token": "whatever"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_reconnecting_overwrites_the_previous_token(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/backups/connect-google-drive",
            json={"refresh_token": "first-token"},
            headers=headers,
        )
        await client.post(
            "/api/v1/backups/connect-google-drive",
            json={"refresh_token": "second-token"},
            headers=headers,
        )

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(BackupOAuthToken))
            rows = result.scalars().all()
            assert len(rows) == 1  # one row per provider, overwritten not duplicated
            assert decrypt_bytes(rows[0].encrypted_refresh_token).decode() == "second-token"


class TestRunBackup:
    async def test_local_backup_requires_no_configuration_at_all(self, client, owner_user):
        """
        The actual bug this closes: previously POST /backups/run
        always required Google Drive connected first, with no offline
        path at all -- confirmed directly against a real running
        server before this fix existed. Local is now the default, and
        needs nothing set up beforehand.
        """
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post("/api/v1/backups/run", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201
        assert r.json()["status"] == "SUCCESS"
        assert r.json()["provider"] == "LOCAL_FILE"

    async def test_local_backup_writes_a_real_file_that_can_be_read_back(
        self, client, owner_user, tmp_path, monkeypatch
    ):
        from app.core.config import Settings

        monkeypatch.setattr(Settings, "local_backup_dir", property(lambda self: tmp_path))
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post("/api/v1/backups/run", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201
        reference = r.json()["reference"]
        assert reference is not None

        written_files = list(tmp_path.glob("pharmacy-backup-*.enc"))
        assert len(written_files) == 1
        assert written_files[0].read_bytes()  # genuinely has content, not an empty file

    async def test_google_drive_still_requires_connection_when_explicitly_chosen(
        self, client, owner_user
    ):
        # Local being the default doesn't remove the real requirement
        # for Google Drive specifically -- someone who explicitly asks
        # for it still needs to have connected it first.
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/backups/run",
            json={"provider": "google_drive"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "not connected" in r.json()["detail"]

    async def test_successful_backup_logs_success_with_manifest(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            fake_provider = FakeBackupProvider()
            service = BackupService(db, provider_override=fake_provider)
            result = await service.run_backup(owner_user)

            assert result.status.value == "SUCCESS"
            assert result.reference is not None
            assert result.size_bytes is not None and result.size_bytes > 0
            assert fake_provider.storage  # something was actually "uploaded"

    async def test_failed_upload_logs_failure_and_publishes_event(self, client, owner_user):
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(CHANNEL)
        await pubsub.get_message(timeout=1)

        async with AsyncSessionLocal() as db:
            fake_provider = FakeBackupProvider(fail_upload=True)
            service = BackupService(db, provider_override=fake_provider)
            result = await service.run_backup(owner_user)

            assert result.status.value == "FAILED"
            assert result.error_message is not None
            assert "simulated upload failure" in result.error_message

        found = False
        for _ in range(10):
            message = await pubsub.get_message(timeout=1)
            if message and message["type"] == "message":
                envelope = json.loads(message["data"])
                if envelope["event_type"] == "backup.failed":
                    assert "simulated upload failure" in envelope["payload"]["reason"]
                    found = True
                    break
        await pubsub.unsubscribe(CHANNEL)
        assert found, "Expected a backup.failed event to be published"

    async def test_run_backup_requires_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post("/api/v1/backups/run", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403


class TestRestoreBackup:
    async def test_restore_requires_explicit_confirm(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            fake_provider = FakeBackupProvider()
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            f"/api/v1/backups/{backup.id}/restore",
            json={"confirm": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "confirm=true" in r.json()["detail"]

    async def test_cannot_restore_a_failed_backup(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            fake_provider = FakeBackupProvider(fail_upload=True)
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            f"/api/v1/backups/{backup.id}/restore",
            json={"confirm": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    async def test_full_backup_then_disaster_then_restore_cycle(self, client, owner_user):
        """
        The test that actually matters for this module: create real
        data, back it up, genuinely destroy it, restore, and confirm
        it's really back with the correct values - not a mocked
        assertion anywhere in the destroy/restore path.
        """
        # 1. Create real data.
        async with AsyncSessionLocal() as db:
            product = Product(name="Disaster Recovery Test Product", default_selling_price=42.0)
            db.add(product)
            await db.commit()
            product_id = int(product.id)

        # 2. Back it up for real (through BackupService, real dump/encrypt).
        fake_provider = FakeBackupProvider()
        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        # 3. Genuinely destroy the data.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            await db.execute(Product.__table__.delete().where(Product.id == product_id))
            await db.commit()
            result = await db.execute(select(Product).where(Product.id == product_id))
            assert result.scalar_one_or_none() is None  # confirmed gone

        # 4. Restore.
        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            restore_result = await service.restore_backup(backup.id, confirm=True, user=owner_user)
            assert restore_result.manifest_matched is True
            assert restore_result.total_rows_restored > 0

        # 5. Confirm it's genuinely back, with the correct value.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(Product).where(Product.id == product_id))
            restored_product = result.scalar_one_or_none()
            assert restored_product is not None
            assert restored_product.name == "Disaster Recovery Test Product"
            assert restored_product.default_selling_price == 42.0

    async def test_manifest_mismatch_refuses_to_restore(self, client, owner_user):
        """
        Simulates a corrupted/tampered backup file: the stored manifest
        (recorded at backup time) no longer matches what's actually in
        the downloaded blob. Restore must refuse rather than silently
        applying a possibly-bad backup.
        """
        fake_provider = FakeBackupProvider()
        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        # Tamper with the "uploaded" blob directly in fake storage to
        # simulate corruption/tampering after the fact.
        tampered_dump = {"products": [{"fake": "corrupted data with wrong shape"}]}
        assert backup.reference is not None
        fake_provider.storage[backup.reference] = encrypt_bytes(json.dumps(tampered_dump).encode())

        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            raised = False
            try:
                await service.restore_backup(backup.id, confirm=True, user=owner_user)
            except HTTPException as exc:
                raised = True
                assert exc.status_code == 400
                assert "does not match" in exc.detail
            assert raised, "Expected restore to refuse on manifest mismatch"

    async def test_restore_requires_permission(self, client, employee_user, owner_user):
        fake_provider = FakeBackupProvider()
        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            f"/api/v1/backups/{backup.id}/restore",
            json={"confirm": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_restoring_an_older_backup_missing_newer_columns_still_works(
        self, client, owner_user
    ):
        """
        Simulates restoring a genuinely older backup -- taken before a
        migration added new columns (must_change_password,
        security_question, security_answer_hash all arrived in later
        migrations than the earliest users table). The real risk this
        proves doesn't happen: an old backup silently failing to
        restore, or restoring with a NOT NULL constraint violation,
        once the app has moved on to a newer schema. Column-level
        defaults must apply correctly even when the backed-up row
        never had that column at all.
        """
        fake_provider = FakeBackupProvider()
        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            backup = await service.run_backup(owner_user)

        # Same row counts as the real backup (so the manifest check
        # still passes -- this genuinely is "the same backup", just
        # missing columns that didn't exist when it was taken), but
        # the users row simulates the older, pre-migration shape.
        async with AsyncSessionLocal() as db:
            from app.services.backup.dump_restore import dump_all_tables

            real_dump = await dump_all_tables(db)

        old_style_dump = dict(real_dump)
        old_style_dump["users"] = [
            {
                key: value
                for key, value in row.items()
                if key not in ("must_change_password", "security_question", "security_answer_hash")
            }
            for row in real_dump["users"]
        ]

        assert backup.reference is not None
        from app.services.backup.dump_restore import serialize_dump

        fake_provider.storage[backup.reference] = encrypt_bytes(serialize_dump(old_style_dump))

        async with AsyncSessionLocal() as db:
            service = BackupService(db, provider_override=fake_provider)
            result = await service.restore_backup(backup.id, confirm=True, user=owner_user)
        assert result.manifest_matched is True

        async with AsyncSessionLocal() as db:
            from sqlalchemy import text

            row = (
                await db.execute(
                    text(
                        "SELECT must_change_password, security_question FROM users "
                        "WHERE username='lucy'"
                    )
                )
            ).one()
            # False (the real column default), not NULL and not a
            # crash, even though the "old" backup never had this
            # column at all.
            assert row[0] == 0
            assert row[1] is None


class TestListBackups:
    async def test_list_backups_shows_both_success_and_failure(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            await BackupService(db, provider_override=FakeBackupProvider()).run_backup(owner_user)
        async with AsyncSessionLocal() as db:
            await BackupService(
                db, provider_override=FakeBackupProvider(fail_upload=True)
            ).run_backup(owner_user)

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/backups", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        statuses = {b["status"] for b in r.json()}
        assert statuses == {"SUCCESS", "FAILED"}


class TestGoogleDriveAdapterRequestShape:
    """
    Verifies the real adapter's HTTP request construction via
    httpx.MockTransport -- no live call to Google's actual OAuth/Drive
    endpoints, same approach used for the AI provider adapters.
    """

    async def test_upload_refreshes_token_then_uploads(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "oauth2.googleapis.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "fresh-access-token"})
            assert request.headers.get("authorization") == "Bearer fresh-access-token"
            return httpx.Response(200, json={"id": "drive-file-id-123"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GoogleDriveBackupProvider(
            refresh_token="fake-refresh",
            client_id="fake-client-id",
            client_secret="fake-secret",
            client=http_client,
        )

        reference = await provider.upload("backup.enc", b"encrypted-bytes-here")

        assert reference == "drive-file-id-123"
        assert any("oauth2.googleapis.com/token" in url for url in calls)
        assert any("googleapis.com/upload/drive" in url for url in calls)

    async def test_download_refreshes_token_then_downloads(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "oauth2.googleapis.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "fresh-access-token"})
            assert "drive-file-id-123" in str(request.url)
            return httpx.Response(200, content=b"decrypted-would-happen-outside-this")

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GoogleDriveBackupProvider(
            refresh_token="fake-refresh",
            client_id="fake-client-id",
            client_secret="fake-secret",
            client=http_client,
        )

        data = await provider.download("drive-file-id-123")
        assert data == b"decrypted-would-happen-outside-this"

    async def test_token_refresh_failure_raises_backup_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GoogleDriveBackupProvider(
            refresh_token="expired-refresh",
            client_id="id",
            client_secret="secret",
            client=http_client,
        )

        raised = False
        try:
            await provider.upload("x.enc", b"data")
        except BackupProviderError:
            raised = True
        assert raised, "Expected BackupProviderError when token refresh fails"
