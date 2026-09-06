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

    async def test_editing_a_non_fefo_next_batch_price_is_invisible_at_product_level(
        self, client, owner_user
    ):
        """
        Real-world confusion this guards against: a product with
        several batches at different prices, where someone edits a
        batch's selling price expecting it to show at the register --
        but current_selling_price (and therefore the POS price and
        margin) only ever reflects the FEFO-next batch. The edit to a
        later-expiring batch genuinely saves (confirmed here), it just
        has zero visible effect anywhere else until the earlier batch
        sells out. This is the intended design (matches current_cost's
        FEFO-only behavior above) -- this test exists so a future
        change to that design is a deliberate choice, not an accident,
        and so the behavior is documented precisely enough that support
        can explain it instead of it looking like random breakage.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        create = await client.post(
            "/api/v1/products",
            json={"name": "FEFO Price Visibility Product", "default_selling_price": 20.0},
            headers=headers,
        )
        product_id = create.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "SOON",
                "expiry_date": "2027-01-01",
                "qty_received": 10,
                "cost_price": 10.0,
                "selling_price": 25.0,
            },
            headers=headers,
        )
        later = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "LATER",
                "expiry_date": "2028-01-01",
                "qty_received": 10,
                "cost_price": 10.0,
                "selling_price": 30.0,
            },
            headers=headers,
        )
        later_batch_id = later.json()["id"]

        before = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert before.json()["current_selling_price"] == 25.0  # the SOON batch, FEFO-next

        edit = await client.patch(
            f"/api/v1/products/{product_id}/batches/{later_batch_id}",
            json={"selling_price": 99.0},
            headers=headers,
        )
        assert edit.status_code == 200
        assert edit.json()["selling_price"] == 99.0  # the edit genuinely saved

        after = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        # Still 25.0, not 99.0 -- the SOON batch is still what's next.
        assert after.json()["current_selling_price"] == 25.0


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


class TestAvailableStockMeansSellable:
    """
    The exact real bug this closes: a product with only expired stock
    showed a nonzero "available" quantity, but every real sale attempt
    correctly failed against it (expired batches are excluded from
    FEFO) -- a genuine, confusing inconsistency between what the
    screen said and what actually worked. "Available" must always
    match what can actually be sold.
    """

    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def test_expired_only_stock_shows_as_zero_available_not_the_expired_count(
        self, client, owner_user
    ):
        from datetime import date, timedelta

        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        product = await client.post(
            "/api/v1/products",
            json={"name": "Expired Only Product", "default_selling_price": 20.0},
            headers=headers,
        )
        product_id = product.json()["id"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "EXP1",
                "expiry_date": yesterday,
                "qty_received": 7,
                "cost_price": 5.0,
            },
            headers=headers,
        )

        # The list view -- this is exactly what a cashier sees before
        # trying to sell.
        listed = await client.get("/api/v1/products", headers=headers)
        entry = next(p for p in listed.json() if p["id"] == product_id)
        assert entry["total_qty_available"] == 0

        # The single-product view must agree.
        single = await client.get(f"/api/v1/products/{product_id}", headers=headers)
        assert single.json()["total_qty_available"] == 0

        # And a real sale attempt must fail for the same reason the
        # displayed number is 0 -- not a mysterious "not in stock"
        # when the screen just said otherwise.
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers=headers,
        )
        assert sale.status_code == 409

    async def test_expired_only_stock_correctly_triggers_low_stock(self, client, owner_user):
        from datetime import date, timedelta

        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        product = await client.post(
            "/api/v1/products",
            json={"name": "Expired Low Stock Product", "reorder_point": 5},
            headers=headers,
        )
        product_id = product.json()["id"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "EXPLOW1",
                "expiry_date": yesterday,
                "qty_received": 50,  # well above reorder_point, but expired
                "cost_price": 5.0,
            },
            headers=headers,
        )

        low_stock = await client.get("/api/v1/inventory/low-stock", headers=headers)
        names = [p["name"] for p in low_stock.json()]
        assert "Expired Low Stock Product" in names


class TestProductListOrdering:
    async def test_products_ordered_by_stock_quantity_descending(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        async def make_with_stock(name: str, qty: int) -> int:
            r = await client.post("/api/v1/products", json={"name": name}, headers=headers)
            pid = r.json()["id"]
            if qty > 0:
                await client.post(
                    f"/api/v1/products/{pid}/batches",
                    json={
                        "batch_number": f"B-{name}",
                        "expiry_date": "2027-06-30",
                        "qty_received": qty,
                        "cost_price": 1.0,
                    },
                    headers=headers,
                )
            return pid

        await make_with_stock("Low Stock Order Test", 5)
        await make_with_stock("High Stock Order Test", 500)
        await make_with_stock("Mid Stock Order Test", 50)

        r = await client.get("/api/v1/products", headers=headers)
        names = [p["name"] for p in r.json()]
        high_idx = names.index("High Stock Order Test")
        mid_idx = names.index("Mid Stock Order Test")
        low_idx = names.index("Low Stock Order Test")
        assert high_idx < mid_idx < low_idx

    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])


class TestBatchCostCorrection:
    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def _make_product_with_batch(
        self, client, headers, cost_price: float = 10.0, batch_number: str = "CORR1"
    ) -> tuple[int, int]:
        product = await client.post(
            "/api/v1/products",
            json={"name": f"Cost Correction Product {batch_number}", "default_selling_price": 30.0},
            headers=headers,
        )
        product_id = product.json()["id"]
        batch = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": batch_number,
                "expiry_date": "2028-01-01",
                "qty_received": 10,
                "cost_price": cost_price,
                "selling_price": 30.0,
            },
            headers=headers,
        )
        return product_id, batch.json()["id"]

    async def test_correcting_cost_on_an_untouched_batch_succeeds(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers, cost_price=10.0)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 7.5, "reason": "mistyped cost on receiving"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["cost_price"] == 7.5

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert batches.json()[0]["cost_price"] == 7.5

    async def test_correcting_cost_after_a_sale_is_allowed(self, client, owner_user):
        """
        Correcting cost after a sale is now allowed unconditionally --
        see test_correction_never_changes_a_past_sales_recorded_profit
        for the guarantee that makes this safe: the sale's own profit
        is frozen at sale time (SaleItem.unit_cost) and does not move
        when the batch's live cost_price is corrected afterward.
        """
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers, cost_price=10.0)

        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )
        assert sale.status_code == 201, sale.text

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 1.0, "reason": "correcting after the sale"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["cost_price"] == 1.0

    async def test_correction_never_changes_a_past_sales_recorded_profit(self, client, owner_user):
        """
        The core guarantee, proven end to end rather than assumed: a
        sale's recorded cost/profit is frozen at the moment it
        happened (SaleItem.unit_cost) and must be completely immune to
        a batch cost correction made afterward, no matter how large
        the correction is. Only what's REMAINING -- valuation of
        unsold stock, and the cost basis of the NEXT sale -- may move.
        """
        from app.core.business_time import business_today
        from app.core.database import AsyncSessionLocal

        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers, cost_price=10.0)

        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )
        assert sale.status_code == 201, sale.text

        async with AsyncSessionLocal() as db:
            today = (await business_today(db)).isoformat()

        before = await client.get(
            "/api/v1/reports/profit",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert before.status_code == 200, before.text
        profit_before = before.json()["total_profit"]

        # A large, deliberately obvious correction -- if this leaked
        # into the already-recorded sale's profit at all, it would be
        # impossible to miss in the assertion below.
        correct = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 1.0, "reason": "large correction to prove isolation"},
            headers=headers,
        )
        assert correct.status_code == 200, correct.text

        after = await client.get(
            "/api/v1/reports/profit",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert after.status_code == 200, after.text
        assert after.json()["total_profit"] == profit_before

        # And the other half of the guarantee: a NEW sale from this
        # same batch's remaining stock DOES use the corrected cost --
        # the correction affects what's remaining, just never what's
        # already sold.
        second_sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )
        assert second_sale.status_code == 201, second_sale.text

        after_second_sale = await client.get(
            "/api/v1/reports/profit",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        # First sale: revenue 30, cost 10 -> profit 20.
        # Second sale: revenue 30, cost 1 (corrected) -> profit 29.
        assert after_second_sale.json()["total_profit"] == profit_before + 29.0

    async def test_employee_without_permission_is_forbidden(
        self, client, owner_user, employee_user
    ):
        owner_token = await self._login(client, "lucy", "S3curePass!")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        product_id, batch_id = await self._make_product_with_batch(client, owner_headers)

        employee_token = await self._login(client, "joe", "pass1234")
        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 1.0, "reason": "should not be allowed"},
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert r.status_code == 403

    async def test_a_blank_reason_is_rejected(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 5.0, "reason": ""},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_correction_is_audit_logged(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers, cost_price=10.0)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/cost",
            json={"cost_price": 6.5, "reason": "supplier invoice was actually 6.50 not 10.00"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        logs = await client.get(
            "/api/v1/audit-logs", params={"action": "batch.cost_corrected"}, headers=headers
        )
        assert logs.status_code == 200, logs.text
        entries = logs.json()["entries"]
        matching = [e for e in entries if e["entity_id"] == str(batch_id)]
        assert len(matching) == 1
        assert matching[0]["old_value"] == "10.00"
        assert "6.50" in matching[0]["new_value"]
        assert "supplier invoice was actually 6.50" in matching[0]["new_value"]


class TestBatchExpiryCorrection:
    """
    Mirrors TestBatchCostCorrection exactly, one test at a time, since
    correct_expiry_date is deliberately built the same way as
    correct_cost_price -- same shape of permission gate, same shape of
    audit trail, same "always allowed once granted, no business-rule
    blocking" philosophy. The one behavior worth testing that cost
    correction doesn't need: correcting an expired batch's date to the
    future makes it sellable again -- that's the exact risk migration
    0035's own docstring names as the reason this needed its own
    permission, so it's proven here rather than just asserted in a
    comment.
    """

    async def _login(self, client, username: str, password: str) -> str:
        r = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["access_token"])

    async def _make_product_with_batch(
        self,
        client,
        headers,
        expiry_date: str = "2028-01-01",
        batch_number: str = "EXP1",
        qty: int = 10,
    ) -> tuple[int, int]:
        product = await client.post(
            "/api/v1/products",
            json={
                "name": f"Expiry Correction Product {batch_number}",
                "default_selling_price": 30.0,
            },
            headers=headers,
        )
        product_id = product.json()["id"]
        batch = await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": batch_number,
                "expiry_date": expiry_date,
                "qty_received": qty,
                "cost_price": 10.0,
                "selling_price": 30.0,
            },
            headers=headers,
        )
        return product_id, batch.json()["id"]

    async def test_correcting_expiry_on_a_batch_succeeds(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/expiry",
            json={"new_expiry_date": "2029-06-30", "reason": "mistyped year on receiving"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["expiry_date"] == "2029-06-30"

        batches = await client.get(f"/api/v1/products/{product_id}/batches", headers=headers)
        assert batches.json()[0]["expiry_date"] == "2029-06-30"

    async def test_correcting_an_expired_batch_to_the_future_makes_it_sellable_again(
        self, client, owner_user
    ):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(
            client, headers, expiry_date="2020-01-01", qty=5
        )

        # Genuinely expired -- FEFO must refuse to sell it as-is.
        blocked = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )
        assert blocked.status_code == 409, blocked.text

        corrected = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/expiry",
            json={"new_expiry_date": "2029-01-01", "reason": "expiry was entered incorrectly"},
            headers=headers,
        )
        assert corrected.status_code == 200, corrected.text

        allowed = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 30.0}],
            },
            headers=headers,
        )
        assert allowed.status_code == 201, allowed.text

    async def test_employee_without_permission_is_forbidden(
        self, client, owner_user, employee_user
    ):
        owner_token = await self._login(client, "lucy", "S3curePass!")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        product_id, batch_id = await self._make_product_with_batch(client, owner_headers)

        employee_token = await self._login(client, "joe", "pass1234")
        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/expiry",
            json={"new_expiry_date": "2029-01-01", "reason": "should not be allowed"},
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert r.status_code == 403

    async def test_a_blank_reason_is_rejected(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/expiry",
            json={"new_expiry_date": "2029-01-01", "reason": ""},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_correction_is_audit_logged(self, client, owner_user):
        token = await self._login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        product_id, batch_id = await self._make_product_with_batch(client, headers)

        r = await client.patch(
            f"/api/v1/products/{product_id}/batches/{batch_id}/expiry",
            json={"new_expiry_date": "2029-06-30", "reason": "supplier packaging showed 2029"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        logs = await client.get(
            "/api/v1/audit-logs", params={"action": "batch.expiry_corrected"}, headers=headers
        )
        assert logs.status_code == 200, logs.text
        entries = logs.json()["entries"]
        matching = [e for e in entries if e["entity_id"] == str(batch_id)]
        assert len(matching) == 1
        assert matching[0]["old_value"] == "2028-01-01"
        assert "2029-06-30" in matching[0]["new_value"]
        assert "supplier packaging showed 2029" in matching[0]["new_value"]
