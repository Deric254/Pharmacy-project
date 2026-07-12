"""
Auth + RBAC tests. This suite is what would have caught the
selectinload string-vs-class-bound-attribute bug found during manual
smoke testing — that's exactly why it's captured here permanently
instead of only being verified once by hand.
"""


class TestLogin:
    async def test_wrong_password_returns_401(self, client, owner_user):
        r = await client.post("/api/v1/auth/login", json={"username": "lucy", "password": "wrong"})
        assert r.status_code == 401

    async def test_unknown_username_returns_401_not_404(self, client, owner_user):
        # Deliberately generic — never reveal whether a username exists.
        r = await client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
        assert r.status_code == 401

    async def test_correct_login_returns_token(self, client, owner_user):
        r = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20


class TestCurrentUser:
    async def test_me_requires_token(self, client, owner_user):
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 401

    async def test_me_returns_correct_role_for_seeded_client_user(self, client, owner_user):
        # This maps directly to the real client's handwritten answers:
        # Lucy is Administrator by job title but the ChemistOwner role
        # here represents "sees profit / approves orders" — role
        # assignment itself is a business decision, this test just
        # confirms whatever role IS assigned is what comes back.
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["role_name"] == "ChemistOwner"
        assert r.json()["username"] == "lucy"


class TestRBACEnforcement:
    async def test_permission_gated_route_rejects_missing_permission(self, client, employee_user):
        # An Employee (only has sales.create) must NOT be able to hit
        # an admin-only endpoint requiring users.manage.
        login = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "pass1234"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": 1, "new_password": "whatever123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_permission_gated_route_allows_correct_permission(self, client, owner_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        # Owner has users.manage -> passes the permission check.
        # Target user doesn't exist -> 404 from the service layer, not
        # 403 from RBAC. This distinction matters: it proves the
        # permission check and the business logic are separate layers.
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": 9999, "new_password": "whatever123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


class TestForgotPassword:
    async def test_forgot_password_without_security_answer_fails_generically(
        self, client, owner_user
    ):
        r = await client.post(
            "/api/v1/auth/forgot-password",
            json={"username": "lucy", "security_answer": "anything", "new_password": "newpass123"},
        )
        # owner_user fixture never set a security question, so this
        # must fail without revealing that fact directly.
        assert r.status_code == 400


class TestLoginRateLimiting:
    async def test_sixth_failed_attempt_is_rate_limited(self, client, owner_user):
        for _ in range(5):
            r = await client.post(
                "/api/v1/auth/login", json={"username": "lucy", "password": "wrong"}
            )
            assert r.status_code == 401

        r = await client.post("/api/v1/auth/login", json={"username": "lucy", "password": "wrong"})
        assert r.status_code == 429

        # Even the CORRECT password is blocked once rate-limited -- the
        # limit is per (ip, username) attempt count, not "wrong password
        # count", so it can't be bypassed by finally guessing right.
        r = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert r.status_code == 429

    async def test_successful_login_clears_prior_failed_attempts(self, client, owner_user):
        for _ in range(3):
            await client.post("/api/v1/auth/login", json={"username": "lucy", "password": "wrong"})

        good = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert good.status_code == 200

        # 3 more failures after a successful login should NOT trip the
        # limiter, since the successful login reset the counter.
        for _ in range(3):
            r = await client.post(
                "/api/v1/auth/login", json={"username": "lucy", "password": "wrong"}
            )
            assert r.status_code == 401  # not yet 429

    async def test_rate_limit_is_scoped_per_username_not_global(
        self, client, owner_user, employee_user
    ):
        for _ in range(5):
            await client.post("/api/v1/auth/login", json={"username": "lucy", "password": "wrong"})
        limited = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "wrong"}
        )
        assert limited.status_code == 429

        # A different username from the same "IP" (TestClient has no
        # real distinguishing IP, but the key includes username) must
        # not be blocked by lucy's failures.
        other = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "wrong"}
        )
        assert other.status_code == 401  # not 429
