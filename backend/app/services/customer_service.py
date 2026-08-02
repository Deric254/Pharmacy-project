"""
Customer service.

Purchase history is a query against `sales`, not a duplicated copy --
same principle as everywhere else in this codebase: one source of
truth, queried, never mirrored into a second table that could drift.

award_loyalty_points() follows the same pattern as
check_and_publish_low_stock() in the Inventory module: called by
SaleService after a sale commits, best-effort, never able to block or
roll back a completed sale.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_config import BusinessConfig
from app.models.customer import Customer
from app.models.sale import Sale
from app.schemas.customer import (
    CustomerCreate,
    CustomerLifetimeValueEntry,
    CustomerLifetimeValueOut,
    CustomerOut,
    PurchaseHistoryEntryOut,
)


class CustomerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: CustomerCreate) -> CustomerOut:
        if payload.phone:
            existing = await self.db.execute(
                select(Customer).where(Customer.phone == payload.phone)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Phone number already registered")

        customer = Customer(**payload.model_dump())
        self.db.add(customer)
        await self.db.commit()
        await self.db.refresh(customer)
        return CustomerOut.model_validate(customer)

    async def list_all(self, search: str | None = None) -> list[CustomerOut]:
        query = select(Customer)
        if search:
            query = query.where((Customer.name.ilike(f"%{search}%")) | (Customer.phone == search))
        result = await self.db.execute(query.order_by(Customer.name))
        return [CustomerOut.model_validate(c) for c in result.scalars().all()]

    async def lifetime_value(self) -> CustomerLifetimeValueOut:
        """
        Real total spend per customer, computed directly from actual
        sales -- never a stored, cacheable figure that could drift
        from reality as new sales happen. Customers with zero
        purchases are excluded entirely from both the list and the
        average, rather than padding it with real customers who
        simply haven't bought anything yet.
        """
        result = await self.db.execute(
            select(
                Customer.id,
                Customer.name,
                Customer.phone,
                func.sum(Sale.total_amount).label("lifetime_value"),
                func.count(Sale.id).label("sale_count"),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .group_by(Customer.id)
            .order_by(func.sum(Sale.total_amount).desc())
        )
        rows = result.all()

        entries = [
            CustomerLifetimeValueEntry(
                customer_id=row.id,
                name=row.name,
                phone=row.phone,
                lifetime_value=float(row.lifetime_value),
                sale_count=int(row.sale_count),
            )
            for row in rows
        ]
        average = sum(e.lifetime_value for e in entries) / len(entries) if entries else None
        return CustomerLifetimeValueOut(entries=entries, average_lifetime_value=average)

    async def get(self, customer_id: int) -> CustomerOut:
        customer = await self._get_or_404(customer_id)
        return CustomerOut.model_validate(customer)

    async def get_by_phone(self, phone: str) -> CustomerOut:
        result = await self.db.execute(select(Customer).where(Customer.phone == phone))
        customer = result.scalar_one_or_none()
        if customer is None:
            raise HTTPException(status_code=404, detail="No customer found with that phone number")
        return CustomerOut.model_validate(customer)

    async def get_purchase_history(self, customer_id: int) -> list[PurchaseHistoryEntryOut]:
        await self._get_or_404(customer_id)  # 404 if customer doesn't exist at all
        result = await self.db.execute(
            select(Sale).where(Sale.customer_id == customer_id).order_by(Sale.created_at.desc())
        )
        return [
            PurchaseHistoryEntryOut(
                sale_id=sale.id, total_amount=sale.total_amount, created_at=sale.created_at
            )
            for sale in result.scalars().all()
        ]

    async def _get_or_404(self, customer_id: int) -> Customer:
        result = await self.db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return customer


async def award_loyalty_points(
    db: AsyncSession, customer_id: int | None, sale_total: float
) -> None:
    """
    Called after a sale commits, only if a customer was attached to
    it. Reads the Config Panel's loyalty toggle/rate live rather than
    caching it here, since this runs rarely enough (once per sale with
    a customer attached) that a config lookup is cheap and always
    reflects the current setting immediately if the owner changes it.
    """
    if customer_id is None:
        return

    config_result = await db.execute(select(BusinessConfig).where(BusinessConfig.id == 1))
    config = config_result.scalar_one_or_none()
    if config is None or not config.loyalty_program_enabled:
        return

    points_earned = int(sale_total * config.loyalty_points_per_currency_unit)
    if points_earned <= 0:
        return

    customer_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        return  # customer_id was valid at sale time; defensive no-op if since deleted

    # Real guarantee against two sales attaching the same customer
    # near-simultaneously silently losing one sale's worth of points --
    # the same class of bug already found and fixed for stock
    # decrements, PO transitions, refund restocks, and stock-take
    # closes this session. A plain `customer.loyalty_points +=` here
    # would have the exact same lost-update risk.
    cast(
        "CursorResult[Any]",
        await db.execute(
            update(Customer)
            .where(Customer.id == customer_id)
            .values(loyalty_points=Customer.loyalty_points + points_earned)
        ),
    )
    await db.commit()
