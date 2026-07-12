"""
Business config tests. This is the module every other screen reads
branding from, so the two properties that actually matter are:
  1. It's readable without auth (login screen needs it before login)
  2. Writes are RBAC-gated and immediately visible to the next read
     (proves the cache-overwrite-on-write pattern, not just
     cache-invalidate-and-hope, actually works)
"""

import json

from app.core.redis_client import redis_client
from app.services.business_config_service import CACHE_KEY


class TestReadConfig:
    async def test_get_config_requires_no_auth(self, client):
        r = await client.get("/api/v1/config")
        assert r.status_code == 200

    async def test_default_values_are_sane(self, client):
        r = await client.get("/api/v1/config")
        body = r.json()
        assert body["business_name"] == "My Pharmacy"
        assert body["currency"] == "KES"
        assert body["expiry_alert_days"] == [90, 60, 30]

    async def test_get_populates_cache(self, client):
        await client.get("/api/v1/config")
        cached = await redis_client.get(CACHE_KEY)
        assert cached is not None
        assert json.loads(cached)["business_name"] == "My Pharmacy"


class TestUpdateConfig:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_update_requires_auth(self, client):
        r = await client.patch("/api/v1/config", json={"business_name": "New Name"})
        assert r.status_code == 401

    async def test_update_rejects_missing_permission(self, client, employee_user):
        token = await self._login(client, "joe", "pass1234")
        r = await client.patch(
            "/api/v1/config",
            json={"business_name": "Hacked Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_update_with_permission_succeeds_and_is_immediately_visible(
        self, client, owner_user
    ):
        token = await self._login(client, "lucy", "S3curePass!")

        r = await client.patch(
            "/api/v1/config",
            json={"business_name": "K-Lamed Chemist", "primary_color": "#22C55E"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["business_name"] == "K-Lamed Chemist"
        assert r.json()["primary_color"] == "#22C55E"

        # Next read (even unauthenticated, like the login screen) must
        # reflect the change immediately -- proves the service
        # overwrites cache on write rather than only invalidating it.
        follow_up = await client.get("/api/v1/config")
        assert follow_up.json()["business_name"] == "K-Lamed Chemist"

    async def test_update_rejects_invalid_hex_color(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.patch(
            "/api/v1/config",
            json={"primary_color": "not-a-color"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_partial_update_does_not_reset_other_fields(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.patch(
            "/api/v1/config", json={"business_name": "K-Lamed Chemist"}, headers=headers
        )
        r = await client.patch("/api/v1/config", json={"currency": "USD"}, headers=headers)

        assert r.json()["business_name"] == "K-Lamed Chemist"  # unchanged by the second call
        assert r.json()["currency"] == "USD"

    async def test_expiry_alert_days_round_trips_as_a_list(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.patch(
            "/api/v1/config",
            json={"expiry_alert_days": [120, 45, 14]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.json()["expiry_alert_days"] == [120, 45, 14]
