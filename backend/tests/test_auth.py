"""
Auth + RBAC tests. This suite is what would have caught the
selectinload string-vs-class-bound-attribute bug found during manual
smoke testing — that's exactly why it's captured here permanently
instead of only being verified once by hand.
"""

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.user import User


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


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

    async def test_me_exposes_real_permission_codes_not_just_role_name(self, client, owner_user):
        # The frontend must gate UI on actual permission codes (data),
        # never on `if role_name == "ChemistOwner"` (a hardcoded string
        # that silently drifts from whatever the DB actually grants).
        # This is the contract that makes that possible.
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert "permissions" in body
        assert isinstance(body["permissions"], list)
        assert len(body["permissions"]) > 0
        assert all(isinstance(code, str) for code in body["permissions"])


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


class TestRefreshTokenRotation:
    async def test_login_sets_httponly_refresh_cookie_not_in_body(self, client, owner_user):
        r = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        assert r.status_code == 200
        assert "refresh_token" not in r.json()  # never handed to JS-readable body
        assert "refresh_token" in r.cookies
        set_cookie_header = r.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie_header

    async def test_refresh_with_no_cookie_is_rejected(self, client, owner_user):
        r = await client.post("/api/v1/auth/refresh")
        assert r.status_code == 401

    async def test_refresh_issues_a_new_working_access_token(self, client, owner_user):
        await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )

        r = await client.post("/api/v1/auth/refresh")
        assert r.status_code == 200
        new_access_token = r.json()["access_token"]

        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"}
        )
        assert me.status_code == 200
        assert me.json()["username"] == "lucy"

    async def test_refresh_rotates_the_cookie_so_the_old_token_is_now_dead(
        self, client, owner_user
    ):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        old_refresh_token = login.cookies["refresh_token"]

        first_refresh = await client.post("/api/v1/auth/refresh")
        assert first_refresh.status_code == 200

        # Replay the OLD refresh token (as if it had been stolen and the
        # legitimate client had already rotated past it) -- must fail.
        replay = await client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": old_refresh_token}
        )
        assert replay.status_code == 401

    async def test_reused_refresh_token_revokes_all_sessions_for_that_user(
        self, client, owner_user
    ):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        old_refresh_token = login.cookies["refresh_token"]

        # Legitimate rotation.
        await client.post("/api/v1/auth/refresh")
        # Attacker replays the stolen pre-rotation token.
        await client.post("/api/v1/auth/refresh", cookies={"refresh_token": old_refresh_token})

        # The legitimate client's rotated (and otherwise still-valid)
        # refresh token must now ALSO be dead -- reuse detection nukes
        # the whole session family, not just the replayed token.
        legit_but_now_revoked = await client.post("/api/v1/auth/refresh")
        assert legit_but_now_revoked.status_code == 401

    async def test_logout_revokes_the_session_and_clears_the_cookie(self, client, owner_user):
        await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204

        r = await client.post("/api/v1/auth/refresh")
        assert r.status_code == 401

    async def test_logout_with_no_cookie_still_succeeds(self, client, owner_user):
        r = await client.post("/api/v1/auth/logout")
        assert r.status_code == 204

    async def test_refresh_with_garbage_cookie_is_rejected_not_500(self, client, owner_user):
        r = await client.post("/api/v1/auth/refresh", cookies={"refresh_token": "not-a-real-jwt"})
        assert r.status_code == 401

    async def test_refresh_with_an_access_token_in_the_cookie_is_rejected(self, client, owner_user):
        # Someone (or a bug) puts an access token where a refresh token
        # belongs -- must be rejected, not silently accepted as if it
        # were a valid refresh token just because it's a well-formed JWT.
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        access_token = login.json()["access_token"]

        r = await client.post("/api/v1/auth/refresh", cookies={"refresh_token": access_token})
        assert r.status_code == 401

    async def test_logout_with_garbage_cookie_still_succeeds(self, client, owner_user):
        r = await client.post("/api/v1/auth/logout", cookies={"refresh_token": "not-a-real-jwt"})
        assert r.status_code == 204


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

    async def test_security_question_lookup_returns_the_real_question(self, client, seeded_roles):
        async with AsyncSessionLocal() as db:
            u = User(
                full_name="Has A Question",
                username="hasq",
                hashed_password=hash_password("pass12345"),
                role_id=seeded_roles["Employee"],
                security_question="What was your first pet's name?",
                security_answer_hash=hash_password("Rex"),
            )
            db.add(u)
            await db.commit()

        r = await client.get("/api/v1/auth/security-question", params={"username": "hasq"})
        assert r.status_code == 200
        assert r.json()["question"] == "What was your first pet's name?"

    async def test_security_question_lookup_never_reveals_unknown_username(self, client):
        # A nonexistent username must return the exact same generic
        # response as an account with no question set -- the whole
        # point is not letting this endpoint be used to enumerate
        # which usernames are real.
        r = await client.get(
            "/api/v1/auth/security-question", params={"username": "definitely-not-a-real-user"}
        )
        assert r.status_code == 200
        assert r.json()["question"] == "Security question"

    async def test_security_question_lookup_never_reveals_missing_question(
        self, client, owner_user
    ):
        # owner_user has no security question set -- same generic
        # response as a nonexistent username, not a different one.
        r = await client.get("/api/v1/auth/security-question", params={"username": "lucy"})
        assert r.status_code == 200
        assert r.json()["question"] == "Security question"

    async def test_full_forgot_password_cycle_with_a_real_question(self, client, seeded_roles):
        async with AsyncSessionLocal() as db:
            u = User(
                full_name="Has A Question",
                username="hasq",
                hashed_password=hash_password("oldpass123"),
                role_id=seeded_roles["Employee"],
                security_question="What was your first pet's name?",
                security_answer_hash=hash_password("Rex"),
            )
            db.add(u)
            await db.commit()

        question = await client.get("/api/v1/auth/security-question", params={"username": "hasq"})
        assert question.json()["question"] == "What was your first pet's name?"

        reset = await client.post(
            "/api/v1/auth/forgot-password",
            json={"username": "hasq", "security_answer": "Rex", "new_password": "brandNewPass1"},
        )
        assert reset.status_code == 204

        login = await client.post(
            "/api/v1/auth/login", json={"username": "hasq", "password": "brandNewPass1"}
        )
        assert login.status_code == 200


