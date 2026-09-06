"""
Refund service.

A refund is always against specific SaleItem rows from a specific
sale -- never a free-floating "refund $X to this customer" -- so every
refunded cent and every unit put back on the shelf traces to exactly
what was originally sold. Two invariants this enforces that a naive
implementation would miss:

1. Over-refund prevention: the sum of quantities refunded against a
   given SaleItem across ALL refunds (not just this one) can never
   exceed what was originally sold on that line. Enforced atomically
   via sale_items.qty_refunded (see _reserve_refund_quantity) -- not
   by a read-then-check-then-write count of prior RefundItem rows,
   which would let two concurrent refunds each pass a check against
   the same stale count.

2. Stock-take lock respect: restocking a batch currently locked for a
   physical count would change the count out from under the counter,
   exactly like a sale against a locked batch would. Refunds against
   a locked batch are rejected (409) rather than silently skipping the
   restock -- the cashier should retry once the count closes, not have
   money quietly leave the till with no matching stock movement.
"""

from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.refund import Refund, RefundItem
from app.models.sale import Sale, SaleItem
from app.models.stock_movement import MovementType, StockMovement
from app.models.user import User
from app.schemas.refund import RefundOut, RefundRequest


class RefundService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_refund(
        self, sale_id: int, payload: RefundRequest, processed_by: User
    ) -> RefundOut:
        sale = await self._get_sale_or_404(sale_id)
        sale_items_by_id = {item.id: item for item in sale.items}

        # sale_item.unit_price is always the FULL, undiscounted price
        # sold at (a sale's discount lives only once, on Sale.discount_
        # amount, never split across its line items -- see
        # sale_service.py and report_service.py's top_products_by_
        # revenue for the same fact). Refunding straight off unit_price
        # would hand back MORE money than the customer actually paid on
        # any discounted sale -- a real loss to the till, not just a
        # reporting quirk. This ratio prorates the sale's discount the
        # same way top_products_by_revenue does, so a refund's total_
        # amount matches real money collected. A zero-subtotal sale (
        # every line free) has nothing to prorate a discount against --
        # ratio of 1.0 is a safe no-op there.
        discount_ratio = (sale.total_amount / sale.subtotal) if sale.subtotal else 1.0

        refund = Refund(
            sale_id=sale.id,
            processed_by_user_id=processed_by.id,
            reason=payload.reason,
            method=payload.method,
            notes=payload.notes,
            total_amount=0.0,  # filled in after validating every line
        )
        self.db.add(refund)
        await self.db.flush()

        total_amount = 0.0
        for line in payload.items:
            sale_item = sale_items_by_id.get(line.sale_item_id)
            if sale_item is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Sale item {line.sale_item_id} does not belong to sale {sale_id}",
                )

            # unit_price stays the original, undiscounted sale price --
            # same historical-traceability rule as SaleItem itself (see
            # Refund's own docstring). line_total is what actually goes
            # back to the customer, so it -- not a plain unit_price *
            # quantity -- is what real money (refund.total_amount) is
            # summed from.
            line_total = sale_item.unit_price * line.quantity * discount_ratio
            total_amount += line_total

            # Atomic reserve-then-fail, not read-then-check-then-write:
            # see _reserve_refund_quantity's own docstring for why the
            # previous version of this check (a plain SELECT/sum, same
            # shape as _already_refunded_quantity below) was only safe
            # from a genuine over-refund race by incidental statement
            # ordering elsewhere in this method, not by anything the
            # database enforced.
            await self._reserve_refund_quantity(sale_item, line.quantity)

            if line.restock:
                await self._restock_batch(
                    sale_item.batch_id, line.quantity, refund.id, processed_by.id
                )

            self.db.add(
                RefundItem(
                    refund_id=refund.id,
                    sale_item_id=sale_item.id,
                    product_id=sale_item.product_id,
                    batch_id=sale_item.batch_id,
                    quantity=line.quantity,
                    unit_price=sale_item.unit_price,
                    line_total=line_total,
                    restocked=line.restock,
                )
            )

        refund.total_amount = total_amount
        self.db.add(
            AuditLog(
                user_id=processed_by.id,
                user_name_snapshot=processed_by.full_name,
                action="sale.refunded",
                entity_type="refund",
                entity_id=str(refund.id),
                new_value=f"sale_id={sale.id} amount={total_amount:.2f} reason={payload.reason}",
            )
        )
        await self.db.commit()
        await self.db.refresh(refund, attribute_names=["items", "created_at"])
        return RefundOut.model_validate(refund)

    async def list_refunds_for_sale(self, sale_id: int) -> list[RefundOut]:
        await self._get_sale_or_404(sale_id)
        result = await self.db.execute(
            select(Refund).where(Refund.sale_id == sale_id).order_by(Refund.created_at)
        )
        return [RefundOut.model_validate(r) for r in result.scalars().all()]

    async def _get_sale_or_404(self, sale_id: int) -> Sale:
        result = await self.db.execute(select(Sale).where(Sale.id == sale_id))
        sale = result.scalar_one_or_none()
        if sale is None:
            raise HTTPException(status_code=404, detail="Sale not found")
        return sale

    async def _reserve_refund_quantity(self, sale_item: SaleItem, quantity: int) -> None:
        """
        Atomic reserve-then-fail against sale_items.qty_refunded (see
        that column's own comment on the model, and migration
        0034_sale_item_qty_refunded for the full history of why this
        replaced a plain SELECT/sum check). The WHERE clause re-checks
        the row's real, current state at the exact moment this UPDATE
        runs -- not a value read earlier in this request -- so two
        concurrent refunds against the same sale_item can never both
        succeed past what was actually sold, regardless of statement
        ordering elsewhere in create_refund(). Same proven shape as
        _restock_batch below and apply_allocations() in
        stock_selection_service.py: SQLite has no usable row-locking
        (SELECT ... FOR UPDATE is silently a no-op), so the invariant
        has to live in the UPDATE's WHERE clause itself.
        """
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(SaleItem)
                .where(
                    SaleItem.id == sale_item.id,
                    SaleItem.qty_refunded + quantity <= SaleItem.quantity,
                )
                .values(qty_refunded=SaleItem.qty_refunded + quantity)
            ),
        )
        if result.rowcount == 0:
            await self.db.refresh(sale_item)
            remaining = sale_item.quantity - sale_item.qty_refunded
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot refund {quantity} of sale item {sale_item.id}: "
                    f"only {remaining} unit(s) remain refundable "
                    f"({sale_item.qty_refunded} already refunded of {sale_item.quantity} sold)"
                ),
            )
        # Deliberately NOT also doing sale_item.qty_refunded += quantity
        # here: the UPDATE statement's default synchronize_session=
        # "evaluate" already applies this same increment to the
        # in-memory object as a side effect of running it. Adding it
        # again would double-apply the increment in Python, and that
        # wrong doubled value would then get written back to the row a
        # second time whenever this session's unit-of-work next
        # flushes anything else dirty on it -- silently overwriting
        # the correct value the atomic UPDATE just committed, with no
        # error anywhere to reveal it happened. Confirmed by hand: this
        # exact line caused sale_item 0+3+3=6 in memory after a single
        # +3 UPDATE, corrupting the real refundable count. Nothing
        # later in create_refund reads qty_refunded again, so there is
        # nothing to keep in sync here.

    async def _restock_batch(
        self, batch_id: int, quantity: int, refund_id: int, created_by_user_id: int
    ) -> None:
        # The real guarantee against two concurrent refunds racing on
        # the same batch -- or a stock take starting to lock this
        # batch in the gap between a check and a write -- is this
        # atomic UPDATE, not row-locking (SQLite silently drops
        # SELECT...FOR UPDATE entirely, so a plan of "SELECT, check in
        # Python, then UPDATE" was never actually safe here, same as
        # the stock-decrement and PO-transition bugs already found and
        # fixed this session). The lock-check is folded directly into
        # the WHERE clause instead of a separate read beforehand.
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(MedicineBatch)
                .where(
                    MedicineBatch.id == batch_id,
                    MedicineBatch.locked_by_stock_take_id.is_(None),
                )
                .values(qty_remaining=MedicineBatch.qty_remaining + quantity)
            ),
        )
        if result.rowcount == 0:
            batch = await self.db.get(MedicineBatch, batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Batch {batch_id} is locked by an open stock take and cannot be "
                    "restocked right now. Retry once the count closes, or refund without "
                    "restocking this line."
                ),
            )

        self.db.add(
            StockMovement(
                batch_id=batch_id,
                movement_type=MovementType.RETURN,
                quantity_delta=quantity,
                reference=f"refund:{refund_id}",
                created_by_user_id=created_by_user_id,
            )
        )
