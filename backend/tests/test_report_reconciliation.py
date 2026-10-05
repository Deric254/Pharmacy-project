"""
Randomised reconciliation: every revenue/profit report must agree, to the
cent, with the ledger it is computed from (sales minus refunds).

The hand-written report tests pin specific scenarios. This one sweeps a
large, seeded-random mix of prices, quantities, uneven discounts and
partial refunds, because rounding disagreements only show up for particular
combinations nobody thinks to write down. The seed is fixed so a failure is
reproducible; change it locally to explore.
"""

import random
from datetime import date

import pytest

from app.core.business_time import business_today
from app.core.database import AsyncSessionLocal
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.stock_movement import MovementType, StockMovement

# Fixed seeds keep any failure reproducible; add more to explore.
SEEDS = (20261004, 7, 1234567)
PRODUCT_COUNT = 6
SALE_COUNT = 40
REFUND_COUNT = 14
STOCK_PER_PRODUCT = 5000


def _cents(amount: float) -> int:
    return round(amount * 100)


async def _login_owner(client) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"username": "lucy", "password": "S3curePass!"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_products(rng: random.Random) -> list[tuple[int, int, int]]:
    """Returns [(product_id, price_cents, cost_cents)]; one batch each."""
    made: list[tuple[int, int, int]] = []
    async with AsyncSessionLocal() as db:
        for index in range(PRODUCT_COUNT):
            price_cents = rng.randint(37, 9999)
            cost_cents = rng.randint(1, price_cents)
            product = Product(name=f"Reconcile {index}")
            db.add(product)
            await db.flush()
            batch = MedicineBatch(
                product_id=product.id,
                batch_number=f"RC{index}",
                expiry_date=date(2097, 1, 1),
                qty_received=STOCK_PER_PRODUCT,
                qty_remaining=STOCK_PER_PRODUCT,
                cost_price=cost_cents / 100,
                selling_price=price_cents / 100,
            )
            db.add(batch)
            await db.flush()
            db.add(
                StockMovement(
                    batch_id=batch.id,
                    movement_type=MovementType.PURCHASE,
                    quantity_delta=STOCK_PER_PRODUCT,
                    created_by_user_id=None,
                )
            )
            made.append((int(product.id), price_cents, cost_cents))
        await db.commit()
    return made


@pytest.mark.parametrize("seed", SEEDS)
async def test_every_report_adds_up_to_the_sales_and_refunds_ledger(client, owner_user, seed):
    rng = random.Random(seed)
    products = await _make_products(rng)
    headers = await _login_owner(client)

    sold_cents = 0
    sales: list[dict] = []
    for _ in range(SALE_COUNT):
        chosen = rng.sample(products, rng.randint(1, 4))
        lines = [(pid, price, rng.randint(1, 7)) for pid, price, _cost in chosen]
        subtotal_cents = sum(price * qty for _pid, price, qty in lines)
        discount_cents = rng.randint(0, subtotal_cents // 3)
        total_cents = subtotal_cents - discount_cents
        r = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": pid, "quantity": qty} for pid, _p, qty in lines],
                "payments": [{"method": "CASH", "amount": total_cents / 100}],
                "discount_amount": discount_cents / 100,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert _cents(body["total_amount"]) == total_cents
        sold_cents += total_cents
        sales.append(body)

    refunded_cents = 0
    refunded_so_far: dict[int, int] = {}
    for sale in rng.sample(sales, REFUND_COUNT):
        line = rng.choice(sale["items"])
        remaining = line["quantity"] - refunded_so_far.get(line["id"], 0)
        if remaining <= 0:
            continue
        quantity = rng.randint(1, remaining)
        restock = rng.random() < 0.5
        r = await client.post(
            f"/api/v1/sales/{sale['id']}/refunds",
            json={
                "reason": "CUSTOMER_RETURN" if restock else "DAMAGED",
                "method": "CASH",
                "items": [{"sale_item_id": line["id"], "quantity": quantity, "restock": restock}],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        refunded_cents += _cents(r.json()["total_amount"])
        refunded_so_far[line["id"]] = refunded_so_far.get(line["id"], 0) + quantity

    expected_revenue = sold_cents - refunded_cents
    assert refunded_cents > 0  # the sweep really did exercise refunds

    today = (await _business_today()).isoformat()
    params = {"start_date": today, "end_date": today}

    profit = (await client.get("/api/v1/reports/profit", params=params, headers=headers)).json()
    assert _cents(profit["total_revenue"]) == expected_revenue

    by_product = (
        await client.get("/api/v1/reports/profit-by-product", params=params, headers=headers)
    ).json()
    assert sum(_cents(e["revenue"]) for e in by_product) == expected_revenue
    assert sum(_cents(e["cost"]) for e in by_product) == _cents(profit["total_cost"])
    assert sum(_cents(e["profit"]) for e in by_product) == _cents(profit["total_profit"])
    for entry in by_product:
        assert _cents(entry["revenue"]) - _cents(entry["cost"]) == _cents(entry["profit"])

    by_category = (
        await client.get("/api/v1/reports/revenue-by-category", params=params, headers=headers)
    ).json()
    assert _cents(by_category["total_revenue"]) == expected_revenue
    assert sum(_cents(c["revenue"]) for c in by_category["categories"]) == expected_revenue


async def _business_today() -> date:
    async with AsyncSessionLocal() as db:
        return await business_today(db)
