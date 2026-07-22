"""
Setup flow tests. This is the one router in the whole app with no
auth dependency at all -- there's nobody to log in as until this
completes. Every test here is really testing one thing from different
angles: that this can bootstrap exactly one owner account, ever, and
nothing about how it's called can turn it into a standing backdoor.
"""

import asyncio


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
            json={"full_name": "Lucy Kangai", "username": "lucy", "password": "S3curePass!"},
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
            json={"full_name": "Lucy Kangai", "username": "lucy", "password": "S3curePass!"},
        )
        status = await client.get("/api/v1/setup/status")
        assert status.json()["needs_setup"] is False

    async def test_cannot_run_twice(self, client, seeded_roles):
        first = await client.post(
            "/api/v1/setup/first-user",
            json={"full_name": "Lucy Kangai", "username": "lucy", "password": "S3curePass!"},
        )
        assert first.status_code == 204

        second = await client.post(
            "/api/v1/setup/first-user",
            json={"full_name": "Someone Else", "username": "intruder", "password": "AnotherPass1"},
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
            json={"full_name": "Intruder", "username": "intruder", "password": "AnotherPass1"},
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
                json={"full_name": "A", "username": "usera", "password": "PasswordA1"},
            ),
            client.post(
                "/api/v1/setup/first-user",
                json={"full_name": "B", "username": "userb", "password": "PasswordB1"},
            ),
            return_exceptions=True,
        )
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(204) == 1

    async def test_password_too_short_rejected(self, client, seeded_roles):
        r = await client.post(
            "/api/v1/setup/first-user",
            json={"full_name": "Lucy", "username": "lucy", "password": "short"},
        )
        assert r.status_code == 422

    async def test_username_too_short_rejected(self, client, seeded_roles):
        r = await client.post(
            "/api/v1/setup/first-user",
            json={"full_name": "Lucy", "username": "ab", "password": "S3curePass!"},
        )
        assert r.status_code == 422

    async def test_requires_no_authentication(self, client, seeded_roles):
        # The whole point: reachable with zero Authorization header.
        r = await client.post(
            "/api/v1/setup/first-user",
            json={"full_name": "Lucy", "username": "lucy", "password": "S3curePass!"},
        )
        assert r.status_code == 204
