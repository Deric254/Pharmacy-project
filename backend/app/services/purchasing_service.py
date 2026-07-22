"""
Purchasing service.

The state machine is enforced here, not left to whatever status string
a client sends: DRAFT -> SENT -> IN_TRANSIT -> RECEIVED -> RECONCILED,
each transition its own method, each one an atomic UPDATE guarded by
the expected prior status so two concurrent calls can't both move the
same PO (see _transition's docstring for why this isn't row-locking).

Receiving is the critical integration point: moving to RECEIVED doesn't
just flip a status field, it creates the actual MedicineBatch rows and
StockMovement ledger entries in the same transaction -- "drag the card,
stock updates" is literally true here, not just a UI illusion. Receiving
variances (actual vs ordered) are detected and returned, never silently
corrected.
"""

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import PurchaseOrderStatusChangedEvent, publish
from app.models.medicine_batch import MedicineBatch
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.stock_movement import MovementType, StockMovement
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.purchase_order import (
    PurchaseOrderCreate,
    PurchaseOrderOut,
    ReceiveRequest,
    ReceiveResponse,
    ReceivingVarianceOut,
    ReconcileRequest,
)


class PurchasingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: PurchaseOrderCreate, user: User) -> PurchaseOrderOut:
        supplier_result = await self.db.execute(
            select(Supplier).where(Supplier.id == payload.supplier_id)
        )
        if supplier_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        po = PurchaseOrder(
            supplier_id=payload.supplier_id, created_by_user_id=user.id, notes=payload.notes
        )
        self.db.add(po)
        await self.db.flush()

        for item in payload.items:
            self.db.add(
                PurchaseOrderItem(
                    purchase_order_id=po.id,
                    product_id=item.product_id,
                    quantity_ordered=item.quantity_ordered,
                    unit_cost_expected=item.unit_cost_expected,
                )
            )

        await self.db.commit()
        await self.db.refresh(po, attribute_names=["items", "created_at"])
        return PurchaseOrderOut.model_validate(po)

    async def send(self, po_id: int, user: User) -> PurchaseOrderOut:
        po, old_status = await self._transition(
            po_id, PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.SENT
        )
        po.sent_at = datetime.now(UTC)
        await self._commit_and_publish(po, old_status)
        return PurchaseOrderOut.model_validate(po)

    async def mark_in_transit(self, po_id: int, user: User) -> PurchaseOrderOut:
        po, old_status = await self._transition(
            po_id, PurchaseOrderStatus.SENT, PurchaseOrderStatus.IN_TRANSIT
        )
        po.in_transit_at = datetime.now(UTC)
        await self._commit_and_publish(po, old_status)
        return PurchaseOrderOut.model_validate(po)

    async def receive(self, po_id: int, payload: ReceiveRequest, user: User) -> ReceiveResponse:
        po, old_status = await self._transition(
            po_id, PurchaseOrderStatus.IN_TRANSIT, PurchaseOrderStatus.RECEIVED
        )

        items_by_id = {item.id: item for item in po.items}
        variances: list[ReceivingVarianceOut] = []
        total_owed = 0.0

        for line in payload.lines:
            item = items_by_id.get(line.item_id)
            if item is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Purchase order item {line.item_id} not found on this PO",
                )
            if item.batch_id is not None:
                raise HTTPException(
                    status_code=400, detail=f"Item {line.item_id} has already been received"
                )

            batch = MedicineBatch(
                product_id=item.product_id,
                batch_number=line.batch_number,
                expiry_date=line.expiry_date,
                qty_received=line.quantity_received,
                qty_remaining=line.quantity_received,
                cost_price=line.unit_cost_actual,
            )
            self.db.add(batch)
            await self.db.flush()

            self.db.add(
                StockMovement(
                    batch_id=batch.id,
                    movement_type=MovementType.PURCHASE,
                    quantity_delta=line.quantity_received,
                    created_by_user_id=user.id,
                    reference=f"po:{po_id}",
                )
            )

            item.quantity_received = line.quantity_received
            item.unit_cost_actual = line.unit_cost_actual
            item.batch_id = batch.id

            total_owed += line.quantity_received * line.unit_cost_actual

            variance = line.quantity_received - item.quantity_ordered
            if variance != 0:
                variances.append(
                    ReceivingVarianceOut(
                        item_id=item.id,
                        product_id=item.product_id,
                        quantity_ordered=item.quantity_ordered,
                        quantity_received=line.quantity_received,
                        variance=variance,
                    )
                )

        if total_owed > 0:
            self.db.add(
                SupplierTransaction(
                    supplier_id=po.supplier_id, amount=total_owed, reference=f"po:{po_id}"
                )
            )

        po.received_at = datetime.now(UTC)
        await self._commit_and_publish(po, old_status)
        await self.db.refresh(po, attribute_names=["items"])

        return ReceiveResponse(
            purchase_order=PurchaseOrderOut.model_validate(po), variances=variances
        )

    async def reconcile(
        self, po_id: int, payload: ReconcileRequest, user: User
    ) -> PurchaseOrderOut:
        po, old_status = await self._transition(
            po_id, PurchaseOrderStatus.RECEIVED, PurchaseOrderStatus.RECONCILED
        )

        if payload.payment_amount:
            self.db.add(
                SupplierTransaction(
                    supplier_id=po.supplier_id,
                    amount=-payload.payment_amount,
                    reference=f"po:{po_id}",
                    notes=payload.notes,
                )
            )

        po.reconciled_at = datetime.now(UTC)
        await self._commit_and_publish(po, old_status)
        return PurchaseOrderOut.model_validate(po)

    async def get(self, po_id: int) -> PurchaseOrderOut:
        result = await self.db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
        po = result.scalar_one_or_none()
        if po is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return PurchaseOrderOut.model_validate(po)

    async def get_kanban(self) -> dict[str, list[PurchaseOrderOut]]:
        result = await self.db.execute(select(PurchaseOrder).order_by(PurchaseOrder.created_at))
        board: dict[str, list[PurchaseOrderOut]] = {
            status.value: [] for status in PurchaseOrderStatus
        }
        for po in result.scalars().all():
            board[po.status.value].append(PurchaseOrderOut.model_validate(po))
        return board

    async def _transition(
        self, po_id: int, expected_current: PurchaseOrderStatus, target: PurchaseOrderStatus
    ) -> tuple[PurchaseOrder, PurchaseOrderStatus]:
        """
        Atomically moves the PO from expected_current to target and
        returns the fresh row (still open for the caller to set
        additional fields, e.g. sent_at, before calling
        _commit_and_publish) plus the status it moved from.

        The actual guarantee against two concurrent transition calls
        racing each other -- only one of "send this PO" called twice
        at once can ever win -- is the atomic `UPDATE ... WHERE status
        = :expected_current` below, not row-locking: SQLite silently
        drops SELECT...FOR UPDATE entirely, so a plan of "SELECT, check
        in Python, then UPDATE" was never actually safe under real
        concurrency on this app's backend. The WHERE clause here is
        checked against the row's real current status at the exact
        moment the UPDATE runs; the loser of two simultaneous calls
        gets a clean 400, never a silently-doubled transition.
        """
        result = cast(
            "CursorResult[Any]",
            await self.db.execute(
                update(PurchaseOrder)
                .where(PurchaseOrder.id == po_id, PurchaseOrder.status == expected_current)
                .values(status=target, version=PurchaseOrder.version + 1)
            ),
        )
        if result.rowcount == 0:
            current = await self.db.get(PurchaseOrder, po_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Purchase order not found")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot move to {target.value} from {current.status.value} "
                    f"(expected current status: {expected_current.value})"
                ),
            )

        po = await self.db.get(PurchaseOrder, po_id)
        assert po is not None  # the UPDATE above just succeeded against this exact row
        return po, expected_current

    async def _commit_and_publish(self, po: PurchaseOrder, old_status: PurchaseOrderStatus) -> None:
        await self.db.commit()
        await self.db.refresh(po)
        await publish(
            PurchaseOrderStatusChangedEvent(
                purchase_order_id=po.id, old_status=old_status.value, new_status=po.status.value
            )
        )
