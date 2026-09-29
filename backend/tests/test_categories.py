async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestCategoryList:
    async def test_requires_login(self, client):
        r = await client.get("/api/v1/categories")
        assert r.status_code == 401

    async def test_empty_list_when_none_created(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/categories", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == []

    async def test_lists_alphabetically(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/categories", json={"name": "Painkillers"}, headers=headers)
        await client.post("/api/v1/categories", json={"name": "Antibiotics"}, headers=headers)

        r = await client.get("/api/v1/categories", headers=headers)
        assert [c["name"] for c in r.json()] == ["Antibiotics", "Painkillers"]


class TestCategoryCreate:
    async def test_requires_products_manage_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/categories",
            json={"name": "Should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_create_returns_the_new_category(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/categories",
            json={"name": "Antibiotics"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["name"] == "Antibiotics"
        assert isinstance(r.json()["id"], int)

    async def test_duplicate_name_rejected_case_insensitively(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/categories", json={"name": "Antibiotics"}, headers=headers)

        r = await client.post("/api/v1/categories", json={"name": "antibiotics"}, headers=headers)
        assert r.status_code == 409

    async def test_blank_name_rejected(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/categories",
            json={"name": "   "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422


class TestCategoryCreateRace:
    """
    Two creates of the same brand-new name can both pass the duplicate
    pre-check before either commits; the database's UNIQUE constraint is
    the real arbiter. The loser must get a clean 409 and leave nothing
    behind -- never a 500, never a half-written row.
    """

    async def test_a_lost_race_is_a_409_and_persists_nothing(self, client, owner_user, monkeypatch):
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.models.category import Category

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        real_flush = AsyncSession.flush

        async def flush_that_loses_the_race(self, *args, **kwargs):
            if any(isinstance(obj, Category) for obj in self.new):
                raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
            return await real_flush(self, *args, **kwargs)

        with monkeypatch.context() as patched:
            patched.setattr(AsyncSession, "flush", flush_that_loses_the_race)
            r = await client.post("/api/v1/categories", json={"name": "Raced"}, headers=headers)

        assert r.status_code == 409
        listed = await client.get("/api/v1/categories", headers=headers)
        assert listed.json() == []
