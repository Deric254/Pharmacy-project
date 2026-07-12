"""
Product/batch API tests. FEFO selection logic itself is tested
separately and more rigorously in test_fefo.py -- this file covers
the CRUD/RBAC surface.
"""


class TestProductCRUD:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_create_requires_permission(self, client, employee_user):
        token = await self._login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/products",
            json={"name": "Amoxicillin 500mg"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_create_and_get_product(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/products",
            json={"name": "Amoxicillin 500mg", "barcode": "AMX500", "reorder_point": 20},
            headers=headers,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Amoxicillin 500mg"
        assert body["total_qty_available"] == 0  # no batches yet

        r2 = await client.get(f"/api/v1/products/{body['id']}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["barcode"] == "AMX500"

    async def test_duplicate_barcode_rejected(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/products", json={"name": "Product A", "barcode": "DUPE123"}, headers=headers
        )
        r = await client.post(
            "/api/v1/products", json={"name": "Product B", "barcode": "DUPE123"}, headers=headers
        )
        assert r.status_code == 409

    async def test_barcode_lookup(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/products", json={"name": "Paracetamol", "barcode": "PARA001"}, headers=headers
        )
        r = await client.get("/api/v1/products/barcode/PARA001", headers=headers)
        assert r.status_code == 200
        assert r.json()["name"] == "Paracetamol"

    async def test_barcode_lookup_unknown_returns_404(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/products/barcode/DOES-NOT-EXIST",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


class TestBatchCreation:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        return str(r.json()["access_token"])

    async def _make_product(self, client, headers) -> int:
        r = await client.post("/api/v1/products", json={"name": "Ibuprofen"}, headers=headers)
        return int(r.json()["id"])

    async def test_create_batch_updates_product_total(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await self._make_product(client, headers)

        r = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "IBU-001",
                "expiry_date": "2027-01-01",
                "qty_received": 100,
                "cost_price": 5.0,
            },
            headers=headers,
        )
        assert r.status_code == 201
        assert r.json()["qty_remaining"] == 100

        product_check = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert product_check.json()["total_qty_available"] == 100

    async def test_two_batches_same_product_never_merge(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id = await self._make_product(client, headers)

        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "IBU-001",
                "expiry_date": "2027-01-01",
                "qty_received": 50,
                "cost_price": 5.0,
            },
            headers=headers,
        )
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "IBU-002",
                "expiry_date": "2027-06-01",
                "qty_received": 30,
                "cost_price": 5.5,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        batches = r.json()
        assert len(batches) == 2  # two distinct rows, not merged into one
        assert {b["batch_number"] for b in batches} == {"IBU-001", "IBU-002"}

    async def test_create_batch_requires_permission(self, client, employee_user, owner_user):
        owner_token = await self._login(client, "lucy", "S3curePass!")
        product_id = await self._make_product(client, {"Authorization": f"Bearer {owner_token}"})

        employee_token = await self._login(client, "joe", "pass1234")
        r = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "X",
                "expiry_date": "2027-01-01",
                "qty_received": 10,
                "cost_price": 1.0,
            },
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert r.status_code == 403
