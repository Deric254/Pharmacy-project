"""
Role management tests. Before this module existed, roles were fixed
at install time -- no API could create a role, rename one, or change
what it grants. These tests cover the actual promise: a top-level
admin (ChemistOwner, via roles.manage) can define new responsibilities
without anything being hardcoded, while the 3 built-in roles stay
un-deletable so the system can never lock itself out of its own
access management.
"""


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestPermissionCatalog:
    async def test_requires_roles_manage_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get("/api/v1/permissions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_owner_can_list_the_full_permission_catalog(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/permissions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        codes = {p["code"] for p in r.json()}
        assert "sales.create" in codes
        assert "roles.manage" in codes


class TestCreateRole:
    async def test_administrator_cannot_manage_roles(self, client, administrator_user):
        """
        users.manage (which Administrator has) and roles.manage
        (which it does not) are deliberately separate -- being able to
        onboard a cashier must not also mean being able to redefine
        what every role in the system is allowed to do.
        """
        token = await _login(client, "sam", "AdminPass1")
        r = await client.post(
            "/api/v1/roles",
            json={"name": "Pharmacist", "permission_codes": ["sales.create"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_owner_can_create_a_brand_new_role(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/roles",
            json={
                "name": "Pharmacist",
                "description": "Dispenses controlled substances, no financial access",
                "permission_codes": ["sales.create", "inventory.view", "batches.create"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Pharmacist"
        assert body["is_system"] is False
        assert set(body["permissions"]) == {"sales.create", "inventory.view", "batches.create"}
        assert body["user_count"] == 0

    async def test_a_new_user_can_be_assigned_the_new_role_and_gets_exactly_its_permissions(
        self, client, owner_user
    ):
        token = await _login(client, "lucy", "S3curePass!")
        role = await client.post(
            "/api/v1/roles",
            json={"name": "Pharmacist", "permission_codes": ["sales.create", "inventory.view"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        role_id = role.json()["id"]

        created = await client.post(
            "/api/v1/users",
            json={
                "full_name": "New Pharmacist",
                "username": "newpharm",
                "password": "SafePass123",
                "role_id": role_id,
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201

        login = await client.post(
            "/api/v1/auth/login", json={"username": "newpharm", "password": "SafePass123"}
        )
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert set(me.json()["permissions"]) == {"sales.create", "inventory.view"}
        assert me.json()["role_name"] == "Pharmacist"

    async def test_duplicate_role_name_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/roles",
            json={"name": "Employee", "permission_codes": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    async def test_unknown_permission_code_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/roles",
            json={"name": "Ghost Role", "permission_codes": ["sales.teleport"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "sales.teleport" in r.json()["detail"]

    async def test_role_with_no_permissions_is_allowed(self, client, owner_user):
        """A role that grants nothing yet (about to be configured) is a
        valid intermediate state, not an error."""
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/roles",
            json={"name": "Trainee", "permission_codes": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["permissions"] == []


class TestUpdateRole:
    async def test_owner_can_edit_a_system_roles_permissions(
        self, client, owner_user, seeded_roles
    ):
        """
        The core promise: nothing is hardcoded. Administrator is a
        built-in role, but the owner can still take inventory.adjust
        away from it (or grant it something new) without deleting and
        recreating the role.
        """
        token = await _login(client, "lucy", "S3curePass!")
        admin_role_id = seeded_roles["Administrator"]

        before = await client.get(
            f"/api/v1/roles/{admin_role_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert "inventory.adjust" in before.json()["permissions"]

        r = await client.patch(
            f"/api/v1/roles/{admin_role_id}",
            json={"permission_codes": ["sales.create", "inventory.view"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert set(r.json()["permissions"]) == {"sales.create", "inventory.view"}
        assert r.json()["is_system"] is True  # editing permissions doesn't strip this

    async def test_permission_edit_takes_effect_for_an_already_logged_in_user_on_next_check(
        self, client, owner_user, administrator_user, seeded_roles
    ):
        """The permission change isn't just visible via the roles API --
        an existing Administrator's actual access changes too, proven by
        hitting a real gated endpoint before and after."""
        owner_token = await _login(client, "lucy", "S3curePass!")
        admin_token = await _login(client, "sam", "AdminPass1")
        admin_role_id = seeded_roles["Administrator"]

        before = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert before.status_code == 200  # Administrator starts with inventory.view

        await client.patch(
            f"/api/v1/roles/{admin_role_id}",
            json={"permission_codes": ["sales.create"]},  # inventory.view revoked
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        after = await client.get(
            "/api/v1/inventory/low-stock", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert after.status_code == 403

    async def test_can_rename_a_role(self, client, owner_user, seeded_roles):
        token = await _login(client, "lucy", "S3curePass!")
        admin_role_id = seeded_roles["Administrator"]
        r = await client.patch(
            f"/api/v1/roles/{admin_role_id}",
            json={"name": "Pharmacist-in-Charge"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Pharmacist-in-Charge"

    async def test_rename_to_an_existing_name_rejected(self, client, owner_user, seeded_roles):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.patch(
            f"/api/v1/roles/{seeded_roles['Administrator']}",
            json={"name": "Employee"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409


class TestDeleteRole:
    async def test_cannot_delete_a_system_role(self, client, owner_user, seeded_roles):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.delete(
            f"/api/v1/roles/{seeded_roles['Employee']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "built-in" in r.json()["detail"]

    async def test_cannot_delete_a_custom_role_still_in_use(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        role = await client.post(
            "/api/v1/roles",
            json={"name": "Pharmacist", "permission_codes": ["sales.create"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        role_id = role.json()["id"]
        created = await client.post(
            "/api/v1/users",
            json={
                "full_name": "P",
                "username": "pharm1",
                "password": "SafePass123",
                "role_id": role_id,
                "security_question": "Test question?",
                "security_answer": "Test answer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text

        r = await client.delete(
            f"/api/v1/roles/{role_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 400
        assert "1 user" in r.json()["detail"]

    async def test_can_delete_an_unused_custom_role(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        role = await client.post(
            "/api/v1/roles",
            json={"name": "Unused Role", "permission_codes": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        role_id = role.json()["id"]

        r = await client.delete(
            f"/api/v1/roles/{role_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 204

        get_after = await client.get(
            f"/api/v1/roles/{role_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert get_after.status_code == 404