class TestHierarchicalPasswordReset:
    """
    The actual gap this closes, confirmed before the fix existed: any
    users.manage holder (Administrator included) could reset ANY
    user's password, including the Owner's, with the admin directly
    choosing -- and therefore knowing -- that person's real password.
    """

    async def test_owner_can_reset_administrator(self, client, owner_user, administrator_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": administrator_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert len(r.json()["temp_password"]) > 0

    async def test_owner_can_reset_employee(self, client, owner_user, employee_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": employee_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    async def test_administrator_can_reset_employee(
        self, client, administrator_user, employee_user
    ):
        token = await _login(client, "sam", "AdminPass1")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": employee_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    async def test_administrator_cannot_reset_owner(self, client, administrator_user, owner_user):
        """The exact gap: an Administrator resetting the Owner's password."""
        token = await _login(client, "sam", "AdminPass1")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": owner_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_administrator_cannot_reset_another_administrator(
        self, client, administrator_user, seeded_roles
    ):
        async with AsyncSessionLocal() as db:
            other_admin = User(
                full_name="Other Admin",
                username="pat",
                hashed_password=hash_password("AdminPass2"),
                role_id=seeded_roles["Administrator"],
            )
            db.add(other_admin)
            await db.commit()
            await db.refresh(other_admin)

        token = await _login(client, "sam", "AdminPass1")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": other_admin.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_the_generated_temp_password_actually_works_for_login(
        self, client, owner_user, employee_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        reset = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": employee_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        temp_password = reset.json()["temp_password"]

        login = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": temp_password}
        )
        assert login.status_code == 200

    async def test_admin_reset_sets_must_change_password(self, client, owner_user, employee_user):
        token = await _login(client, "lucy", "S3curePass!")
        reset = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": employee_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        temp_password = reset.json()["temp_password"]

        joe_token = await _login(client, "joe", temp_password)
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {joe_token}"})
        assert me.json()["must_change_password"] is True

    async def test_changing_password_clears_must_change_password(
        self, client, owner_user, employee_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        reset = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": employee_user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        temp_password = reset.json()["temp_password"]
        joe_token = await _login(client, "joe", temp_password)

        change = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": temp_password, "new_password": "joesRealPassword1"},
            headers={"Authorization": f"Bearer {joe_token}"},
        )
        assert change.status_code == 204

        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {joe_token}"})
        assert me.json()["must_change_password"] is False

        # And the new real password actually works, replacing the temp one.
        relogin = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "joesRealPassword1"}
        )
        assert relogin.status_code == 200

    async def test_change_password_requires_the_correct_current_password(
        self, client, employee_user
    ):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "newRealPassword1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    async def test_admin_reset_is_rejected_for_nonexistent_user(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/auth/admin-reset-password",
            json={"user_id": 999999},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


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
