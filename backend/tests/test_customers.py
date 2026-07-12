"""
Customer tests. The properties that matter:
  1. Purchase history reflects real sales, queried live - not a
     duplicated/stale copy.
  2. Loyalty points only accrue when the Config Panel's toggle is on,
     and at the configured rate - proving the sale-to-config-to-
     customer wiring actually works end to end, not just each piece
     in isolation.
"""

from datetime import date

from app.core.database import AsyncSessionLocal
from app.models.business_config import BusinessConfig
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _make_product_with_batch(qty: int = 50, price: float = 10.0) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name="Customer Test Product", default_selling_price=price)
        db.add(product)
        await db.flush()
        batch = MedicineBatch(
            product_id=product.id,
            batch_number="C1",
            expiry_date=date(2027, 1, 1),
            qty_received=qty,
            qty_remaining=qty,
            cost_price=2.0,
        )
        db.add(batch)
        await db.flush()
        db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=MovementType.PURCHASE,
                quantity_delta=qty,
                created_by_user_id=None,
            )
        )
        await db.commit()
        return int(product.id)


async def _enable_loyalty(rate: float = 1.0) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        result = await db.execute(select(BusinessConfig).where(BusinessConfig.id == 1))
        config = result.scalar_one_or_none()
        if config is None:
            config = BusinessConfig(id=1)
            db.add(config)
        config.loyalty_program_enabled = True
        config.loyalty_points_per_currency_unit = rate
        await db.commit()


class TestCustomerCRUD:
    async def test_create_and_get(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/customers",
            json={"name": "Jane Wanjiru", "phone": "0712345678"},
            headers=headers,
        )
        assert r.status_code == 201
        assert r.json()["loyalty_points"] == 0

        customer_id = r.json()["id"]
        r2 = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Jane Wanjiru"

    async def test_duplicate_phone_rejected(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/customers", json={"name": "First", "phone": "0700000001"}, headers=headers
        )
        r = await client.post(
            "/api/v1/customers", json={"name": "Second", "phone": "0700000001"}, headers=headers
        )
        assert r.status_code == 409

    async def test_lookup_by_phone(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post(
            "/api/v1/customers",
            json={"name": "Phone Lookup", "phone": "0722222222"},
            headers=headers,
        )
        r = await client.get("/api/v1/customers/phone/0722222222", headers=headers)
        assert r.status_code == 200
        assert r.json()["name"] == "Phone Lookup"

    async def test_lookup_by_unknown_phone_returns_404(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/customers/phone/0700000000", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 404

    async def test_search_by_name(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        await client.post("/api/v1/customers", json={"name": "Alice Njeri"}, headers=headers)
        await client.post("/api/v1/customers", json={"name": "Bob Otieno"}, headers=headers)

        r = await client.get("/api/v1/customers?search=Njeri", headers=headers)
        names = {c["name"] for c in r.json()}
        assert "Alice Njeri" in names
        assert "Bob Otieno" not in names


class TestPurchaseHistory:
    async def test_purchase_history_reflects_real_sales(self, client, employee_user):
        product_id = await _make_product_with_batch(qty=50, price=10.0)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        customer_resp = await client.post(
            "/api/v1/customers", json={"name": "Loyal Customer"}, headers=headers
        )
        customer_id = customer_resp.json()["id"]

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 3}],
                "payments": [{"method": "CASH", "amount": 30.0}],
                "customer_id": customer_id,
            },
            headers=headers,
        )
        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
                "customer_id": customer_id,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/customers/{customer_id}/purchase-history", headers=headers)
        assert r.status_code == 200
        totals = sorted(entry["total_amount"] for entry in r.json())
        assert totals == [20.0, 30.0]

    async def test_purchase_history_empty_for_new_customer(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}
        customer_resp = await client.post(
            "/api/v1/customers", json={"name": "Never Bought Anything"}, headers=headers
        )
        r = await client.get(
            f"/api/v1/customers/{customer_resp.json()['id']}/purchase-history", headers=headers
        )
        assert r.json() == []

    async def test_purchase_history_for_unknown_customer_404s(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.get(
            "/api/v1/customers/999999/purchase-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


class TestSaleWithCustomer:
    async def test_sale_with_unknown_customer_id_rejected(self, client, employee_user):
        product_id = await _make_product_with_batch()
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 10.0}],
                "customer_id": 999999,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_sale_without_customer_still_works(self, client, employee_user):
        product_id = await _make_product_with_batch()
        token = await _login(client, "joe", "pass1234")

        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 10.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["customer_id"] is None


class TestLoyaltyPoints:
    async def test_points_awarded_when_program_enabled(self, client, employee_user):
        await _enable_loyalty(rate=2.0)  # 2 points per currency unit
        product_id = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        customer_resp = await client.post(
            "/api/v1/customers", json={"name": "Points Customer"}, headers=headers
        )
        customer_id = customer_resp.json()["id"]

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],  # total = 50.0
                "payments": [{"method": "CASH", "amount": 50.0}],
                "customer_id": customer_id,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
        assert r.json()["loyalty_points"] == 100  # 50.0 * 2.0

    async def test_no_points_when_program_disabled(self, client, employee_user):
        # loyalty disabled by default -- no _enable_loyalty() call
        product_id = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        customer_resp = await client.post(
            "/api/v1/customers", json={"name": "No Points Customer"}, headers=headers
        )
        customer_id = customer_resp.json()["id"]

        await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 5}],
                "payments": [{"method": "CASH", "amount": 50.0}],
                "customer_id": customer_id,
            },
            headers=headers,
        )

        r = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
        assert r.json()["loyalty_points"] == 0

    async def test_no_points_when_no_customer_attached(self, client, employee_user):
        await _enable_loyalty(rate=1.0)
        product_id = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")

        # Sale with no customer_id -- should not error, just skip loyalty.
        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 2}],
                "payments": [{"method": "CASH", "amount": 20.0}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201

    async def test_points_accumulate_across_multiple_sales(self, client, employee_user):
        await _enable_loyalty(rate=1.0)
        product_id = await _make_product_with_batch(price=10.0)
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        customer_resp = await client.post(
            "/api/v1/customers", json={"name": "Repeat Customer"}, headers=headers
        )
        customer_id = customer_resp.json()["id"]

        for _ in range(3):
            await client.post(
                "/api/v1/sales",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "payments": [{"method": "CASH", "amount": 10.0}],
                    "customer_id": customer_id,
                },
                headers=headers,
            )

        r = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
        assert r.json()["loyalty_points"] == 30  # 10 points x 3 sales
