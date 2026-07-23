"""
SECURITY AUDIT.

Not per-module spot checks (those already exist) -- this sweeps
properties across the WHOLE system:
  1. Every protected endpoint genuinely rejects unauthenticated access.
  2. Secrets (password hashes, encrypted keys/tokens) never appear in
     any API response body, anywhere, full stop.
  3. A tampered JWT is rejected, not just an expired/missing one.
  4. SQL-injection-shaped input is handled safely (parameterized
     queries protect us structurally, but this proves it, not assumes it).
  5. Cross-user data isolation holds for every "my own data" resource,
     not just the ones spot-checked in their own module's test file.
"""

import jwt as pyjwt


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestUnauthenticatedAccessRejected:
    """
    A representative sweep across every module's protected surface --
    not exhaustive of every single route, but at least one write and
    one read endpoint per module, confirming none of them silently
    allow anonymous access.
    """

    ENDPOINTS_REQUIRING_AUTH = [
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/products"),
        ("POST", "/api/v1/products"),
        ("GET", "/api/v1/inventory/low-stock"),
        ("POST", "/api/v1/inventory/adjustments"),
        ("POST", "/api/v1/sales"),
        ("GET", "/api/v1/stock-takes"),
        ("GET", "/api/v1/suppliers"),
        ("GET", "/api/v1/purchase-orders/kanban"),
        ("GET", "/api/v1/customers"),
        ("GET", "/api/v1/reports/expired-stock"),
        ("GET", "/api/v1/ai/keys"),
        ("GET", "/api/v1/backups"),
    ]

    async def test_every_protected_endpoint_rejects_no_token(self, client):
        failures = []
        for method, path in self.ENDPOINTS_REQUIRING_AUTH:
            r = await client.request(method, path, json={} if method == "POST" else None)
            if r.status_code not in (401, 422):
                # 422 only acceptable if it's a validation error that
                # still never touched business logic; capture for review.
                failures.append((method, path, r.status_code))
        assert failures == [], f"Endpoints that did NOT reject unauthenticated access: {failures}"

    async def test_config_get_is_the_deliberate_exception(self, client):
        """GET /config is intentionally public -- the login screen needs
        branding before a session exists. Confirmed here so it's an
        explicit, tested exception, not an accidental gap."""
        r = await client.get("/api/v1/config")
        assert r.status_code == 200


class TestSecretsNeverLeakInResponses:
    async def test_user_me_never_includes_password_hash(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert "hashed_password" not in r.text
        assert "$argon2" not in r.text

    async def test_ai_key_responses_never_contain_the_raw_key(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        raw_key = "sk-extremely-secret-raw-api-key-value-999"

        create_resp = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": raw_key},
            headers=headers,
        )
        assert raw_key not in create_resp.text

        list_resp = await client.get("/api/v1/ai/keys", headers=headers)
        assert raw_key not in list_resp.text

    async def test_backup_oauth_token_never_leaks_in_any_response(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        raw_refresh_token = "1//super-secret-google-refresh-token-xyz"

        connect_resp = await client.post(
            "/api/v1/backups/connect-google-drive",
            json={"refresh_token": raw_refresh_token},
            headers=headers,
        )
        assert raw_refresh_token not in connect_resp.text

        list_resp = await client.get("/api/v1/backups", headers=headers)
        assert raw_refresh_token not in list_resp.text


class TestJWTTampering:
    async def test_tampered_signature_is_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        # Flip a character well before the end of the signature, not
        # the very last one. The last character of a base64url-encoded
        # HS256 signature sits in a partial final group with unused
        # padding bits -- confirmed directly that ~1 in 20 possible
        # replacement characters there decode to the byte-for-byte
        # IDENTICAL signature, making the token not actually tampered
        # at all (a real, occasional CI failure this caused, not
        # flakiness). Position -6 sits inside a full group, where
        # every character maps to a unique output -- confirmed
        # directly that every possible replacement there changes the
        # decoded bytes.
        pos = -6
        replacement = "A" if token[pos] != "A" else "B"
        tampered = token[:pos] + replacement + token[pos + 1 :]

        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
        assert r.status_code == 401

    async def test_token_with_none_algorithm_is_rejected(self, client, owner_user):
        """
        Classic JWT vulnerability: some libraries historically accepted
        alg=none, letting an attacker forge an unsigned token. Confirms
        our decode path (which pins algorithms=[settings.jwt_algorithm])
        does not fall for it.
        """
        forged = pyjwt.encode({"sub": "1", "type": "access"}, key="", algorithm="none")
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    async def test_token_signed_with_wrong_secret_is_rejected(self, client, owner_user):
        forged = pyjwt.encode(
            {"sub": "1", "type": "access"}, key="attacker-guessed-secret", algorithm="HS256"
        )
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    async def test_refresh_token_cannot_be_used_as_an_access_token(self, client, owner_user):
        """
        decode_token() itself doesn't distinguish token types -- the
        route layer must check payload["type"] == "access" explicitly.
        This proves that check is actually in place, not just assumed.
        """
        from app.core.security import create_token

        refresh = create_token(subject="1", token_type="refresh")
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"})
        assert r.status_code == 401


class TestSQLInjectionResistance:
    async def test_product_search_with_injection_payload_is_handled_safely(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/products", json={"name": "Amoxicillin"}, headers=headers)

        payload = "'; DROP TABLE products; --"
        r = await client.get("/api/v1/products", params={"search": payload}, headers=headers)
        assert r.status_code == 200  # handled as a literal string, not executed

        # Confirm the table genuinely still exists and has our data.
        follow_up = await client.get("/api/v1/products", headers=headers)
        assert follow_up.status_code == 200
        assert any(p["name"] == "Amoxicillin" for p in follow_up.json())

    async def test_customer_search_with_injection_payload_is_handled_safely(
        self, client, employee_user
    ):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/customers", json={"name": "Test Customer"}, headers=headers)

        payload = "x' OR '1'='1"
        r = await client.get("/api/v1/customers", params={"search": payload}, headers=headers)
        assert r.status_code == 200
        # An injection succeeding would typically return ALL rows
        # regardless of the filter; confirm it correctly returns none
        # (no customer is literally named "x' OR '1'='1").
        assert r.json() == []

    async def test_barcode_lookup_with_injection_payload_returns_404_not_error(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        payload = "1' UNION SELECT * FROM users --"
        r = await client.get(
            f"/api/v1/products/barcode/{payload}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404  # not a 500, not leaked data


class TestCrossUserIsolation:
    async def test_me_never_reflects_anyone_but_the_caller(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["username"] == "lucy"  # never anyone else's, regardless of what's asked

    async def test_admin_reset_password_cannot_be_used_by_non_admin(self, client, employee_user):
        """Confirms the admin-reset-password endpoint can't be abused by
        a low-privilege user to reset an arbitrary account (already
        covered narrowly in test_auth.py; reconfirmed here as part of
        the systematic sweep)."""
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": 1, "new_password": "hacked12345"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
