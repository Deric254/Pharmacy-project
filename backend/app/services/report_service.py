"""
Report service.

Every report here reads from the same ledger/transactional tables the
operational modules write to (sales, sale_items, medicine_batches,
purchase_order_items, stock_takes) -- no separate analytics database or
ETL step, which would be over-engineering at this scale and would risk
the reports drifting from reality. This product is SQLite-only (no
MySQL driver is installed, no MySQL config exists anywhere in this
codebase) -- date-range and grouping queries use SQLite's own date()
and strftime() functions directly for real SQL-side aggregation,
which is what keeps reports fast regardless of how many years of
sales have accumulated, rather than loading every row into Python.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import (
    get_business_timezone,
    local_day_bounds_utc,
    local_offset_segments,
)
from app.models.customer import Customer
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrderItem
from app.models.sale import Sale, SaleItem
from app.models.stock_take import StockTake, StockTakeStatus
from app.schemas.reports import (
    ExpiredStockEntry,
    ExpiredStockReportOut,
    FastSlowMoversOut,
    KpiDashboardOut,
    NeverSoldEntry,
    ProductMovementEntry,
    ProfitReportOut,
    ReceivingDiscrepancyEntry,
    ReceivingDiscrepancyReportOut,
    RevenuePotentialEntry,
    RevenuePotentialOut,
    RevenueTrendOut,
    RevenueTrendPoint,
    SalesSummaryEntry,
    SalesSummaryOut,
    StockRunwayEntry,
    StockRunwayOut,
    StockTakeHistoryEntry,
    StockTakeHistoryOut,
    TopCustomerEntry,
    TopCustomersOut,
    TopProductEntry,
)
from app.services.inventory_service import InventoryService


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sales_summary(
        self, start_date: date, end_date: date, group_by: str = "day"
    ) -> SalesSummaryOut:
        sales = await self._sales_in_range(start_date, end_date)
        tz = await get_business_timezone(self.db)

        buckets: dict[str, list[Sale]] = defaultdict(list)
        for sale in sales:
            # created_at is stored UTC; convert to the business's own
            # local time via astimezone(), which resolves DST using
            # THIS row's own date -- not a single offset computed once
            # for the whole request -- or a sale made early in the
            # local day would still group under the UTC day before,
            # even though _sales_in_range's own filter is now
            # timezone-correct.
            local_created_at = sale.created_at.replace(tzinfo=UTC).astimezone(tz)
            key = (
                local_created_at.strftime("%Y-%m-%d")
                if group_by == "day"
                else local_created_at.strftime("%Y-%m")
            )
            buckets[key].append(sale)

        entries = [
            SalesSummaryEntry(
                period=period,
                sale_count=len(bucket_sales),
                total_revenue=sum(s.total_amount for s in bucket_sales),
                total_discount=sum(s.discount_amount for s in bucket_sales),
            )
            for period, bucket_sales in sorted(buckets.items())
        ]

        return SalesSummaryOut(
            entries=entries,
            total_revenue=sum(e.total_revenue for e in entries),
            total_sale_count=sum(e.sale_count for e in entries),
        )

    async def profit_report(self, start_date: date, end_date: date) -> ProfitReportOut:
        total_revenue, _ = await self._revenue_and_count_in_range(start_date, end_date)
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)

        cost_result = await self.db.execute(
            select(func.coalesce(func.sum(SaleItem.quantity * MedicineBatch.cost_price), 0.0))
            .join(MedicineBatch, MedicineBatch.id == SaleItem.batch_id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )
        )
        total_cost = float(cost_result.scalar_one())

        total_profit = total_revenue - total_cost
        margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0.0

        return ProfitReportOut(
            start_date=start_date,
            end_date=end_date,
            total_revenue=total_revenue,
            total_cost=total_cost,
            total_profit=total_profit,
            profit_margin_percent=round(margin, 2),
        )

    async def expired_stock(self) -> ExpiredStockReportOut:
        today = date.today()
        result = await self.db.execute(
            select(MedicineBatch, Product.name)
            .join(Product, Product.id == MedicineBatch.product_id)
            .where(MedicineBatch.expiry_date < today, MedicineBatch.qty_remaining > 0)
            .order_by(MedicineBatch.expiry_date)
        )

        entries = [
            ExpiredStockEntry(
                batch_id=batch.id,
                product_id=batch.product_id,
                product_name=product_name,
                batch_number=batch.batch_number,
                expiry_date=batch.expiry_date,
                days_expired=(today - batch.expiry_date).days,
                qty_remaining=batch.qty_remaining,
                value_at_cost=batch.qty_remaining * batch.cost_price,
            )
            for batch, product_name in result.all()
        ]

        total_value = sum(e.value_at_cost for e in entries)
        recommendation = (
            "No expired stock currently in inventory."
            if not entries
            else (
                f"{len(entries)} batch(es) worth {total_value:.2f} are expired and still "
                "counted as stock on hand -- write these off via an inventory adjustment "
                "(reason: EXPIRED) to keep stock valuation accurate."
            )
        )

        return ExpiredStockReportOut(
            entries=entries, total_value=total_value, recommendation=recommendation
        )

    async def fast_slow_movers(self, days: int = 30, limit: int = 10) -> FastSlowMoversOut:
        # UTC, matching how Sale.created_at is stored -- datetime.now()
        # (naive local time) would silently widen or narrow this
        # rolling window by the business's UTC offset depending on the
        # server OS's local clock, the same class of bug already fixed
        # for the calendar-day report filters above.
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

        result = await self.db.execute(
            select(SaleItem.product_id, Product.name, SaleItem.quantity)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(Sale.created_at >= cutoff)
        )

        sold_qty: dict[int, int] = defaultdict(int)
        names: dict[int, str] = {}
        for product_id, name, quantity in result.all():
            sold_qty[product_id] += quantity
            names[product_id] = name

        movement = [
            ProductMovementEntry(product_id=pid, name=names[pid], quantity_sold=qty)
            for pid, qty in sold_qty.items()
        ]
        movement.sort(key=lambda m: m.quantity_sold, reverse=True)

        all_products_result = await self.db.execute(
            select(Product.id, Product.name).where(Product.deleted_at.is_(None))
        )
        never_sold = [
            NeverSoldEntry(product_id=pid, name=name)
            for pid, name in all_products_result.all()
            if pid not in sold_qty
        ]

        return FastSlowMoversOut(
            period_days=days,
            fast_movers=movement[:limit],
            slow_movers=list(reversed(movement))[:limit],
            never_sold=never_sold,
        )

    async def receiving_discrepancies(self) -> ReceivingDiscrepancyReportOut:
        result = await self.db.execute(
            select(PurchaseOrderItem, Product.name)
            .join(Product, Product.id == PurchaseOrderItem.product_id)
            .where(PurchaseOrderItem.quantity_received.is_not(None))
        )

        entries = [
            ReceivingDiscrepancyEntry(
                purchase_order_id=item.purchase_order_id,
                item_id=item.id,
                product_id=item.product_id,
                product_name=product_name,
                quantity_ordered=item.quantity_ordered,
                quantity_received=item.quantity_received,
                variance=item.quantity_received - item.quantity_ordered,
            )
            for item, product_name in result.all()
            if item.quantity_received != item.quantity_ordered
        ]

        recommendation = (
            "No receiving discrepancies on record."
            if not entries
            else (
                f"{len(entries)} line(s) received a different quantity than ordered -- "
                "review supplier reliability for repeated short-shipments."
            )
        )

        return ReceivingDiscrepancyReportOut(entries=entries, recommendation=recommendation)

    async def stock_take_history(self) -> StockTakeHistoryOut:
        result = await self.db.execute(
            select(StockTake)
            .where(StockTake.status == StockTakeStatus.CLOSED)
            .order_by(StockTake.closed_at.desc())
        )
        stock_takes = result.scalars().all()

        entries = []
        for stock_take in stock_takes:
            shrinkage_value = 0.0
            expected_value = 0.0
            for item in stock_take.items:
                batch_result = await self.db.execute(
                    select(MedicineBatch).where(MedicineBatch.id == item.batch_id)
                )
                batch = batch_result.scalar_one_or_none()
                cost = batch.cost_price if batch else 0.0
                expected_value += item.expected_qty * cost
                if item.physical_qty is not None:
                    variance = item.physical_qty - item.expected_qty
                    if variance < 0:
                        shrinkage_value += abs(variance) * cost

            shrinkage_percent = (
                (shrinkage_value / expected_value * 100) if expected_value > 0 else 0.0
            )
            entries.append(
                StockTakeHistoryEntry(
                    stock_take_id=stock_take.id,
                    started_at=stock_take.started_at,
                    closed_at=stock_take.closed_at,
                    shrinkage_value=round(shrinkage_value, 2),
                    shrinkage_percent=round(shrinkage_percent, 2),
                )
            )

        return StockTakeHistoryOut(entries=entries)

    async def kpi_dashboard(
        self, start_date: date, end_date: date, include_profit: bool
    ) -> KpiDashboardOut:
        revenue, transaction_count = await self._revenue_and_count_in_range(start_date, end_date)
        average_basket = revenue / transaction_count if transaction_count > 0 else 0.0

        # Comparison period: immediately preceding, same length -- "this
        # week vs last week" regardless of what range was actually
        # selected, not a fixed lookback window that would compare
        # mismatched period lengths.
        period_days = (end_date - start_date).days + 1
        prior_end = start_date - timedelta(days=1)
        prior_start = prior_end - timedelta(days=period_days - 1)
        prior_revenue, _ = await self._revenue_and_count_in_range(prior_start, prior_end)
        revenue_change_percent = (
            ((revenue - prior_revenue) / prior_revenue * 100) if prior_revenue > 0 else None
        )

        profit: float | None = None
        profit_margin_percent: float | None = None
        if include_profit:
            profit_result = await self.profit_report(start_date, end_date)
            profit = profit_result.total_profit
            profit_margin_percent = profit_result.profit_margin_percent

        top_products = await self.top_products_by_revenue(start_date, end_date, limit=5)

        low_stock = await InventoryService(self.db).get_low_stock_products()
        expiring = await InventoryService(self.db).get_expiring_batches()

        return KpiDashboardOut(
            start_date=start_date,
            end_date=end_date,
            revenue=revenue,
            transaction_count=transaction_count,
            average_basket=round(average_basket, 2),
            revenue_change_percent=(
                round(revenue_change_percent, 2) if revenue_change_percent is not None else None
            ),
            profit=profit,
            profit_margin_percent=profit_margin_percent,
            top_products=top_products,
            low_stock_count=len(low_stock),
            expiring_soon_count=len(expiring),
        )

    async def _revenue_and_count_in_range(
        self, start_date: date, end_date: date
    ) -> tuple[float, int]:
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        result = await self.db.execute(
            select(func.coalesce(func.sum(Sale.total_amount), 0.0), func.count(Sale.id)).where(
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )
        )
        total_revenue, count = result.one()
        return float(total_revenue), int(count)

    async def top_products_by_revenue(
        self, start_date: date, end_date: date, limit: int
    ) -> list[TopProductEntry]:
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                func.sum(SaleItem.quantity).label("qty"),
                func.sum(SaleItem.quantity * SaleItem.unit_price).label("revenue"),
            )
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity * SaleItem.unit_price).desc())
            .limit(limit)
        )
        return [
            TopProductEntry(product_id=pid, name=name, quantity_sold=int(qty), revenue=revenue)
            for pid, name, qty, revenue in result.all()
        ]

    async def top_customers(
        self, start_date: date, end_date: date, limit: int = 20
    ) -> TopCustomersOut:
        """
        Ranked by real revenue from actual sales, with a running
        cumulative percentage -- the real Pareto question ("which
        customers make up 80% of revenue") is directly answerable from
        this, not left for someone to eyeball from a bar chart.
        Customer-less (walk-in, no name recorded) sales are
        deliberately excluded -- there's no real customer identity to
        rank there.
        """
        sales = await self._sales_in_range(start_date, end_date)
        sale_ids = [s.id for s in sales]
        if not sale_ids:
            return TopCustomersOut(entries=[], total_revenue=0.0)

        result = await self.db.execute(
            select(
                Customer.id,
                Customer.name,
                func.count(Sale.id).label("sale_count"),
                func.sum(Sale.total_amount).label("revenue"),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .where(Sale.id.in_(sale_ids))
            .group_by(Customer.id)
            .order_by(func.sum(Sale.total_amount).desc())
            .limit(limit)
        )
        rows = result.all()

        total_revenue = sum(s.total_amount for s in sales)
        entries = []
        running_total = 0.0
        for customer_id, name, sale_count, revenue in rows:
            running_total += revenue
            entries.append(
                TopCustomerEntry(
                    customer_id=customer_id,
                    name=name,
                    sale_count=sale_count,
                    revenue=revenue,
                    cumulative_percent=(
                        round(running_total / total_revenue * 100, 1) if total_revenue > 0 else 0.0
                    ),
                )
            )
        return TopCustomersOut(entries=entries, total_revenue=total_revenue)

    async def revenue_potential(self) -> RevenuePotentialOut:
        """
        A real hypothetical computed entirely from real current data:
        every batch's real qty_remaining and real cost_price, every
        product's real current selling price. Never a forecast of
        when this would happen or whether it will -- that depends on
        real customer demand this system has no way to know.
        """
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                Product.default_selling_price,
                func.coalesce(func.sum(MedicineBatch.qty_remaining), 0).label("qty"),
                func.coalesce(
                    func.sum(MedicineBatch.qty_remaining * MedicineBatch.cost_price), 0.0
                ).label("cost"),
            )
            .outerjoin(
                MedicineBatch,
                and_(
                    MedicineBatch.product_id == Product.id,
                    MedicineBatch.expiry_date >= date.today(),
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                ),
            )
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
        )

        by_product: list[RevenuePotentialEntry] = []
        total_revenue = 0.0
        total_cost = 0.0
        for product_id, name, selling_price, qty, cost in result.all():
            if qty <= 0:
                continue
            revenue = qty * selling_price
            gross_profit = revenue - cost
            total_revenue += revenue
            total_cost += cost
            by_product.append(
                RevenuePotentialEntry(
                    product_id=product_id,
                    name=name,
                    qty_on_hand=int(qty),
                    potential_revenue=revenue,
                    potential_cost=cost,
                    potential_gross_profit=gross_profit,
                )
            )
        by_product.sort(key=lambda e: e.potential_revenue, reverse=True)

        total_gross_profit = total_revenue - total_cost
        margin_percent = (total_gross_profit / total_revenue * 100) if total_revenue > 0 else None

        return RevenuePotentialOut(
            total_potential_revenue=total_revenue,
            total_potential_cost=total_cost,
            total_potential_gross_profit=total_gross_profit,
            overall_margin_percent=margin_percent,
            by_product=by_product,
            caveat=(
                "This is what selling every unit currently in stock at today's prices would "
                "add up to -- not a prediction of when that will happen or whether it will. "
                "Real sales depend on real demand, which this figure does not account for."
            ),
        )

    async def revenue_trend(
        self, start_date: date, end_date: date, include_profit: bool
    ) -> RevenueTrendOut:
        """
        Real SQL-side aggregation, never Python-side bucketing of
        individual sale rows -- correct regardless of how many years
        of sales have accumulated, not just at today's data volume.
        Granularity picks itself from the range length so a 4-year
        chart becomes ~48 monthly points, not 1,460 unreadable ones.

        Queried per DST-offset segment (see
        business_time.local_offset_segments) rather than as one pass
        with one offset -- Africa/Nairobi never has more than one
        segment here since it never observes DST, but a multi-month or
        multi-year chart for a DST-observing client routinely spans a
        transition, and revenue/transaction counts/cost are purely
        additive, so summing each segment's own correctly-bucketed
        results by period label is exact, not an approximation.
        """
        days = (end_date - start_date).days + 1
        granularity: Literal["day", "week", "month"]
        if days <= 31:
            granularity = "day"
        elif days <= 180:
            granularity = "week"
        else:
            granularity = "month"

        def bucket_expr_for(shifted_column: Any) -> Any:
            if granularity == "day":
                return func.date(shifted_column)
            if granularity == "week":
                return func.strftime("%Y-W%W", shifted_column)
            return func.strftime("%Y-%m", shifted_column)

        revenue_by_period: dict[str, float] = {}
        txn_count_by_period: dict[str, int] = {}
        cost_by_period: dict[str, float] = {}
        period_order: list[str] = []

        segments = await local_offset_segments(self.db, start_date, end_date)
        for segment_start, segment_end, offset_minutes in segments:
            shifted_column = func.datetime(Sale.created_at, f"{offset_minutes:+d} minutes")
            bucket_expr = bucket_expr_for(shifted_column)
            utc_start, utc_end = await local_day_bounds_utc(self.db, segment_start, segment_end)
            date_filter = (
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )

            revenue_result = await self.db.execute(
                select(
                    bucket_expr.label("period"),
                    func.coalesce(func.sum(Sale.total_amount), 0.0).label("revenue"),
                    func.count(Sale.id).label("txn_count"),
                )
                .where(*date_filter)
                .group_by("period")
                .order_by("period")
            )
            for row in revenue_result.all():
                if row.period not in revenue_by_period:
                    period_order.append(row.period)
                    revenue_by_period[row.period] = 0.0
                    txn_count_by_period[row.period] = 0
                revenue_by_period[row.period] += float(row.revenue)
                txn_count_by_period[row.period] += int(row.txn_count)

            if include_profit:
                cost_result = await self.db.execute(
                    select(
                        bucket_expr.label("period"),
                        func.coalesce(
                            func.sum(SaleItem.quantity * MedicineBatch.cost_price), 0.0
                        ).label("cost"),
                    )
                    .select_from(SaleItem)
                    .join(Sale, Sale.id == SaleItem.sale_id)
                    .join(MedicineBatch, MedicineBatch.id == SaleItem.batch_id)
                    .where(*date_filter)
                    .group_by("period")
                )
                for cost_row in cost_result.all():
                    cost_by_period[cost_row.period] = cost_by_period.get(
                        cost_row.period, 0.0
                    ) + float(cost_row.cost)

        period_order.sort()
        points = [
            RevenueTrendPoint(
                period_label=period,
                revenue=revenue_by_period[period],
                profit=(
                    round(revenue_by_period[period] - cost_by_period.get(period, 0.0), 2)
                    if include_profit
                    else None
                ),
                transaction_count=txn_count_by_period[period],
            )
            for period in period_order
        ]

        return RevenueTrendOut(granularity=granularity, points=points)

    async def stock_runway(self, lookback_days: int = 30) -> StockRunwayOut:
        """
        A transparent extrapolation, not a forecast: real units sold
        per product over the last `lookback_days`, divided into real
        current stock. A product with no sales in the window gets
        None for days_remaining -- there's no real rate to divide by,
        and guessing one would be exactly the kind of fabrication this
        feature exists to avoid.
        """
        window_start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=lookback_days)

        sold_result = await self.db.execute(
            select(SaleItem.product_id, func.sum(SaleItem.quantity))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(Sale.created_at >= window_start)
            .group_by(SaleItem.product_id)
        )
        sold_by_product: dict[int, int] = dict(sold_result.tuples().all())

        stock_result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                func.coalesce(func.sum(MedicineBatch.qty_remaining), 0).label("qty"),
            )
            .outerjoin(
                MedicineBatch,
                and_(
                    MedicineBatch.product_id == Product.id,
                    MedicineBatch.expiry_date >= date.today(),
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                ),
            )
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
        )

        entries: list[StockRunwayEntry] = []
        for product_id, name, qty in stock_result.all():
            if qty <= 0:
                continue
            units_sold = sold_by_product.get(product_id, 0)
            avg_daily = units_sold / lookback_days
            days_remaining = (qty / avg_daily) if avg_daily > 0 else None
            entries.append(
                StockRunwayEntry(
                    product_id=product_id,
                    name=name,
                    qty_on_hand=int(qty),
                    units_sold_in_window=units_sold,
                    avg_daily_sales=round(avg_daily, 2),
                    days_remaining=round(days_remaining, 1) if days_remaining is not None else None,
                )
            )
        # Soonest-to-run-out first -- products with no sales history
        # (None) sort last, since there's nothing urgent to flag there.
        entries.sort(key=lambda e: (e.days_remaining is None, e.days_remaining))

        return StockRunwayOut(
            lookback_days=lookback_days,
            entries=entries,
            caveat=(
                f"Based on real sales over the last {lookback_days} days, extrapolated forward "
                "at that same pace. Not a guarantee -- real demand changes (season, promotions, "
                "supplier issues) that this simple average does not account for."
            ),
        )

    async def _sales_in_range(self, start_date: date, end_date: date) -> list[Sale]:
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        result = await self.db.execute(
            select(Sale).where(
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )
        )
        return list(result.scalars().all())
