"""
Users API tests. This module didn't exist before -- there was no way
to create any user via the API at all, only reset an existing user's
password. These tests cover the gap that closed.
"""


class TestCreateUser:
    async def test_requires_users_manage_permission(self, client, employee_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "pass1234"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/users",
            json={
                "full_name": "New Hire",
                "username": "newhire",
                "password": "SafePass123",
                "role_id": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_owner_can_create_a_new_employee(self, client, owner_user, seeded_roles):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/users",
            json={
                "full_name": "New Cashier",
                "username": "newcashier",
                "password": "SafePass123",
                "role_id": seeded_roles["Employee"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["username"] == "newcashier"
        assert body["role_name"] == "Employee"
        assert body["is_active"] is True

        # And the new account can actually log in -- proves the
        # password was hashed and stored correctly, not just that a
        # row exists.
        new_login = await client.post(
            "/api/v1/auth/login", json={"username": "newcashier", "password": "SafePass123"}
        )
        assert new_login.status_code == 200

    async def test_duplicate_username_rejected(self, client, owner_user, seeded_roles):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/users",
            json={
                "full_name": "Someone Else",
                "username": "lucy",
                "password": "SafePass123",
                "role_id": seeded_roles["Employee"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    async def test_unknown_role_id_rejected(self, client, owner_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.post(
            "/api/v1/users",
            json={
                "full_name": "Someone",
                "username": "someone",
                "password": "SafePass123",
                "role_id": 999999,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400


class TestListUsersAndRoles:
    async def test_list_users_requires_permission(self, client, employee_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "pass1234"}
        )
        token = login.json()["access_token"]

        r = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_owner_can_list_users(self, client, owner_user, employee_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        usernames = {u["username"] for u in r.json()}
        assert {"lucy", "joe"} <= usernames

    async def test_list_roles(self, client, owner_user, seeded_roles):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.get("/api/v1/users/roles", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        names = {role["name"] for role in r.json()}
        assert names == {"Employee", "Administrator", "ChemistOwner"}


class TestDeactivateUser:
    async def test_owner_can_deactivate_another_user(self, client, owner_user, employee_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.delete(
            f"/api/v1/users/{employee_user.id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 204

        # Deactivated user can no longer log in.
        blocked_login = await client.post(
            "/api/v1/auth/login", json={"username": "joe", "password": "pass1234"}
        )
        assert blocked_login.status_code == 401

    async def test_cannot_deactivate_your_own_account(self, client, owner_user):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
        )
        token = login.json()["access_token"]

        r = await client.delete(
            f"/api/v1/users/{owner_user.id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 400
