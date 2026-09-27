"""
Supplier tests. Covers the pre-existing create/get endpoints (no test
file existed for these before) and the new time-sliced KPI endpoint,
whose properties that matter are:
  1. "Purchased" and "paid" are flows scoped strictly to the selected
     date range -- a transaction outside the range must not leak in.
  2. "net_due" is a balance as of the end of that range, not a flow --
     it includes everything up to end_date regardless of when it
     started, and is exactly what SupplierService._to_schema's
     balance_owed already computes when end_date is today.
  3. Uses the same signed SupplierTransaction ledger as balance_owed,
     so it can never disagree with the number shown on the supplier's
     own record.
"""

from datetime import UTC, date, datetime, timedelta

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.supplier import SupplierTransaction


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(client) -> dict[str, str]:
    token = await _login(client, "lucy", "S3curePass!")
    return {"Authorization": f"Bearer {token}"}


async def _business_today() -> date:
    async with AsyncSessionLocal() as db:
        return await business_today(db)


async def _make_supplier(client, headers, name: str) -> int:
    r = await client.post("/api/v1/suppliers", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _make_transaction(supplier_id: int, amount: float, days_ago: int = 0) -> None:
    """A transaction dated in the past, bypassing the API (which always
    timestamps 'now') so range-boundary behaviour is actually testable."""
    async with AsyncSessionLocal() as db:
        db.add(
            SupplierTransaction(
                supplier_id=supplier_id,
                amount=amount,
                created_at=datetime.now(UTC) - timedelta(days=days_ago),
                reference="test",
            )
        )
        await db.commit()


class TestSupplierCreateAndGet:
    async def test_requires_purchasing_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        r = await client.post(
            "/api/v1/suppliers",
            json={"name": "Should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_create_and_fetch(self, client, owner_user):
        headers = await _owner_headers(client)
        r = await client.post(
            "/api/v1/suppliers",
            json={"name": "MedSupply Ltd", "contact_phone": "0700000000"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        supplier_id = r.json()["id"]
        assert r.json()["balance_owed"] == 0.0

        r2 = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "MedSupply Ltd"


class TestSupplierKpis:
    async def test_requires_purchasing_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_zero_activity_gives_zeroed_kpis(self, client, owner_user):
        headers = await _owner_headers(client)
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "start_date": today,
            "end_date": today,
            "total_purchased": 0.0,
            "total_paid": 0.0,
            "net_due": 0.0,
            "active_supplier_count": 0,
        }

    async def test_purchased_and_paid_within_the_period(self, client, owner_user):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "KPI Supplier")
        await _make_transaction(supplier_id, 500.0)  # charge: goods received
        await _make_transaction(supplier_id, -200.0)  # payment

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 500.0
        assert body["total_paid"] == 200.0
        assert body["net_due"] == 300.0
        assert body["active_supplier_count"] == 1

    async def test_transactions_outside_the_range_are_excluded_from_the_flows(
        self, client, owner_user
    ):
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Old Activity Supplier")
        await _make_transaction(supplier_id, 500.0, days_ago=30)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 0.0
        assert body["active_supplier_count"] == 0
        # But the balance from that old charge is still owed today.
        assert body["net_due"] == 500.0

    async def test_net_due_is_cumulative_across_multiple_suppliers(self, client, owner_user):
        headers = await _owner_headers(client)
        a = await _make_supplier(client, headers, "Supplier A")
        b = await _make_supplier(client, headers, "Supplier B")
        await _make_transaction(a, 300.0)
        await _make_transaction(b, 700.0)
        await _make_transaction(b, -100.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 1000.0
        assert body["total_paid"] == 100.0
        assert body["net_due"] == 900.0
        assert body["active_supplier_count"] == 2

    async def test_a_real_purchase_creates_a_matching_charge(self, client, owner_user):
        """End-to-end: quick_purchase's own SupplierTransaction write
        (not a hand-inserted test row) is what the KPI reads."""
        headers = await _owner_headers(client)
        supplier_id = await _make_supplier(client, headers, "Real PO Supplier")
        product = await client.post(
            "/api/v1/products", json={"name": "KPI Test Product"}, headers=headers
        )
        product_id = product.json()["id"]

        await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 10,
                        "batch_number": "KPI-001",
                        "expiry_date": "2027-06-30",
                        "unit_cost": 5.0,
                        "selling_price": 9.0,
                    }
                ],
            },
            headers=headers,
        )

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/suppliers/kpis",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["total_purchased"] == 50.0
        assert body["net_due"] == 50.0
