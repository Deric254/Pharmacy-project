"""
Audit log read tests. Covers the actual gap that was found: the audit
trail was being written throughout the app the whole time, but nothing
could ever read it back. These tests exercise the real read path,
the permission boundary, and the name-snapshot behavior that motivated
adding the column in the first place.
"""


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestPermissionBoundary:
    async def test_requires_audit_view_permission(self, client, administrator_user):
        """
        Administrator has users.manage and roles.manage is NOT
        included with it -- audit.view is a third, separate
        permission, granted to ChemistOwner only by default. This is
        the whole point of making it its own permission rather than
        folding it into an existing one.
        """
        token = await _login(client, "sam", "AdminPass1")
        r = await client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_owner_can_view(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    async def test_requires_authentication_at_all(self, client, owner_user):
        r = await client.get("/api/v1/audit-logs")
        assert r.status_code == 401


class TestAuditLogContent:
    async def test_a_real_login_produces_a_readable_entry_with_the_actors_name(
        self, client, owner_user
    ):
        """
        End-to-end: log in as the owner (which itself writes an audit
        row), then read that exact row back through the endpoint being
        tested, and confirm the name snapshot -- not just a bare
        user_id -- is what's actually returned.
        """
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/audit-logs",
            params={"action": "login.success"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        entry = body["entries"][0]
        assert entry["action"] == "login.success"
        assert entry["user_name_snapshot"] == "Lucy Kangai"

    async def test_a_failed_login_is_also_logged(self, client, owner_user):
        await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "wrong-password"}
        )
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/audit-logs",
            params={"action": "login.failed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    async def test_entries_are_ordered_newest_first(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        r = await client.get(
            "/api/v1/audit-logs",
            params={"action": "login.success"},
            headers={"Authorization": f"Bearer {token}"},
        )
        entries = r.json()["entries"]
        ids = [e["id"] for e in entries]
        assert ids == sorted(ids, reverse=True)

    async def test_filters_by_entity_type(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/audit-logs",
            params={"entity_type": "user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        for entry in r.json()["entries"]:
            assert entry["entity_type"] == "user"

    async def test_filters_by_entity_type_that_matches_nothing(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/audit-logs",
            params={"entity_type": "nonexistent-entity-type"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["entries"] == []
        assert r.json()["total"] == 0


class TestPagination:
    async def test_limit_is_respected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        for _ in range(3):
            await client.post(
                "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
            )
        r = await client.get(
            "/api/v1/audit-logs",
            params={"limit": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(r.json()["entries"]) == 2
        assert r.json()["total"] >= 4

    async def test_limit_above_the_maximum_is_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/audit-logs",
            params={"limit": 500},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_offset_moves_the_window(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        for _ in range(3):
            await client.post(
                "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
            )
        first_page = await client.get(
            "/api/v1/audit-logs",
            params={"action": "login.success", "limit": 2, "offset": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        second_page = await client.get(
            "/api/v1/audit-logs",
            params={"action": "login.success", "limit": 2, "offset": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
        first_ids = {e["id"] for e in first_page.json()["entries"]}
        second_ids = {e["id"] for e in second_page.json()["entries"]}
        assert first_ids.isdisjoint(second_ids)
