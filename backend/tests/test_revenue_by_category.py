"""
Revenue-by-category tests. The properties that matter, same rigor as
test_reports.py's TestProfitByProduct and TestTopCustomers:
  1. Category entries sum to the same authoritative total every other
     revenue report agrees on, and each entry's percent_of_total is
     correct relative to that real total.
  2. Discount proration and refund netting at the category level match
     the already-pinned per-product behavior exactly (they share the
     same underlying SQL, see report_service.py's _CategoryFilter).
  3. The category drill-down (top_products_in_category) never leaks a
     product from a different category into the ranking.
"""

from datetime import date

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.category import Category
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement


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


async def _make_category(name: str) -> int:
    async with AsyncSessionLocal() as db:
        category = Category(name=name)
        db.add(category)
        await db.commit()
        return int(category.id)


async def _make_product_with_batch(
    name: str,
    category_id: int | None = None,
    price: float = 10.0,
    cost: float = 4.0,
    qty: int = 50,
) -> int:
    async with AsyncSessionLocal() as db:
        product = Product(name=name, category_id=category_id)
        db.add(product)
        await db.flush()
        batch = MedicineBatch(
            product_id=product.id,
            batch_number=f"CAT-{name}",
            expiry_date=date(2027, 1, 1),
            qty_received=qty,
            qty_remaining=qty,
            cost_price=cost,
            selling_price=price,
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


async def _sell(client, headers, lines: list[tuple[int, int]], total: float, discount: float = 0.0):
    r = await client.post(
        "/api/v1/sales",
        json={
            "items": [{"product_id": product_id, "quantity": qty} for product_id, qty in lines],
            "payments": [{"method": "CASH", "amount": total}],
            "discount_amount": discount,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _refund(client, headers, sale: dict, quantity: int, line: int = 0) -> None:
    r = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={
            "reason": "CUSTOMER_RETURN",
            "method": "CASH",
            "items": [
                {"sale_item_id": sale["items"][line]["id"], "quantity": quantity, "restock": True}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text


def _cents(amount: float) -> int:
    return round(amount * 100)


class TestRevenueByCategory:
    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category",
            params={"start_date": today, "end_date": today},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_two_categories_sum_to_the_authoritative_total(self, client, owner_user):
        antibiotics = await _make_category("Antibiotics")
        painkillers = await _make_category("Painkillers")
        a_id = await _make_product_with_batch("Amoxicillin", antibiotics, price=10.0, cost=4.0)
        p_id = await _make_product_with_batch("Paracetamol", painkillers, price=20.0, cost=8.0)
        headers = await _owner_headers(client)
        await _sell(client, headers, [(a_id, 1)], total=10.0)
        await _sell(client, headers, [(p_id, 1)], total=20.0)

        today = (await _business_today()).isoformat()
        params = {"start_date": today, "end_date": today}
        total = (
            await client.get("/api/v1/reports/kpi-dashboard", params=params, headers=headers)
        ).json()["revenue"]
        r = await client.get(
            "/api/v1/reports/revenue-by-category", params=params, headers=headers
        )
        assert r.status_code == 200, r.text
        body = r.json()

        assert _cents(body["total_revenue"]) == _cents(total)
        assert sum(_cents(c["revenue"]) for c in body["categories"]) == _cents(total)
        by_name = {c["category_name"]: c for c in body["categories"]}
        assert by_name["Painkillers"]["revenue"] == 20.0
        assert by_name["Antibiotics"]["revenue"] == 10.0
        # Painkillers is 2/3 of the 30.0 total.
        assert by_name["Painkillers"]["percent_of_total"] == round(20.0 / 30.0 * 100, 1)
        assert by_name["Antibiotics"]["percent_of_total"] == round(10.0 / 30.0 * 100, 1)
        # Highest revenue first.
        assert [c["category_name"] for c in body["categories"]] == ["Painkillers", "Antibiotics"]

    async def test_products_with_no_category_are_grouped_as_uncategorised(
        self, client, owner_user
    ):
        loose_id = await _make_product_with_batch("Loose Item", category_id=None)
        headers = await _owner_headers(client)
        await _sell(client, headers, [(loose_id, 1)], total=10.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert len(body["categories"]) == 1
        assert body["categories"][0]["category_id"] is None
        assert body["categories"][0]["category_name"] == "Uncategorised"
        assert body["categories"][0]["revenue"] == 10.0
        assert body["categories"][0]["percent_of_total"] == 100.0

    async def test_a_discount_is_prorated_the_same_as_the_per_product_report(
        self, client, owner_user
    ):
        """Pinned the same way as top_products_by_revenue: a 250 sale
        discounted by 50 nets to exactly 200, now at the category level."""
        antibiotics = await _make_category("Antibiotics")
        product_id = await _make_product_with_batch(
            "Discounted Drug", antibiotics, price=250.0, cost=50.0, qty=5
        )
        headers = await _owner_headers(client)
        await _sell(client, headers, [(product_id, 1)], total=200.0, discount=50.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        assert body["categories"][0]["revenue"] == 200.0

    async def test_refund_nets_against_the_categorys_revenue_in_the_refund_period(
        self, client, owner_user
    ):
        antibiotics = await _make_category("Antibiotics")
        product_id = await _make_product_with_batch(
            "Refunded Drug", antibiotics, price=10.0, cost=4.0, qty=20
        )
        headers = await _owner_headers(client)
        sale = await _sell(client, headers, [(product_id, 5)], total=50.0)
        await _refund(client, headers, sale, quantity=2)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        body = r.json()
        # 5 sold at 10.0 = 50, minus 2 refunded at 10.0 = 20 -> net 30.
        assert body["categories"][0]["revenue"] == 30.0


class TestTopProductsInCategory:
    async def test_requires_reports_view_permission(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={"start_date": today, "end_date": today, "category_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_requires_category_id_or_uncategorised_flag(self, client, owner_user):
        headers = await _owner_headers(client)
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={"start_date": today, "end_date": today},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_rejects_both_category_id_and_uncategorised_together(self, client, owner_user):
        headers = await _owner_headers(client)
        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={
                "start_date": today,
                "end_date": today,
                "category_id": 1,
                "uncategorised": True,
            },
            headers=headers,
        )
        assert r.status_code == 422

    async def test_only_returns_products_from_the_requested_category(self, client, owner_user):
        antibiotics = await _make_category("Antibiotics")
        painkillers = await _make_category("Painkillers")
        amox_id = await _make_product_with_batch("Amoxicillin", antibiotics, price=10.0)
        pcm_id = await _make_product_with_batch("Paracetamol", painkillers, price=20.0)
        headers = await _owner_headers(client)
        await _sell(client, headers, [(amox_id, 1)], total=10.0)
        await _sell(client, headers, [(pcm_id, 1)], total=20.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={"start_date": today, "end_date": today, "category_id": antibiotics},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        names = [entry["name"] for entry in r.json()]
        assert names == ["Amoxicillin"]

    async def test_ranks_by_revenue_within_the_category(self, client, owner_user):
        antibiotics = await _make_category("Antibiotics")
        big_id = await _make_product_with_batch("Big Seller", antibiotics, price=100.0, qty=5)
        small_id = await _make_product_with_batch(
            "Small Seller", antibiotics, price=10.0, qty=5
        )
        headers = await _owner_headers(client)
        await _sell(client, headers, [(small_id, 1)], total=10.0)
        await _sell(client, headers, [(big_id, 1)], total=100.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={"start_date": today, "end_date": today, "category_id": antibiotics},
            headers=headers,
        )
        names = [entry["name"] for entry in r.json()]
        assert names == ["Big Seller", "Small Seller"]

    async def test_drills_into_the_uncategorised_bucket(self, client, owner_user):
        antibiotics = await _make_category("Antibiotics")
        categorised_id = await _make_product_with_batch("Amoxicillin", antibiotics, price=10.0)
        loose_id = await _make_product_with_batch("Loose Item", category_id=None, price=5.0)
        headers = await _owner_headers(client)
        await _sell(client, headers, [(categorised_id, 1)], total=10.0)
        await _sell(client, headers, [(loose_id, 1)], total=5.0)

        today = (await _business_today()).isoformat()
        r = await client.get(
            "/api/v1/reports/revenue-by-category/products",
            params={"start_date": today, "end_date": today, "uncategorised": True},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        names = [entry["name"] for entry in r.json()]
        assert names == ["Loose Item"]
