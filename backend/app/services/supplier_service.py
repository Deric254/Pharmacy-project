from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import local_day_bounds_utc
from app.models.audit_log import AuditLog
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.supplier import PaymentRecordRequest, SupplierCreate, SupplierKpiOut, SupplierOut


class SupplierService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: SupplierCreate, created_by: User) -> SupplierOut:
        supplier = Supplier(**payload.model_dump())
        self.db.add(supplier)
        await self.db.flush()
        self.db.add(
            AuditLog(
                user_id=created_by.id,
                user_name_snapshot=created_by.full_name,
                action="supplier.created",
                entity_type="supplier",
                entity_id=str(supplier.id),
                new_value=f"name={supplier.name}",
            )
        )
        await self.db.commit()
        await self.db.refresh(supplier)
        return await self._to_schema(supplier)

    async def list_all(self) -> list[SupplierOut]:
        result = await self.db.execute(select(Supplier).order_by(Supplier.name))
        return [await self._to_schema(s) for s in result.scalars().all()]

    async def get(self, supplier_id: int) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        return await self._to_schema(supplier)

    async def record_payment(
        self, supplier_id: int, payload: PaymentRecordRequest, recorded_by: User
    ) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        self.db.add(
            SupplierTransaction(
                supplier_id=supplier.id,
                amount=-payload.amount,  # negative: reduces what's owed
                reference="manual-payment",
                notes=payload.notes,
            )
        )
        # Real money leaving the business, recorded by hand -- same
        # audit non-negotiable as every other real-money event in this
        # codebase (purchase_order.received, sale.refunded).
        self.db.add(
            AuditLog(
                user_id=recorded_by.id,
                user_name_snapshot=recorded_by.full_name,
                action="supplier.payment_recorded",
                entity_type="supplier",
                entity_id=str(supplier.id),
                new_value=f"amount={payload.amount:.2f}"
                + (f" notes={payload.notes}" if payload.notes else ""),
            )
        )
        await self.db.commit()
        return await self._to_schema(supplier)

    async def kpis(self, start_date: date, end_date: date) -> SupplierKpiOut:
        """
        Simple accounts-payable KPIs, time-sliced. "Purchased" and
        "paid" are flows summed within [start_date, end_date] (what
        moved during the period); "net due" is a balance, so it's the
        running total across every supplier as of the END of that
        period, not something scoped to the range -- if end_date is
        today, this is exactly today's real outstanding balance.
        Uses the same signed SupplierTransaction ledger balance_owed
        is derived from (see the model's own docstring): positive
        amount = charged (goods received), negative = paid.
        """
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)

        purchased_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.amount > 0,
                SupplierTransaction.created_at >= utc_start,
                SupplierTransaction.created_at < utc_end,
            )
        )
        total_purchased = float(purchased_result.scalar_one())

        paid_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.amount < 0,
                SupplierTransaction.created_at >= utc_start,
                SupplierTransaction.created_at < utc_end,
            )
        )
        # Stored negative (a payment reduces what's owed) -- flipped to
        # a positive "amount paid" for the KPI card.
        total_paid = -float(paid_result.scalar_one())

        net_due_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.created_at < utc_end,
            )
        )
        net_due = float(net_due_result.scalar_one())

        active_count_result = await self.db.execute(
            select(func.count(func.distinct(SupplierTransaction.supplier_id))).where(
                SupplierTransaction.created_at >= utc_start,
                SupplierTransaction.created_at < utc_end,
            )
        )
        active_supplier_count = int(active_count_result.scalar_one())

        return SupplierKpiOut(
            start_date=start_date,
            end_date=end_date,
            total_purchased=round(total_purchased, 2),
            total_paid=round(total_paid, 2),
            net_due=round(net_due, 2),
            active_supplier_count=active_supplier_count,
        )

    async def _get_or_404(self, supplier_id: int) -> Supplier:
        result = await self.db.execute(select(Supplier).where(Supplier.id == supplier_id))
        supplier = result.scalar_one_or_none()
        if supplier is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return supplier

    async def _to_schema(self, supplier: Supplier) -> SupplierOut:
        balance_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.supplier_id == supplier.id
            )
        )
        balance = float(balance_result.scalar_one())
        out = SupplierOut.model_validate(supplier)
        out.balance_owed = balance
        return out
