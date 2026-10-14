"""
Purchasing service.

Stock only ever enters through quick_purchase: physically-here stock,
receipted in one atomic step -- real batches, real stock movements,
real money owed to the supplier, no draft/sent/in-transit ceremony in
between. Every purchase order is therefore created already RECEIVED, and
reverse_purchase is the one way to undo it (all of it, or nothing).
"""

from datetime import UTC, datetime
from typing import Any, NoReturn, cast

from fastapi import HTTPException
from sqlalchemy import Integer, String, func, literal, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.money_types import MoneyCents, to_cents
from app.models.audit_log import AuditLog
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.purchase_order import (
    PurchaseOrderOut,
    PurchaseReverseRequest,
    QuickPurchaseRequest,
)


class PurchasingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def quick_purchase(self, payload: QuickPurchaseRequest, user: User) -> PurchaseOrderOut:
        """
        The direct path: stock that's already physically here, no
        advance order, no draft/send/in-transit ceremony. Goes
        straight to a PO that's already RECEIVED -- real batches, real
        stock movements, real money owed to the supplier -- in one
        atomic step. Internally this is still a real purchase order
        (so it shows up in the same history, still contributes to the
        same supplier balance), it just never sits in an intermediate
        state along the way.
        """
        supplier_result = await self.db.execute(
            select(Supplier).where(Supplier.id == payload.supplier_id)
        )
        if supplier_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        now = datetime.now(UTC)
        po = PurchaseOrder(
            supplier_id=payload.supplier_id,
            created_by_user_id=user.id,
            notes=payload.notes,
            status=PurchaseOrderStatus.RECEIVED,
            received_at=now,
        )
        self.db.add(po)
        await self.db.flush()

        total_owed = 0.0
        product_result = await self.db.execute(
            select(Product).where(Product.id.in_([line.product_id for line in payload.lines]))
        )
        products_by_id = {product.id: product for product in product_result.scalars().all()}
        for line in payload.lines:
            product = products_by_id.get(line.product_id)
            if product is None:
                raise HTTPException(status_code=404, detail="Product not found")
            # Receiving the same physical batch again -- same product,
            # same batch number, same expiry -- merges into the
            # existing record via standard weighted-average cost,
            # rather than creating a second, separate batch row for
            # what is physically the identical batch. This is exactly
            # what re-uploading the same (or an updated) purchase list
            # should do: add to what's already there, never duplicate it.
            existing_result = await self.db.execute(
                select(MedicineBatch).where(
                    MedicineBatch.product_id == line.product_id,
                    MedicineBatch.batch_number == line.batch_number,
                    MedicineBatch.expiry_date == line.expiry_date,
                )
            )
            existing_batch = existing_result.scalar_one_or_none()
            # What this line blended into, kept on its item so the purchase can
            # be reversed exactly later (see reverse_purchase). Stays None for a
            # line that creates its batch.
            cost_before: float | None = None
            cost_after: float | None = None

            if existing_batch is not None and existing_batch.locked_by_stock_take_id is not None:
                # Same invariant RefundService._restock_batch already
                # enforces for restocking, applied here for receiving:
                # a batch locked for an active physical count must not
                # have its qty_remaining/cost_price move underneath the
                # counter mid-count. Rejecting cleanly (rather than
                # silently creating a second, separate batch row with
                # the same product/batch_number/expiry, which would
                # break the "always merge, never duplicate" invariant
                # this method's own docstring describes) keeps this
                # consistent with how the rest of the app already
                # treats a locked batch.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Batch {line.batch_number} for this product is locked by an open "
                        "stock take and cannot receive new stock right now. Retry once the "
                        "count closes."
                    ),
                )

            if existing_batch is not None:
                # Selling-price conflict is a business-rule decision,
                # not a race-safety one -- it has to read the row first
                # to decide 409 vs apply vs leave-alone, so this part
                # keeps the same shape as before. What changes below is
                # qty_remaining/qty_received/cost_price: those are
                # applied via SQL column-relative expressions in a
                # single UPDATE rather than computed in Python from
                # values read here, so the actual persisted numbers
                # stay correct under concurrent receipts of the same
                # batch regardless of how stale this particular read
                # turns out to be by the time that UPDATE runs -- see
                # TestQuickPurchaseConcurrency in tests/test_purchasing.py
                # for the exact race this closes (previously passing
                # only by the same incidental single-writer-lock
                # ordering already found and hardened in
                # RefundService._reserve_refund_quantity, not by
                # anything this method itself guaranteed).
                # A restock is allowed to say nothing about price at
                # all -- it already has one from when this batch was
                # first created, and blank here means "keep it".
                # line.selling_price is only ever compared/checked when
                # the caller actually stated one; otherwise this batch's
                # existing (guaranteed non-null -- migration 0036) price
                # is left exactly as-is, untouched. Deliberately
                # changing an existing batch's price stays a separate,
                # audited action (update_selling_price) -- not something
                # that happens as a side effect of receiving stock, and
                # a mismatched explicit price here is a conflict, not a
                # silent overwrite.
                if (
                    line.selling_price is not None
                    and existing_batch.selling_price != line.selling_price
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This batch already has a different selling price. "
                            "Use a different batch number or edit the existing batch."
                        ),
                    )
                # session.execute() autoflushes pending changes first
                # (the selling_price assignment just above, if any) --
                # so that reaches the row before this statement runs,
                # and refresh() below reads back the true combined
                # state of both changes together, not just this one.
                # locked_by_stock_take_id.is_(None) here closes the
                # narrow window between the read above and this write:
                # a stock take could have locked this exact batch in
                # between. rowcount==0 with a still-existing row means
                # either that race happened, or the price condition
                # just below rejected it -- disambiguated after the
                # fact rather than guessed at.
                #
                # blended_cost is computed once and reused in both the
                # SET and the WHERE below, specifically so the
                # rejection check compares against the exact same
                # value that would be written -- never a Python-side
                # recomputation of the average, which would reopen the
                # stale-read race this whole method already closes
                # once (see TestQuickPurchaseConcurrency). Rejecting
                # via the WHERE clause keeps the read-decide-write
                # atomic: no other request can move cost_price,
                # qty_remaining, or selling_price between this
                # statement deciding and this statement writing,
                # because there's only one statement.
                #
                # The average of whole-cent costs is generally a fractional
                # cent, and SQLite would store that REAL in the integer-cents
                # column as-is (10.0761538... instead of 10.08) -- so it is
                # rounded to a whole cent inside the statement, half up.
                # Take the database's write lock now (a no-op write) and read the
                # cost this line blends into under it. Nothing can change the
                # batch between this read and the UPDATE below, so the "before"
                # kept for a later reversal is the true one -- an ordinary read
                # here could already be stale (see the comment above).
                await self.db.execute(
                    update(MedicineBatch)
                    .where(MedicineBatch.id == existing_batch.id)
                    .values(qty_remaining=MedicineBatch.qty_remaining)
                )
                await self.db.refresh(existing_batch)
                cost_before = existing_batch.cost_price
                incoming_cost = literal(line.quantity * line.unit_cost, type_=MoneyCents)
                blended_cost = sql_cast(
                    func.round(
                        (MedicineBatch.qty_remaining * MedicineBatch.cost_price + incoming_cost)
                        / (MedicineBatch.qty_remaining + line.quantity)
                    ),
                    Integer,
                )
                merge_result = cast(
                    "CursorResult[Any]",
                    await self.db.execute(
                        update(MedicineBatch)
                        .where(
                            MedicineBatch.id == existing_batch.id,
                            MedicineBatch.locked_by_stock_take_id.is_(None),
                            blended_cost <= MedicineBatch.selling_price,
                        )
                        .values(
                            cost_price=blended_cost,
                            qty_received=MedicineBatch.qty_received + line.quantity,
                            qty_remaining=MedicineBatch.qty_remaining + line.quantity,
                        )
                    ),
                )
                if merge_result.rowcount == 0:
                    await self.db.refresh(existing_batch)
                    if existing_batch.locked_by_stock_take_id is not None:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Batch {line.batch_number} for this product was just locked "
                                "by an open stock take and cannot receive new stock right "
                                "now. Retry once the count closes."
                            ),
                        )
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Receiving this line at {line.unit_cost} would push batch "
                            f"{line.batch_number}'s blended cost above its selling price "
                            f"({existing_batch.selling_price}) -- every unit sold from it "
                            "would lose money. Update the selling price first if this cost "
                            "increase is correct."
                        ),
                    )
                await self.db.refresh(existing_batch)
                cost_after = existing_batch.cost_price
                batch = existing_batch
            else:
                # A genuinely new batch has nothing to inherit a price
                # from any more -- no product-level default exists.
                # Blank is only ever valid for a restock (handled in
                # the branch above, where the batch already has its
                # own price); here it's a real gap the caller must
                # fill, so it's rejected as a clear business-rule
                # error rather than silently defaulting to 0 or
                # anything else guessed on its behalf.
                if line.selling_price is None:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Batch {line.batch_number} is new stock -- a selling price is "
                            "required (no existing batch to inherit one from)."
                        ),
                    )
                if line.selling_price < line.unit_cost:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Selling price ({line.selling_price}) for batch "
                            f"{line.batch_number} is below its cost price ({line.unit_cost}) "
                            "-- every unit sold from it would lose money. Correct the "
                            "selling price for this line."
                        ),
                    )
                batch = MedicineBatch(
                    product_id=line.product_id,
                    batch_number=line.batch_number,
                    expiry_date=line.expiry_date,
                    qty_received=line.quantity,
                    qty_remaining=line.quantity,
                    cost_price=line.unit_cost,
                    selling_price=line.selling_price,
                )
                self.db.add(batch)
            await self.db.flush()

            self.db.add(
                StockMovement(
                    batch_id=batch.id,
                    movement_type=MovementType.PURCHASE,
                    quantity_delta=line.quantity,
                    created_by_user_id=user.id,
                    reference=f"po:{po.id}",
                )
            )

            self.db.add(
                PurchaseOrderItem(
                    purchase_order_id=po.id,
                    product_id=line.product_id,
                    quantity_ordered=line.quantity,
                    unit_cost_expected=line.unit_cost,
                    quantity_received=line.quantity,
                    unit_cost_actual=line.unit_cost,
                    batch_id=batch.id,
                    batch_cost_before=cost_before,
                    batch_cost_after=cost_after,
                )
            )
            total_owed += line.quantity * line.unit_cost

        if total_owed > 0:
            self.db.add(
                SupplierTransaction(
                    supplier_id=payload.supplier_id, amount=total_owed, reference=f"po:{po.id}"
                )
            )

        # One entry per purchase order, not per line -- this is a
        # receiving event (real stock and real money entering the
        # business, both audit non-negotiables), same "who/what/when"
        # standard as every other AuditLog write in this codebase, at
        # the granularity a real audit review actually reads at.
        line_summary = ", ".join(
            f"{line.quantity}x product_id={line.product_id} @{line.unit_cost}"
            for line in payload.lines
        )
        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="purchase_order.received",
                entity_type="purchase_order",
                entity_id=str(po.id),
                old_value=None,
                new_value=(
                    f"supplier_id={payload.supplier_id}, total_owed={total_owed:.2f}, "
                    f"lines=[{line_summary}]"
                ),
            )
        )

        await self.db.commit()
        await self.db.refresh(po, attribute_names=["items", "created_at"])
        return PurchaseOrderOut.model_validate(po)

    async def _refuse_reversal(self, detail: str) -> NoReturn:
        """Undo everything this reversal wrote so far, then refuse."""
        await self.db.rollback()
        raise HTTPException(status_code=409, detail=detail)

    async def reverse_purchase(
        self, po_id: int, payload: PurchaseReverseRequest, user: User
    ) -> PurchaseOrderOut:
        """
        Undo a received purchase completely, or not at all: its stock leaves,
        what it added to the supplier's balance is cancelled, and the purchase
        is marked REVERSED -- one transaction, so there is never a half-
        reversed purchase.

        Allowed only while every batch it touched is exactly as the purchase
        left it: no stock movement of any kind since (a sale, an adjustment, a
        later delivery into the same batch), no open stock take on it, and --
        for a restock -- its cost price still the one this purchase produced.
        Under that condition the batch goes back to precisely the state it had
        before the purchase: the quantities are subtracted and the cost price
        is put back from the "before" recorded on the purchase item, so no
        rounding of the weighted average is ever guessed at. Anything else is
        refused with the reason: once stock has moved, which units were these
        no longer has an answer, and a guess would corrupt cost and profit.
        Several deliveries into one batch are therefore undone latest-first.

        Nothing is edited or deleted: the purchase, its items and its original
        ledger and stock rows stay, and opposite entries are appended.
        """
        po = (
            await self.db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
        ).scalar_one_or_none()
        if po is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        if po.status != PurchaseOrderStatus.RECEIVED:
            raise HTTPException(status_code=409, detail="This purchase has already been reversed.")

        # Per batch, in the order the lines were received: (quantity,
        # cost before, cost after). `lines` keeps the flat order for the stock
        # movements written at the end.
        lines: list[tuple[int, int]] = []
        lines_by_batch: dict[int, list[tuple[int, float | None, float | None]]] = {}
        for item in sorted(po.items, key=lambda i: i.id):
            if item.batch_id is None or item.quantity_received is None:
                raise HTTPException(
                    status_code=409,
                    detail="This purchase record is incomplete and cannot be reversed.",
                )
            lines.append((item.batch_id, item.quantity_received))
            lines_by_batch.setdefault(item.batch_id, []).append(
                (item.quantity_received, item.batch_cost_before, item.batch_cost_after)
            )
        purchase_ref = f"po:{po.id}"
        # Captured now: a rollback below expires every loaded object.
        supplier_id = po.supplier_id

        # Claim the purchase first. This statement takes the database's single
        # write lock and flips RECEIVED -> REVERSED only if it is still
        # RECEIVED, so of any number of simultaneous attempts exactly one gets
        # past here -- and everything after it reads and writes a database no
        # one else can change until this transaction ends.
        claim = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(PurchaseOrder)
                .where(
                    PurchaseOrder.id == po_id, PurchaseOrder.status == PurchaseOrderStatus.RECEIVED
                )
                .values(
                    status=PurchaseOrderStatus.REVERSED,
                    reversed_at=datetime.now(UTC),
                    reversal_reason=payload.reason,
                )
            ),
        )
        if claim.rowcount == 0:
            await self._refuse_reversal("This purchase has already been reversed.")

        batch_rows = await self.db.execute(
            select(
                MedicineBatch.id,
                MedicineBatch.batch_number,
                MedicineBatch.cost_price,
                MedicineBatch.qty_remaining,
                MedicineBatch.qty_received,
                MedicineBatch.locked_by_stock_take_id,
            ).where(MedicineBatch.id.in_(lines_by_batch))
        )
        batches = {row.id: row for row in batch_rows}

        # Where this purchase sits in each batch's stock ledger. A batch with no
        # movement of this purchase at all means the records disagree.
        own_first = aliased(StockMovement)
        first_own_movement = (
            select(func.min(own_first.id))
            .where(
                own_first.batch_id == StockMovement.batch_id,
                own_first.reference == purchase_ref,
                own_first.movement_type == MovementType.PURCHASE,
            )
            .scalar_subquery()
        )
        recorded = set(
            (
                await self.db.execute(
                    select(StockMovement.batch_id)
                    .where(
                        StockMovement.batch_id.in_(lines_by_batch),
                        StockMovement.reference == purchase_ref,
                        StockMovement.movement_type == MovementType.PURCHASE,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if recorded != set(lines_by_batch):
            await self._refuse_reversal(
                "This purchase record is incomplete and cannot be reversed."
            )
        # Every movement on these batches that is not this purchase's own
        # receipt: whether it came after it (later) or before it, and whether it
        # belongs to a purchase that was itself reversed. A reversed purchase's
        # receipt and its reversal cancel exactly -- the batch was put back to
        # the state it had before them -- so they are not "activity since".
        reversed_receipts = select(literal("po:") + sql_cast(PurchaseOrder.id, String)).where(
            PurchaseOrder.status == PurchaseOrderStatus.REVERSED
        )
        others = await self.db.execute(
            select(
                StockMovement.batch_id,
                (StockMovement.id > first_own_movement).label("later"),
                or_(
                    StockMovement.reference.like("po-reversal:%"),
                    StockMovement.reference.in_(reversed_receipts),
                ).label("cancelled"),
            )
            .where(
                StockMovement.batch_id.in_(lines_by_batch),
                or_(
                    StockMovement.reference.is_(None),
                    StockMovement.reference != purchase_ref,
                    StockMovement.movement_type != MovementType.PURCHASE,
                ),
            )
            .distinct()
        )
        rows = others.all()
        moved_since = {batch_id for batch_id, later, cancelled in rows if later and not cancelled}
        existed_before = {batch_id for batch_id, later, _ in rows if not later}

        restores: list[tuple[int, int, float]] = []
        for batch_id, batch_lines in lines_by_batch.items():
            batch = batches[batch_id]
            if batch.locked_by_stock_take_id is not None:
                await self._refuse_reversal(
                    f"Batch {batch.batch_number} is locked by an open stock take. "
                    "Retry once the count closes."
                )
            if batch_id in moved_since:
                await self._refuse_reversal(
                    f"Batch {batch.batch_number} has had other stock activity since this "
                    "purchase (a sale, an adjustment, or another delivery into it), so it "
                    "cannot be reversed. Reverse later deliveries first, or use a stock "
                    "adjustment instead."
                )
            # Walk the deliveries into this batch latest-first, undoing each
            # one's effect on the cost price and checking that it is still
            # exactly what that delivery produced.
            restored_cost = batch.cost_price
            for position in reversed(range(len(batch_lines))):
                _, before, after = batch_lines[position]
                if before is None or after is None:
                    # No snapshot: only a line that created the batch has none
                    # by design (it is the first line, with nothing before it).
                    # Anything else is a restock from before they were recorded.
                    if position == 0 and batch_id not in existed_before:
                        break
                    await self._refuse_reversal(
                        f"This purchase restocked batch {batch.batch_number} before the system "
                        "recorded what is needed to reverse a restock, so it cannot be "
                        "reversed automatically. Use a stock adjustment instead."
                    )
                if to_cents(after) != to_cents(restored_cost):
                    await self._refuse_reversal(
                        f"The cost price of batch {batch.batch_number} was changed after this "
                        "purchase, so it cannot be reversed automatically."
                    )
                restored_cost = before
            total = sum(quantity for quantity, _, _ in batch_lines)
            if batch.qty_remaining < total or batch.qty_received < total:
                await self._refuse_reversal(
                    f"Batch {batch.batch_number} no longer holds all of this purchase's stock, "
                    "so it cannot be reversed. Use a stock adjustment instead."
                )
            restores.append((batch_id, total, restored_cost))

        for batch_id, total, restored_cost in restores:
            await self.db.execute(
                update(MedicineBatch)
                .where(MedicineBatch.id == batch_id)
                .values(
                    qty_remaining=MedicineBatch.qty_remaining - total,
                    qty_received=MedicineBatch.qty_received - total,
                    cost_price=restored_cost,
                )
            )

        for batch_id, quantity in lines:
            self.db.add(
                StockMovement(
                    batch_id=batch_id,
                    movement_type=MovementType.ADJUSTMENT,
                    quantity_delta=-quantity,
                    reason=payload.reason,
                    reference=f"po-reversal:{po_id}",
                    created_by_user_id=user.id,
                )
            )

        charge = (
            await self.db.execute(
                select(SupplierTransaction).where(
                    SupplierTransaction.supplier_id == supplier_id,
                    SupplierTransaction.reference == purchase_ref,
                    SupplierTransaction.amount > 0,
                    SupplierTransaction.reverses_transaction_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        cancelled = 0.0
        if charge is not None:
            cancelled = charge.amount
            self.db.add(
                SupplierTransaction(
                    supplier_id=supplier_id,
                    amount=-charge.amount,
                    reference=f"po-reversal:{po_id}",
                    notes=payload.reason,
                    reverses_transaction_id=charge.id,
                )
            )

        self.db.add(
            AuditLog(
                user_id=user.id,
                user_name_snapshot=user.full_name,
                action="purchase_order.reversed",
                entity_type="purchase_order",
                entity_id=str(po_id),
                old_value=f"status=RECEIVED, supplier_id={supplier_id}, total_owed={cancelled:.2f}",
                new_value=f"status=REVERSED, reason={payload.reason}",
            )
        )

        await self.db.commit()
        await self.db.refresh(po, attribute_names=["status", "reversed_at", "reversal_reason"])
        return PurchaseOrderOut.model_validate(po)

    async def get(self, po_id: int) -> PurchaseOrderOut:
        result = await self.db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
        po = result.scalar_one_or_none()
        if po is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return PurchaseOrderOut.model_validate(po)

    async def list_all(self) -> list[PurchaseOrderOut]:
        result = await self.db.execute(
            select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
        )
        return [PurchaseOrderOut.model_validate(po) for po in result.scalars().all()]
