"""
Report service.

Every report here reads from the same ledger/transactional tables the
operational modules write to (sales, sale_items, medicine_batches,
purchase_order_items, stock_takes) -- no separate analytics database or
ETL step, which would be over-engineering at this scale and would risk
the reports drifting from reality. Date-range grouping is done in
Python rather than DB-specific date functions (MySQL's DATE_FORMAT vs
SQLite's strftime differ), keeping this portable across both engines
without a dialect-specific branch, consistent with lessons learned
earlier in this project about MySQL/SQLite divergence.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrderItem
from app.models.sale import Sale, SaleItem
from app.models.stock_take import StockTake, StockTakeStatus
from app.schemas.reports import (
    ExpiredStockEntry,
    ExpiredStockReportOut,
    FastSlowMoversOut,
    NeverSoldEntry,
    ProductMovementEntry,
    ProfitReportOut,
    ReceivingDiscrepancyEntry,
    ReceivingDiscrepancyReportOut,
    SalesSummaryEntry,
    SalesSummaryOut,
    StockTakeHistoryEntry,
    StockTakeHistoryOut,
)


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sales_summary(
        self, start_date: date, end_date: date, group_by: str = "day"
    ) -> SalesSummaryOut:
        sales = await self._sales_in_range(start_date, end_date)

        buckets: dict[str, list[Sale]] = defaultdict(list)
        for sale in sales:
            key = (
                sale.created_at.strftime("%Y-%m-%d")
                if group_by == "day"
                else sale.created_at.strftime("%Y-%m")
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
        sales = await self._sales_in_range(start_date, end_date)
        sale_ids = [s.id for s in sales]

        total_revenue = sum(s.total_amount for s in sales)
        total_cost = 0.0

        if sale_ids:
            result = await self.db.execute(
                select(SaleItem, MedicineBatch.cost_price)
                .join(MedicineBatch, MedicineBatch.id == SaleItem.batch_id)
                .where(SaleItem.sale_id.in_(sale_ids))
            )
            for sale_item, cost_price in result.all():
                total_cost += sale_item.quantity * cost_price

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
        cutoff = datetime.now() - timedelta(days=days)

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

    async def _sales_in_range(self, start_date: date, end_date: date) -> list[Sale]:
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        result = await self.db.execute(
            select(Sale).where(Sale.created_at >= start_dt, Sale.created_at <= end_dt)
        )
        return list(result.scalars().all())
