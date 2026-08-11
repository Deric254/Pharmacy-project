"""
Generates a large, realistic multi-year dataset directly via SQL --
API calls one at a time would take far too long at this volume.
Deliberately places sales at exact year-end, New Year, and leap-day
boundaries so date-range slicer accuracy can be independently verified
against hand-computed sums for the trickiest cases, not just typical
ones.

Usage: run from backend/ with DATABASE_URL, JWT_SECRET_KEY, and
ENCRYPTION_KEY set, against a fresh (migrated, empty) database.
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password

random.seed(42)

NUM_PRODUCTS = 250
YEARS_OF_HISTORY = 4
TOTAL_SALES = 60000


async def main() -> None:
    async with AsyncSessionLocal() as db:
        role_result = await db.execute(text("SELECT id FROM roles WHERE name = 'ChemistOwner'"))
        role_id = role_result.scalar_one()

        await db.execute(
            text(
                "INSERT INTO users (full_name, username, hashed_password, role_id, "
                "is_active, must_change_password, security_question, security_answer_hash) "
                "VALUES ('Mega Owner', 'megaowner', :pw, :role_id, 1, 0, 'Q?', :ans)"
            ),
            {
                "pw": hash_password("MegaPass123"),
                "role_id": role_id,
                "ans": hash_password("A"),
            },
        )
        await db.commit()
        user_result = await db.execute(text("SELECT id FROM users WHERE username='megaowner'"))
        user_id = user_result.scalar_one()

        print(f"Creating {NUM_PRODUCTS} products with batches...")
        product_ids: list[tuple[int, float, float, int]] = []
        now = datetime.now()
        start_date = now - timedelta(days=365 * YEARS_OF_HISTORY)

        for i in range(NUM_PRODUCTS):
            price = round(random.uniform(5, 100), 2)
            cost = round(price * random.uniform(0.3, 0.7), 2)
            result = await db.execute(
                text(
                    "INSERT INTO products (name, default_selling_price, reorder_point, "
                    "is_active) VALUES (:name, :price, 20, 1) RETURNING id"
                ),
                {"name": f"Stress Test Product {i:04d}", "price": price},
            )
            pid = result.scalar_one()

            batch_result = await db.execute(
                text(
                    "INSERT INTO medicine_batches (product_id, batch_number, expiry_date, "
                    "qty_received, qty_remaining, cost_price) "
                    "VALUES (:pid, :bn, :exp, 100000, 100000, :cost) RETURNING id"
                ),
                {
                    "pid": pid,
                    "bn": f"BATCH-{i:04d}",
                    "exp": (now + timedelta(days=1000)).date().isoformat(),
                    "cost": cost,
                },
            )
            batch_id = batch_result.scalar_one()
            product_ids.append((pid, price, cost, batch_id))
        await db.commit()
        print(f"Products created: {len(product_ids)}")

        # Deliberately-placed sales at tricky date boundaries, so we
        # can verify exact slicer behavior at each one independently.
        boundary_dates: list[datetime] = []
        for year in range(start_date.year, now.year + 1):
            boundary_dates += [
                datetime(year, 12, 31, 23, 59, 0),  # last second of the year
                datetime(year, 1, 1, 0, 0, 30),  # first moments of the year
            ]
        boundary_dates.append(datetime(2024, 2, 29, 12, 0, 0))  # real leap day
        boundary_dates = [d for d in boundary_dates if start_date <= d <= now]

        print(f"Generating {TOTAL_SALES} sales across {YEARS_OF_HISTORY} years...")
        total_seconds = int((now - start_date).total_seconds())

        batch_size = 2000
        sale_rows: list[dict[str, Any]] = []
        item_rows: list[dict[str, Any]] = []
        payment_rows: list[dict[str, Any]] = []

        for i in range(TOTAL_SALES):
            if i < len(boundary_dates):
                sale_time = boundary_dates[i]
            else:
                sale_time = start_date + timedelta(seconds=random.randint(0, total_seconds))

            local_idx = len(sale_rows)
            num_items = random.randint(1, 3)
            chosen = random.sample(product_ids, num_items)
            subtotal = 0.0
            items_for_sale: list[tuple[int, int, int, float, float]] = []
            for pid, price, _cost, batch_id in chosen:
                qty = random.randint(1, 5)
                line_total = round(price * qty, 2)
                subtotal += line_total
                items_for_sale.append((pid, batch_id, qty, price, line_total))
            subtotal = round(subtotal, 2)

            sale_rows.append(
                {
                    "subtotal": subtotal,
                    "discount_amount": 0.0,
                    "total_amount": subtotal,
                    "cashier_user_id": user_id,
                    "created_at": sale_time.isoformat(),
                }
            )
            payment_rows.append({"amount": subtotal, "method": "CASH", "idx": local_idx})
            for pid, batch_id, qty, price, line_total in items_for_sale:
                item_rows.append(
                    {
                        "product_id": pid,
                        "batch_id": batch_id,
                        "quantity": qty,
                        "unit_price": price,
                        "line_total": line_total,
                        "idx": local_idx,
                    }
                )

            if len(sale_rows) >= batch_size or i == TOTAL_SALES - 1:
                sale_ids: list[int] = []
                for row in sale_rows:
                    result = await db.execute(
                        text(
                            "INSERT INTO sales (subtotal, discount_amount, total_amount, "
                            "cashier_user_id, created_at) VALUES "
                            "(:subtotal, :discount_amount, :total_amount, :cashier_user_id, "
                            ":created_at) RETURNING id"
                        ),
                        row,
                    )
                    sale_ids.append(result.scalar_one())

                for item in item_rows:
                    item["sale_id"] = sale_ids[item.pop("idx")]
                for pay in payment_rows:
                    pay["sale_id"] = sale_ids[pay.pop("idx")]

                if item_rows:
                    await db.execute(
                        text(
                            "INSERT INTO sale_items (sale_id, product_id, batch_id, "
                            "quantity, unit_price, line_total) VALUES "
                            "(:sale_id, :product_id, :batch_id, :quantity, :unit_price, "
                            ":line_total)"
                        ),
                        item_rows,
                    )
                if payment_rows:
                    await db.execute(
                        text(
                            "INSERT INTO payments (sale_id, amount, method) VALUES "
                            "(:sale_id, :amount, :method)"
                        ),
                        payment_rows,
                    )
                await db.commit()
                sale_rows, item_rows, payment_rows = [], [], []
                if (i + 1) % 10000 == 0:
                    print(f"  ...{i + 1} sales inserted")

        print("Updating batch qty_remaining and stock movements to match real sales...")
        await db.execute(
            text(
                "UPDATE medicine_batches SET qty_remaining = qty_received - COALESCE("
                "(SELECT SUM(si.quantity) FROM sale_items si "
                "WHERE si.batch_id = medicine_batches.id), 0)"
            )
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements (batch_id, quantity_delta, movement_type, "
                "created_at) SELECT batch_id, -quantity, 'SALE', "
                "(SELECT created_at FROM sales WHERE sales.id = sale_items.sale_id) "
                "FROM sale_items"
            )
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements (batch_id, quantity_delta, movement_type, "
                "created_at) SELECT id, qty_received, 'RECEIVE', datetime('now') "
                "FROM medicine_batches"
            )
        )
        await db.commit()
        print("Data generation complete.")


if __name__ == "__main__":
    asyncio.run(main())
