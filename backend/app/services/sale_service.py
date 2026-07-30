"""
Sale service.

The entire checkout is one DB transaction: sale header, sale items
(one per FEFO batch allocation), stock ledger entries, and payment
rows either all commit together or none do. If any product line can't
be fully covered by available stock, the whole sale is rolled back --
no partial sales.

Side effects (receipt printing, dashboard updates, low-stock checks)
are NOT done here -- this service commits the transaction and
publishes `sale.completed`; everything else subscribes to that event
and reacts independently, so checkout itself stays fast.
"""

from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SaleCompletedEvent, publish
from app.models.customer import Customer
from app.models.product import Product
from app.models.sale import Payment, Sale, SaleItem
from app.models.stock_movement import MovementType
from app.models.user import User
from app.schemas.sale import SaleCreate, SaleListItemOut, SaleOut, SalePage
from app.services.customer_service import award_loyalty_points
from app.services.inventory_service import check_and_publish_low_stock
from app.services.stock_selection_service import (
    Allocation,
    InsufficientStockError,
    apply_allocations,
    select_batches_fefo,
)


class SaleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_sale(self, payload: SaleCreate, cashier: User) -> SaleOut:
        try:
            products_by_id = await self._load_active_products(
                [item.product_id for item in payload.items]
            )
            if payload.customer_id is not None:
                await self._validate_customer_exists(payload.customer_id)

            subtotal = 0.0
            all_allocations: list[tuple[int, Allocation]] = []  # (product_id, allocation)

            for item in payload.items:
                product = products_by_id[item.product_id]
                unit_price = product.default_selling_price
                subtotal += unit_price * item.quantity

                allocations = await select_batches_fefo(
                    self.db, item.product_id, item.quantity, lock=True
                )
                for batch, _qty in allocations:
                    if unit_price < batch.cost_price:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f'"{product.name}" would sell at a loss: selling price '
                                f"{unit_price:.2f} is below this stock's cost "
                                f"{batch.cost_price:.2f}. Raise the selling price or adjust "
                                "the batch cost before selling this line."
                            ),
                        )
                for allocation in allocations:
                    all_allocations.append((item.product_id, allocation))

            total_amount = subtotal - payload.discount_amount
            if payload.discount_amount > subtotal:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Discount ({payload.discount_amount:.2f}) cannot exceed "
                        f"the subtotal ({subtotal:.2f})."
                    ),
                )
            self._validate_payment_total(payload, total_amount)

            sale = Sale(
                cashier_user_id=cashier.id,
                customer_id=payload.customer_id,
                subtotal=subtotal,
                discount_amount=payload.discount_amount,
                total_amount=total_amount,
            )
            self.db.add(sale)
            await self.db.flush()  # assigns sale.id without ending the transaction

            for product_id, (batch, qty) in all_allocations:
                unit_price = products_by_id[product_id].default_selling_price
                self.db.add(
                    SaleItem(
                        sale_id=sale.id,
                        product_id=product_id,
                        batch_id=batch.id,
                        quantity=qty,
                        unit_price=unit_price,
                        line_total=unit_price * qty,
                    )
                )

            await apply_allocations(
                self.db,
                [allocation for _, allocation in all_allocations],
                movement_type=MovementType.SALE,
                created_by_user_id=cashier.id,
                reference=f"sale:{sale.id}",
            )

            for payment in payload.payments:
                self.db.add(
                    Payment(
                        sale_id=sale.id,
                        method=payment.method,
                        amount=payment.amount,
                        reference=payment.reference,
                    )
                )

            await self.db.commit()
        except InsufficientStockError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Insufficient stock for product {exc.product_id}: "
                    f"requested {exc.requested}, only {exc.available} available"
                ),
            ) from exc
        except HTTPException:
            await self.db.rollback()
            raise

        await self.db.refresh(sale, attribute_names=["items", "payments", "created_at"])

        await publish(
            SaleCompletedEvent(
                sale_id=sale.id, customer_id=sale.customer_id, total_amount=f"{total_amount:.2f}"
            )
        )
        # Checked after commit, per product actually sold -- not the
        # whole catalog -- so this stays cheap and never blocks or
        # risks rolling back a completed sale if it fails for any reason.
        await check_and_publish_low_stock(self.db, list(products_by_id.keys()))
        await award_loyalty_points(self.db, sale.customer_id, total_amount)

        return SaleOut.model_validate(sale)

    async def get_sale(self, sale_id: int) -> SaleOut:
        result = await self.db.execute(select(Sale).where(Sale.id == sale_id))
        sale = result.scalar_one_or_none()
        if sale is None:
            raise HTTPException(status_code=404, detail="Sale not found")
        return SaleOut.model_validate(sale)

    async def list_sales(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SalePage:
        limit = min(limit, 200)

        query = (
            select(
                Sale.id,
                User.full_name.label("cashier_name"),
                Customer.name.label("customer_name"),
                Sale.total_amount,
                Sale.created_at,
                func.count(SaleItem.id).label("item_count"),
            )
            .join(User, User.id == Sale.cashier_user_id)
            .outerjoin(Customer, Customer.id == Sale.customer_id)
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .group_by(Sale.id, User.full_name, Customer.name, Sale.total_amount, Sale.created_at)
            .order_by(Sale.created_at.desc(), Sale.id.desc())
        )
        count_query = select(func.count()).select_from(Sale)

        if start_date is not None:
            start_dt = datetime.combine(start_date, datetime.min.time())
            query = query.where(Sale.created_at >= start_dt)
            count_query = count_query.where(Sale.created_at >= start_dt)
        if end_date is not None:
            end_dt = datetime.combine(end_date, datetime.max.time())
            query = query.where(Sale.created_at <= end_dt)
            count_query = count_query.where(Sale.created_at <= end_dt)

        total = await self.db.scalar(count_query) or 0
        result = await self.db.execute(query.limit(limit).offset(offset))

        entries = [
            SaleListItemOut(
                id=row.id,
                cashier_name=row.cashier_name,
                customer_name=row.customer_name,
                item_count=row.item_count,
                total_amount=row.total_amount,
                created_at=row.created_at,
            )
            for row in result.all()
        ]
        return SalePage(entries=entries, total=total, limit=limit, offset=offset)

    async def _load_active_products(self, product_ids: list[int]) -> dict[int, Product]:
        result = await self.db.execute(
            select(Product).where(Product.id.in_(product_ids), Product.deleted_at.is_(None))
        )
        products = {p.id: p for p in result.scalars().all()}
        missing = set(product_ids) - set(products.keys())
        if missing:
            raise HTTPException(status_code=404, detail=f"Product(s) not found: {sorted(missing)}")
        return products

    async def _validate_customer_exists(self, customer_id: int) -> None:
        result = await self.db.execute(select(Customer).where(Customer.id == customer_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    @staticmethod
    def _validate_payment_total(payload: SaleCreate, total_amount: float) -> None:
        paid = sum(p.amount for p in payload.payments)
        # Small epsilon for float rounding, never exact equality on money math.
        if abs(paid - total_amount) > 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"Payment total ({paid:.2f}) does not match sale total ({total_amount:.2f})",
            )
