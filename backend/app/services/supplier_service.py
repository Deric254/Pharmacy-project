from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import local_day_bounds_utc
from app.core.money_types import from_cents, to_cents
from app.models.audit_log import AuditLog
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.supplier import (
    PaymentRecordRequest,
    PaymentReverseRequest,
    SupplierCreate,
    SupplierKpiOut,
    SupplierOut,
    SupplierPaymentOut,
)

# The four kinds of row in a supplier's ledger. The sign alone is not enough:
# a reversal carries the opposite sign of the row it cancels and points at it
# through reverses_transaction_id.
_CHARGE = and_(
    SupplierTransaction.amount > 0, SupplierTransaction.reverses_transaction_id.is_(None)
)
_PURCHASE_REVERSAL = and_(
    SupplierTransaction.amount < 0, SupplierTransaction.reverses_transaction_id.is_not(None)
)
_PAYMENT = and_(
    SupplierTransaction.amount < 0, SupplierTransaction.reverses_transaction_id.is_(None)
)
_PAYMENT_REVERSAL = and_(
    SupplierTransaction.amount > 0, SupplierTransaction.reverses_transaction_id.is_not(None)
)


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
        supplier_name = supplier.name  # a rollback below expires the ORM object
        amount_cents = to_cents(payload.amount)

        if payload.idempotency_key is not None:
            replay = await self._replay_if_already_recorded(supplier, payload, amount_cents)
            if replay is not None:
                return replay

        # ONE atomic statement: the row is inserted only if, at the very
        # moment it runs, the supplier's ledger still shows at least this
        # much owed. Checking the balance in one query and inserting in
        # another would let two simultaneous payments both pass the check
        # and together overpay. Same pattern as the refund guard
        # (`UPDATE ... WHERE qty_refunded + :n <= quantity`).
        # Amounts are whole cents here -- the column's own storage unit --
        # so nothing is converted or rounded on the way in.
        try:
            result = await self.db.execute(
                text(
                    "INSERT INTO supplier_transactions "
                    "(supplier_id, amount, reference, notes, idempotency_key) "
                    "SELECT :supplier_id, :negative_cents, 'manual-payment', :notes, :key "
                    "WHERE (SELECT COALESCE(SUM(amount), 0) FROM supplier_transactions "
                    "WHERE supplier_id = :supplier_id) >= :cents"
                ),
                {
                    "supplier_id": supplier.id,
                    "negative_cents": -amount_cents,
                    "cents": amount_cents,
                    "notes": payload.notes,
                    "key": payload.idempotency_key,
                },
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                await self.db.rollback()
                owed = max(await self._balance_cents(supplier_id), 0)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Payment exceeds the amount owed to {supplier_name} "
                        f"({from_cents(owed):.2f})."
                    ),
                )
            # Real money leaving the business, recorded by hand -- same
            # audit non-negotiable as every other real-money event in this
            # codebase (purchase_order.received, sale.refunded). Written in
            # the same transaction as the payment, so neither can exist
            # without the other.
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
        except IntegrityError:
            # Two requests with the same idempotency key raced past the
            # replay check above; the unique index let exactly one in.
            await self.db.rollback()
            supplier = await self._get_or_404(supplier_id)
            replay = await self._replay_if_already_recorded(supplier, payload, amount_cents)
            if replay is None:
                raise
            return replay
        return await self._to_schema(supplier)

    async def _replay_if_already_recorded(
        self, supplier: Supplier, payload: PaymentRecordRequest, amount_cents: int
    ) -> SupplierOut | None:
        """
        If this idempotency key was already used, answer with the current
        state instead of paying again. A key reused for a DIFFERENT
        supplier or amount is a client bug, never a retry: refuse it.
        """
        existing = (
            await self.db.execute(
                select(SupplierTransaction).where(
                    SupplierTransaction.idempotency_key == payload.idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return None
        if existing.supplier_id != supplier.id or to_cents(existing.amount) != -amount_cents:
            raise HTTPException(
                status_code=409,
                detail="This request key was already used for a different payment.",
            )
        return await self._to_schema(supplier)

    async def list_payments(self, supplier_id: int) -> list[SupplierPaymentOut]:
        """A supplier's payments, newest first, each marked if reversed."""
        supplier = await self._get_or_404(supplier_id)
        rows = (
            (
                await self.db.execute(
                    select(SupplierTransaction)
                    .where(SupplierTransaction.supplier_id == supplier.id, _PAYMENT)
                    .order_by(SupplierTransaction.id.desc())
                )
            )
            .scalars()
            .all()
        )
        reversals = {
            r.reverses_transaction_id: r
            for r in (
                await self.db.execute(
                    select(SupplierTransaction).where(
                        SupplierTransaction.supplier_id == supplier.id, _PAYMENT_REVERSAL
                    )
                )
            )
            .scalars()
            .all()
        }
        return [
            SupplierPaymentOut(
                id=t.id,
                amount=-t.amount,
                notes=t.notes,
                created_at=t.created_at,
                reversed=t.id in reversals,
                reversal_reason=reversals[t.id].notes if t.id in reversals else None,
            )
            for t in rows
        ]

    async def reverse_payment(
        self,
        supplier_id: int,
        transaction_id: int,
        payload: PaymentReverseRequest,
        reversed_by: User,
    ) -> SupplierOut:
        """
        Cancel a recorded payment by appending an opposite entry. The
        original row is never edited or deleted, so the history of what
        happened -- including the mistake -- stays complete.
        """
        supplier = await self._get_or_404(supplier_id)
        original = (
            await self.db.execute(
                select(SupplierTransaction).where(
                    SupplierTransaction.id == transaction_id,
                    SupplierTransaction.supplier_id == supplier.id,
                    _PAYMENT,
                )
            )
        ).scalar_one_or_none()
        if original is None:
            raise HTTPException(status_code=404, detail="Payment not found for this supplier.")

        self.db.add(
            SupplierTransaction(
                supplier_id=supplier.id,
                amount=-original.amount,  # positive: owed again
                reference=f"reversal:{original.id}",
                notes=payload.reason,
                reverses_transaction_id=original.id,
            )
        )
        self.db.add(
            AuditLog(
                user_id=reversed_by.id,
                user_name_snapshot=reversed_by.full_name,
                action="supplier.payment_reversed",
                entity_type="supplier",
                entity_id=str(supplier.id),
                old_value=f"payment_id={original.id} amount={-original.amount:.2f}",
                new_value=f"reason={payload.reason}",
            )
        )
        try:
            await self.db.commit()
        except IntegrityError as exc:
            # The unique index on reverses_transaction_id: already reversed.
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="This payment has already been reversed."
            ) from exc
        return await self._to_schema(supplier)

    async def _balance_cents(self, supplier_id: int) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                SupplierTransaction.supplier_id == supplier_id
            )
        )
        return to_cents(float(result.scalar_one()))

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
        amount = charged (goods received), negative = paid, and a
        reversal of either is counted against its own side (see the
        row-kind clauses above the class).
        """
        utc_start, utc_end = await local_day_bounds_utc(self.db, start_date, end_date)

        purchased_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                # Goods received, net of purchases that were reversed.
                or_(_CHARGE, _PURCHASE_REVERSAL),
                SupplierTransaction.created_at >= utc_start,
                SupplierTransaction.created_at < utc_end,
            )
        )
        total_purchased = float(purchased_result.scalar_one())

        paid_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierTransaction.amount), 0.0)).where(
                # Payments (negative) net of payment reversals (positive), so
                # a reversed payment stops counting as paid.
                or_(_PAYMENT, _PAYMENT_REVERSAL),
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
