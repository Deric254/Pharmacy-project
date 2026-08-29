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
    business_today,
    get_business_timezone,
    local_day_bounds_utc,
    local_offset_segments,
)
from app.models.customer import Customer
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrderItem
from app.models.refund import Refund, RefundItem
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
        # Refunds are fetched by their OWN created_at, not the created_at
        # of the sale they're against -- a refund processed today against
        # last week's sale is real money leaving the till TODAY, not a
        # rewrite of last week's already-closed total. Bucketed and netted
        # against revenue below so a refunded sale doesn't leave stale
        # money sitting in whatever period it reports as "revenue".
        refunds = await self._refunds_in_range(start_date, end_date)
        tz = await get_business_timezone(self.db)

        def bucket_key(dt: datetime) -> str:
            # created_at is stored UTC; convert to the business's own
            # local time via astimezone(), which resolves DST using
            # THIS row's own date -- not a single offset computed once
            # for the whole request -- or a row made early in the
            # local day would still group under the UTC day before.
            local_dt = dt.replace(tzinfo=UTC).astimezone(tz)
            return (
                local_dt.strftime("%Y-%m-%d") if group_by == "day" else local_dt.strftime("%Y-%m")
            )

        revenue_by_period: dict[str, float] = defaultdict(float)
        discount_by_period: dict[str, float] = defaultdict(float)
        count_by_period: dict[str, int] = defaultdict(int)

        for sale in sales:
            key = bucket_key(sale.created_at)
            revenue_by_period[key] += sale.total_amount
            discount_by_period[key] += sale.discount_amount
            count_by_period[key] += 1

        for refund in refunds:
            # Netted into revenue only -- sale_count and discount stay
            # tied to actual sales made, a refund isn't a sale.
            revenue_by_period[bucket_key(refund.created_at)] -= refund.total_amount

        entries = [
            SalesSummaryEntry(
                period=period,
                sale_count=count_by_period.get(period, 0),
                total_revenue=revenue_by_period[period],
                total_discount=discount_by_period.get(period, 0.0),
            )
            for period in sorted(revenue_by_period.keys())
        ]

        return SalesSummaryOut(
            entries=entries,
            total_revenue=sum(e.total_revenue for e in entries),
            total_sale_count=sum(e.sale_count for e in entries),
        )

    async def profit_report(self, start_date: date, end_date: date) -> ProfitReportOut:
        total_revenue, _ = await self._revenue_and_count_in_range(start_date, end_date)
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)

        # SaleItem.unit_cost is frozen at the moment of sale (see that
        # column's own comment) -- never a live join to
        # MedicineBatch.cost_price, which would let a batch's cost
        # changing later (a correction, or anything else) silently
        # change what an already-closed period's profit shows the next
        # time this report runs.
        cost_result = await self.db.execute(
            select(func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0.0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.created_at >= utc_start,
                Sale.created_at < utc_end,
            )
        )
        total_cost = float(cost_result.scalar_one())

        # A unit the customer returned that went back onto the shelf
        # (RefundItem.restocked) is no longer actually sold -- its cost
        # must come back out of COGS for the period the REFUND happened
        # in, or this report keeps charging cost for stock that is
        # physically sitting back in inventory. A non-restocked refund
        # (damaged/expired) keeps its original cost as a real loss --
        # nothing to reverse there, same restocked flag refund_service.py
        # already uses to decide whether to touch qty_remaining at all.
        # Reversed using the ORIGINAL sale item's frozen unit_cost (via
        # RefundItem.sale_item_id), not the batch's current cost -- a
        # refund must back out exactly what the sale it's reversing
        # actually recorded, nothing else.
        cost_reversal_result = await self.db.execute(
            select(func.coalesce(func.sum(RefundItem.quantity * SaleItem.unit_cost), 0.0))
            .join(SaleItem, SaleItem.id == RefundItem.sale_item_id)
            .join(Refund, Refund.id == RefundItem.refund_id)
            .where(
                Refund.created_at >= utc_start,
                Refund.created_at < utc_end,
                RefundItem.restocked.is_(True),
            )
        )
        total_cost -= float(cost_reversal_result.scalar_one())

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
        today = await business_today(self.db)
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
                # unit_cost_at_close is frozen at the moment this stock
                # take closed (see that column's own comment) -- never
                # a live read of batch.cost_price, which would let a
                # cost correction made afterward silently change what
                # an already-closed stock take's shrinkage shows the
                # next time this report is viewed. Falls back to the
                # batch's current cost only for a pre-migration row
                # that predates this column existing.
                cost = item.unit_cost_at_close
                if cost is None:
                    cost = item.batch.cost_price if item.batch else 0.0
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
        # Net of refunds processed in this same window -- see
        # sales_summary's comment on why a refund is counted against
        # the period it actually happened in, not the original sale's
        # period. transaction_count is left alone: a refund isn't a
        # sale, it doesn't undo the fact that a transaction occurred.
        refund_result = await self.db.execute(
            select(func.coalesce(func.sum(Refund.total_amount), 0.0)).where(
                Refund.created_at >= utc_start,
                Refund.created_at < utc_end,
            )
        )
        total_refunds = refund_result.scalar_one()
        net_revenue = float(total_revenue) - float(total_refunds)
        return net_revenue, int(count)

    async def top_products_by_revenue(
        self, start_date: date, end_date: date, limit: int
    ) -> list[TopProductEntry]:
        # SaleItem.unit_price is always the FULL, undiscounted price --
        # a sale's discount lives only once, on the Sale header
        # (Sale.discount_amount), and is never split across its line
        # items at write time (see sale_service.py). Ranking directly
        # by SUM(quantity * unit_price), as this used to, therefore
        # overstated every discounted sale's contribution -- product
        # revenue here didn't add up to the real money the business
        # actually took in, while top_customers() and the KPI/PDF
        # revenue totals (both keyed off Sale.total_amount) did. This
        # prorates each sale's discount across its own line items in
        # proportion to their share of that sale's subtotal, so a
        # product's revenue here is its real, after-discount share --
        # consistent with every other report reading from this file.
        #
        # That proration is inherently a per-line computation (the
        # ratio differs sale by sale), so it can't be pushed into a
        # single SQL GROUP BY the way a plain SUM() can. Sale.subtotal
        # and Sale.total_amount are read here as plain typed columns
        # (not divided in SQL), which is what keeps MoneyCents'
        # cents<->dollars conversion exact -- dividing two MoneyCents
        # columns directly in SQLite would divide their raw stored
        # integer cents, not their dollar values, and (being integer
        # division) would silently truncate every ratio below 1 to 0.
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        sale_totals = (
            select(Sale.id, Sale.subtotal, Sale.total_amount)
            .where(Sale.created_at >= utc_start, Sale.created_at < utc_end)
            .subquery()
        )
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                SaleItem.quantity,
                SaleItem.unit_price,
                sale_totals.c.subtotal,
                sale_totals.c.total_amount,
            )
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(sale_totals, sale_totals.c.id == SaleItem.sale_id)
        )

        name_by_product: dict[int, str] = {}
        qty_by_product: dict[int, int] = defaultdict(int)
        revenue_by_product: dict[int, float] = defaultdict(float)
        for product_id, name, quantity, unit_price, subtotal, total_amount in result.all():
            name_by_product[product_id] = name
            qty_by_product[product_id] += quantity
            # A sale with a zero subtotal (every line free) has
            # nothing to prorate a discount against -- keep that
            # line at face value rather than dividing by zero.
            discount_ratio = (total_amount / subtotal) if subtotal else 1.0
            revenue_by_product[product_id] += quantity * unit_price * discount_ratio

        # RefundItem.line_total is already the real, discount-prorated
        # money handed back on that line (see refund_service.py) -- no
        # re-derivation needed here, just netted against this same
        # product's revenue for the period the refund happened in, same
        # "refund counts against its own date" rule as every other
        # report in this file.
        refund_result = await self.db.execute(
            select(
                RefundItem.product_id,
                Product.name,
                func.coalesce(func.sum(RefundItem.line_total), 0.0),
            )
            .join(Refund, Refund.id == RefundItem.refund_id)
            .join(Product, Product.id == RefundItem.product_id)
            .where(Refund.created_at >= utc_start, Refund.created_at < utc_end)
            .group_by(RefundItem.product_id)
        )
        for product_id, name, refund_total in refund_result.all():
            name_by_product.setdefault(product_id, name)
            revenue_by_product[product_id] -= float(refund_total)

        ranked = sorted(revenue_by_product.items(), key=lambda pair: pair[1], reverse=True)
        return [
            TopProductEntry(
                product_id=product_id,
                name=name_by_product[product_id],
                quantity_sold=qty_by_product[product_id],
                revenue=round(revenue, 2),
            )
            for product_id, revenue in ranked[:limit]
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
        )
        rows = result.all()

        # Refunds against this customer's sales, counted against the
        # period the refund itself happened in (it may be a different
        # period than the sale it's against) -- same rule every other
        # revenue figure in this file follows. Not restricted to
        # sale_ids: a refund processed in this window against an OLDER
        # sale is still real money leaving in this window.
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        refund_result = await self.db.execute(
            select(Sale.customer_id, func.coalesce(func.sum(Refund.total_amount), 0.0))
            .join(Refund, Refund.sale_id == Sale.id)
            .where(
                Refund.created_at >= utc_start,
                Refund.created_at < utc_end,
                Sale.customer_id.is_not(None),
            )
            .group_by(Sale.customer_id)
        )
        refund_by_customer = {
            customer_id: float(total) for customer_id, total in refund_result.all()
        }

        total_revenue = sum(s.total_amount for s in sales) - sum(refund_by_customer.values())
        net_rows = sorted(
            (
                (customer_id, name, sale_count, revenue - refund_by_customer.get(customer_id, 0.0))
                for customer_id, name, sale_count, revenue in rows
            ),
            key=lambda row: row[3],
            reverse=True,
        )[:limit]

        entries = []
        running_total = 0.0
        for customer_id, name, sale_count, net_revenue in net_rows:
            running_total += net_revenue
            entries.append(
                TopCustomerEntry(
                    customer_id=customer_id,
                    name=name,
                    sale_count=sale_count,
                    revenue=net_revenue,
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
        today = await business_today(self.db)
        result = await self.db.execute(
            select(
                Product.id,
                Product.name,
                func.coalesce(func.sum(MedicineBatch.qty_remaining), 0).label("qty"),
                func.coalesce(
                    func.sum(MedicineBatch.qty_remaining * MedicineBatch.cost_price), 0.0
                ).label("cost"),
                func.coalesce(
                    func.sum(
                        MedicineBatch.qty_remaining
                        * func.coalesce(MedicineBatch.selling_price, Product.default_selling_price)
                    ),
                    0.0,
                ).label("revenue"),
            )
            .outerjoin(
                MedicineBatch,
                and_(
                    MedicineBatch.product_id == Product.id,
                    MedicineBatch.expiry_date >= today,
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                ),
            )
            .where(Product.deleted_at.is_(None))
            .group_by(Product.id)
        )

        by_product: list[RevenuePotentialEntry] = []
        total_revenue = 0.0
        total_cost = 0.0
        for product_id, name, qty, cost, revenue in result.all():
            if qty <= 0:
                continue
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
                # SaleItem.unit_cost is frozen at sale time -- see
                # profit_report's identical comment for why this must
                # never join live to MedicineBatch.cost_price.
                cost_result = await self.db.execute(
                    select(
                        bucket_expr.label("period"),
                        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0.0).label(
                            "cost"
                        ),
                    )
                    .select_from(SaleItem)
                    .join(Sale, Sale.id == SaleItem.sale_id)
                    .where(*date_filter)
                    .group_by("period")
                )
                for cost_row in cost_result.all():
                    cost_by_period[cost_row.period] = cost_by_period.get(
                        cost_row.period, 0.0
                    ) + float(cost_row.cost)

            # Refunds bucketed by their OWN created_at (shifted through
            # this same segment's offset), not the created_at of the
            # sale they're against -- same "a refund counts against the
            # period it actually happened in" rule as every other report
            # in this file. Without this, a chart could show a sale's
            # full revenue in one period with nothing ever backing it
            # out, even after the money was actually returned.
            refund_shifted_column = func.datetime(Refund.created_at, f"{offset_minutes:+d} minutes")
            refund_bucket_expr = bucket_expr_for(refund_shifted_column)
            refund_date_filter = (
                Refund.created_at >= utc_start,
                Refund.created_at < utc_end,
            )
            refund_result = await self.db.execute(
                select(
                    refund_bucket_expr.label("period"),
                    func.coalesce(func.sum(Refund.total_amount), 0.0).label("refund_total"),
                )
                .where(*refund_date_filter)
                .group_by("period")
            )
            for refund_row in refund_result.all():
                if refund_row.period not in revenue_by_period:
                    period_order.append(refund_row.period)
                    revenue_by_period[refund_row.period] = 0.0
                    txn_count_by_period[refund_row.period] = 0
                revenue_by_period[refund_row.period] -= float(refund_row.refund_total)

            if include_profit:
                # Restocked refund lines put cost back into inventory --
                # same reversal profit_report() applies (reversed using
                # the original sale item's frozen unit_cost, not the
                # batch's current cost), bucketed the same way as the
                # cost/revenue queries above.
                cost_reversal_result = await self.db.execute(
                    select(
                        refund_bucket_expr.label("period"),
                        func.coalesce(
                            func.sum(RefundItem.quantity * SaleItem.unit_cost), 0.0
                        ).label("cost_reversal"),
                    )
                    .select_from(RefundItem)
                    .join(Refund, Refund.id == RefundItem.refund_id)
                    .join(SaleItem, SaleItem.id == RefundItem.sale_item_id)
                    .where(*refund_date_filter, RefundItem.restocked.is_(True))
                    .group_by("period")
                )
                for reversal_row in cost_reversal_result.all():
                    cost_by_period[reversal_row.period] = cost_by_period.get(
                        reversal_row.period, 0.0
                    ) - float(reversal_row.cost_reversal)

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
        today = await business_today(self.db)

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
                    MedicineBatch.expiry_date >= today,
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

    async def _refunds_in_range(self, start_date: date, end_date: date) -> list[Refund]:
        # Filtered by the refund's OWN created_at, not the created_at of
        # the sale it's against -- see sales_summary's comment for why.
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)
        result = await self.db.execute(
            select(Refund).where(
                Refund.created_at >= utc_start,
                Refund.created_at < utc_end,
            )
        )
        return list(result.scalars().all())
