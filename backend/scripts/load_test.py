"""
Real load test, not a guess. Seeds a large, realistic multi-year sales
history directly (bypassing the API/business-logic layer on purpose --
this is measuring the DATABASE and QUERY layer's behavior at volume,
not re-testing checkout logic that's already covered elsewhere), then
times the exact queries a pharmacy owner would actually run against
it: today's dashboard, a full-year revenue trend, a paginated sales
list, and the busiest report queries.

Run with: python -m scripts.load_test [num_sales]
"""

import asyncio
import random
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

sys.path.insert(0, ".")


async def main(num_sales: int) -> None:
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/load_test.db")
    os.environ.setdefault("JWT_SECRET_KEY", "load-test-secret-0123456789-0123456789")
    os.environ.setdefault("ENCRYPTION_KEY", "cGhhcm1hY3ktZXJwLXRlc3Qta2V5LTAxMjM0NTY3ODk=")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

    db_path = "/tmp/load_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    from app.core.database import AsyncSessionLocal, Base, engine
    from app.core.security import hash_password
    from app.models.business_config import BusinessConfig
    from app.models.category import Category
    from app.models.medicine_batch import MedicineBatch
    from app.models.product import Product
    from app.models.role import Role
    from app.models.sale import Payment, PaymentMethod, Sale, SaleItem
    from app.models.supplier import Supplier, SupplierTransaction
    from app.models.user import User

    print(f"Seeding {num_sales:,} sales directly into a fresh SQLite database...")

    # Spread sales across 3 real years, at random times of day, so
    # date-range queries (today / this month / this year) all have to
    # do real filtering work, not just "return everything".
    start = datetime(2023, 1, 1)
    end = datetime(2026, 8, 9)
    span_seconds = int((end - start).total_seconds())

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        db.add(BusinessConfig(id=1, business_name="Load Test Pharmacy", timezone="Africa/Nairobi"))
        role = Role(name="Owner")
        db.add(role)
        await db.flush()
        user = User(
            username="loadtest",
            full_name="Load Test",
            hashed_password=await hash_password("x"),
            role_id=role.id,
            must_change_password=False,
        )
        db.add(user)

        # 8 real categories, but the first 5 products deliberately stay
        # uncategorised -- revenue_by_category's "Uncategorised" bucket
        # and top_products_in_category(category_id=None) need to be
        # exercised at real volume too, not just the happy path.
        categories = [Category(name=f"Load Test Category {i}") for i in range(8)]
        db.add_all(categories)
        await db.flush()
        category_ids = [c.id for c in categories]

        products = [
            Product(
                name=f"Load Test Product {i}",
                category_id=None if i < 5 else category_ids[i % len(category_ids)],
            )
            for i in range(50)
        ]
        db.add_all(products)
        await db.flush()

        # A handful of suppliers with a realistic charge/payment history,
        # for the supplier-KPI endpoint's own timing below. Small volume
        # on purpose -- purchase-order activity is inherently much lower
        # frequency than retail sales for a real pharmacy.
        suppliers = [Supplier(name=f"Load Test Supplier {i}") for i in range(20)]
        db.add_all(suppliers)
        await db.flush()
        for s_idx, supplier in enumerate(suppliers):
            for t in range(50):
                charge_date = start + timedelta(days=random.randint(0, 1300))
                db.add(
                    SupplierTransaction(
                        supplier_id=supplier.id,
                        amount=round(random.uniform(50, 5000), 2),
                        created_at=charge_date,
                        reference=f"LT-PO-{s_idx}-{t}",
                    )
                )
                if t % 3 == 0:
                    db.add(
                        SupplierTransaction(
                            supplier_id=supplier.id,
                            amount=-round(random.uniform(50, 5000), 2),
                            created_at=charge_date + timedelta(days=random.randint(1, 30)),
                            reference=f"LT-PAY-{s_idx}-{t}",
                        )
                    )
        await db.commit()

        batches = []
        for p in products:
            selling_price = 10.0 + products.index(p)
            b = MedicineBatch(
                product_id=p.id,
                batch_number=f"LT-{p.id}",
                expiry_date=date(2028, 1, 1),
                qty_received=10_000_000,
                qty_remaining=10_000_000,
                cost_price=selling_price / 2,
                selling_price=selling_price,
            )
            batches.append(b)
        db.add_all(batches)
        await db.flush()
        await db.commit()
        product_ids = [p.id for p in products]
        batch_by_product = {p.id: b.id for p, b in zip(products, batches, strict=True)}
        cost_by_product = {p.id: b.cost_price for p, b in zip(products, batches, strict=True)}
        user_id = user.id

    BATCH_SIZE = 2000
    seeded = 0
    seed_start = time.monotonic()
    async with AsyncSessionLocal() as db:
        while seeded < num_sales:
            chunk = min(BATCH_SIZE, num_sales - seeded)
            for _ in range(chunk):
                created_at = start + timedelta(seconds=random.randint(0, span_seconds))
                product_id = random.choice(product_ids)
                qty = random.randint(1, 5)
                unit_price = 10.0 + (product_id % 50)
                line_total = round(unit_price * qty, 2)
                sale = Sale(
                    cashier_user_id=user_id,
                    subtotal=line_total,
                    discount_amount=0.0,
                    total_amount=line_total,
                    created_at=created_at,
                )
                db.add(sale)
                await db.flush()
                db.add(
                    SaleItem(
                        sale_id=sale.id,
                        product_id=product_id,
                        batch_id=batch_by_product[product_id],
                        quantity=qty,
                        unit_price=unit_price,
                        unit_cost=cost_by_product[product_id],
                        line_total=line_total,
                    )
                )
                db.add(Payment(sale_id=sale.id, method=PaymentMethod.CASH, amount=line_total))
            await db.commit()
            seeded += chunk
            print(f"  seeded {seeded:,}/{num_sales:,}", end="\r")
    seed_elapsed = time.monotonic() - seed_start
    print(f"\nSeeding done: {num_sales:,} sales in {seed_elapsed:.1f}s")

    # Real ANALYZE, matching what a production deployment would have
    # after real usage -- without this, SQLite's query planner may
    # make worse choices than it would in the field.
    async with AsyncSessionLocal() as db:
        await db.execute(text("ANALYZE"))
        await db.commit()

    from app.services.report_service import ReportService
    from app.services.sale_service import SaleService
    from app.services.supplier_service import SupplierService

    async def timed(label: str, coro: Any) -> None:
        t0 = time.monotonic()
        result = await coro
        elapsed = (time.monotonic() - t0) * 1000
        size = ""
        if hasattr(result, "points"):
            size = f" ({len(result.points)} points)"
        elif hasattr(result, "entries"):
            size = f" ({len(result.entries)} rows)"
        flag = "  <-- SLOW" if elapsed > 500 else ""
        print(f"  {label:55s} {elapsed:8.1f}ms{size}{flag}")

    print(f"\n=== Query timings against {num_sales:,} real sales ===")
    async with AsyncSessionLocal() as db:
        reports = ReportService(db)
        sales_svc = SaleService(db)

        await timed(
            "KPI dashboard, today",
            reports.kpi_dashboard(date(2026, 8, 9), date(2026, 8, 9), include_profit=True),
        )
        await timed(
            "KPI dashboard, this month",
            reports.kpi_dashboard(date(2026, 8, 1), date(2026, 8, 9), include_profit=True),
        )
        await timed(
            "Revenue trend, full year (daily->weekly->monthly auto)",
            reports.revenue_trend(date(2026, 1, 1), date(2026, 8, 9), include_profit=True),
        )
        await timed(
            "Revenue trend, full 3.5 years (monthly)",
            reports.revenue_trend(date(2023, 1, 1), date(2026, 8, 9), include_profit=True),
        )
        await timed(
            "Top products by revenue, full history",
            reports.top_products_by_revenue(date(2023, 1, 1), date(2026, 8, 9), limit=20),
        )
        await timed(
            "Sales list, page 1 (50 rows)",
            sales_svc.list_sales(None, None, limit=50, offset=0),
        )
        await timed(
            "Sales list, deep page (offset 50000)",
            sales_svc.list_sales(None, None, limit=50, offset=min(50_000, max(0, num_sales - 100))),
        )
        await timed(
            "Sales list filtered to one month",
            sales_svc.list_sales(date(2026, 8, 1), date(2026, 8, 9), limit=50, offset=0),
        )
        await timed(
            "Profit report, full 3.5 years",
            reports.profit_report(date(2023, 1, 1), date(2026, 8, 9)),
        )
        await timed(
            "Revenue by category, full 3.5 years (heaviest possible case: "
            "same multi-join discount-proration as top_products_by_revenue, "
            "plus a Category outer join and a GROUP BY)",
            reports.revenue_by_category(date(2023, 1, 1), date(2026, 8, 9)),
        )
        await timed(
            "Revenue-by-category drill-down, one real category, full history",
            reports.top_products_in_category(
                date(2023, 1, 1), date(2026, 8, 9), category_id=category_ids[0], limit=20
            ),
        )
        await timed(
            "Revenue-by-category drill-down, Uncategorised bucket, full history",
            reports.top_products_in_category(
                date(2023, 1, 1), date(2026, 8, 9), category_id=None, limit=20
            ),
        )

        suppliers_svc = SupplierService(db)
        await timed(
            "Supplier KPIs, full 3.5 years (20 suppliers, ~1,000 transactions)",
            suppliers_svc.kpis(date(2023, 1, 1), date(2026, 8, 9)),
        )

    print("\nDone.")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    asyncio.run(main(n))
