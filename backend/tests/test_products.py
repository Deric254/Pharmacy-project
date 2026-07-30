"""
Product/batch API tests. FEFO selection logic itself is tested
separately and more rigorously in test_fefo.py -- this file covers
the CRUD/RBAC surface.
"""

import asyncio


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


class TestProductExport:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_excel_export_returns_a_real_spreadsheet(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            "/api/v1/products",
            json={"name": "Exportable Product", "default_selling_price": 5.0},
            headers=headers,
        )

        r = await client.get("/api/v1/products?export=excel", headers=headers)
        assert r.status_code == 200
        assert (
            r.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(r.content) > 0

        # A genuine .xlsx file is a real zip archive -- confirms this
        # is an actual spreadsheet, not just bytes with the right
        # content-type header slapped on.
        import io
        import zipfile

        assert zipfile.is_zipfile(io.BytesIO(r.content))

    async def test_json_export_is_still_the_default(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")


class TestDuplicatePrevention:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_duplicate_name_rejected_case_insensitively(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r1 = await client.post(
            "/api/v1/products", json={"name": "Paracetamol 500mg"}, headers=headers
        )
        assert r1.status_code == 201

        r2 = await client.post(
            "/api/v1/products", json={"name": "paracetamol 500MG"}, headers=headers
        )
        assert r2.status_code == 409

    async def test_deactivating_frees_the_name_for_reuse(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r1 = await client.post("/api/v1/products", json={"name": "Old Formula"}, headers=headers)
        product_id = r1.json()["id"]

        await client.delete(f"/api/v1/products/{product_id}", headers=headers)

        r2 = await client.post("/api/v1/products", json={"name": "Old Formula"}, headers=headers)
        assert r2.status_code == 201

    async def test_rename_to_an_existing_active_name_is_rejected(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/products", json={"name": "Amoxicillin"}, headers=headers)
        r2 = await client.post("/api/v1/products", json={"name": "Ibuprofen"}, headers=headers)
        other_id = r2.json()["id"]

        r = await client.patch(
            f"/api/v1/products/{other_id}", json={"name": "Amoxicillin"}, headers=headers
        )
        assert r.status_code == 409

    async def test_two_concurrent_creates_with_the_same_name_only_one_succeeds(
        self, client, owner_user
    ):
        """
        The actual gap this closes: nothing stopped the same drug
        being entered twice, silently splitting its real stock across
        two "different" catalog entries. The pre-check alone can still
        race under real concurrency -- the database constraint is what
        makes this genuinely, not just usually, safe.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async def create():
            return await client.post(
                "/api/v1/products", json={"name": "Concurrent Drug Entry"}, headers=headers
            )

        results = await asyncio.gather(create(), create(), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(201) == 1
        assert status_codes.count(409) == 1

    async def test_deactivating_frees_the_barcode_for_reuse(self, client, owner_user):
        """
        The related bug found alongside name uniqueness: barcode's old
        constraint was a plain column-level unique with no exception
        for soft-deleted rows, so once a product was deactivated, its
        barcode could never be reused by a genuinely new product ever
        again.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r1 = await client.post(
            "/api/v1/products",
            json={"name": "Discontinued Item", "barcode": "REUSE-ME-123"},
            headers=headers,
        )
        product_id = r1.json()["id"]

        await client.delete(f"/api/v1/products/{product_id}", headers=headers)

        r2 = await client.post(
            "/api/v1/products",
            json={"name": "Replacement Item", "barcode": "REUSE-ME-123"},
            headers=headers,
        )
        assert r2.status_code == 201

    async def test_two_concurrent_creates_with_the_same_barcode_only_one_succeeds(
        self, client, owner_user
    ):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async def create(name: str):
            return await client.post(
                "/api/v1/products",
                json={"name": name, "barcode": "RACE-BARCODE-1"},
                headers=headers,
            )

        results = await asyncio.gather(create("Racer A"), create("Racer B"), return_exceptions=True)
        status_codes = [r.status_code for r in results if not isinstance(r, Exception)]
        assert status_codes.count(201) == 1
        assert status_codes.count(409) == 1


class TestMarginAndMarkup:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_no_stock_means_no_margin_fields_ever_fabricated(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            "/api/v1/products",
            json={"name": "No Stock Yet", "default_selling_price": 20.0},
            headers=headers,
        )
        body = r.json()
        assert body["current_cost"] is None
        assert body["margin_amount"] is None
        assert body["margin_percent"] is None
        assert body["markup_percent"] is None

    async def test_margin_and_markup_computed_correctly(self, client, owner_user):
        """
        Selling 20.0, cost 12.0: margin (profit as % of selling
        price) and markup (profit as % of cost) are genuinely
        different numbers -- proving both are computed distinctly,
        not one value duplicated under two names.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        create = await client.post(
            "/api/v1/products",
            json={"name": "Margin Test Product", "default_selling_price": 20.0},
            headers=headers,
        )
        product_id = create.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "M1",
                "expiry_date": "2027-01-01",
                "qty_received": 10,
                "cost_price": 12.0,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        body = r.json()
        assert body["current_cost"] == 12.0
        assert body["margin_amount"] == 8.0  # 20 - 12
        assert round(body["margin_percent"], 2) == 40.0  # 8/20
        assert round(body["markup_percent"], 2) == round(8 / 12 * 100, 2)  # ~66.67%
        # The two must genuinely differ -- confirms they're not the
        # same number duplicated under two field names.
        assert body["margin_percent"] != body["markup_percent"]

    async def test_margin_reflects_the_fefo_next_batch_not_an_average(self, client, owner_user):
        """
        Two batches, different costs and expiries: margin must be
        computed from whichever batch would ACTUALLY be sold next
        (earliest expiry), matching real FEFO sale behavior -- not an
        average across all batches, which would misrepresent what the
        next real sale actually earns.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        create = await client.post(
            "/api/v1/products",
            json={"name": "FEFO Margin Product", "default_selling_price": 20.0},
            headers=headers,
        )
        product_id = create.json()["id"]
        # Later-expiring batch, cheaper -- must NOT be what margin uses.
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "LATER",
                "expiry_date": "2028-01-01",
                "qty_received": 10,
                "cost_price": 5.0,
            },
            headers=headers,
        )
        # Earlier-expiring batch -- this is the one FEFO would actually sell next.
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "SOONER",
                "expiry_date": "2027-01-01",
                "qty_received": 10,
                "cost_price": 15.0,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert r.json()["current_cost"] == 15.0  # the sooner-expiring batch's cost, not 5.0

    async def test_margin_appears_in_the_list_endpoint_too(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        create = await client.post(
            "/api/v1/products",
            json={"name": "List Margin Product", "default_selling_price": 20.0},
            headers=headers,
        )
        product_id = create.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "L1",
                "expiry_date": "2027-01-01",
                "qty_received": 10,
                "cost_price": 12.0,
            },
            headers=headers,
        )

        r = await client.get("/api/v1/products", headers=headers)
        product = next(p for p in r.json() if p["id"] == product_id)
        assert product["current_cost"] == 12.0
        assert product["margin_amount"] == 8.0


class TestNameCannotBeJustWhitespace:
    """
    A real gap an adversarial chaos test found: a name of pure spaces
    passed validation, since min_length only checks raw string length,
    not content. Applies to both create and update.
    """

    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_whitespace_only_name_rejected_on_create(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/products",
            json={"name": "     "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    async def test_real_name_gets_surrounding_whitespace_stripped(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        r = await client.post(
            "/api/v1/products",
            json={"name": "  Padded Name  "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["name"] == "Padded Name"
