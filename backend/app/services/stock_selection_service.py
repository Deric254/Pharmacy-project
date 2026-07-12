"""
FEFO (First-Expiry-First-Out) stock selection.

This is intentionally a pure selection/allocation utility, not a full
"make a sale" service -- it doesn't commit, and it doesn't know what
kind of movement is happening (sale, transfer, write-off). That's
deliberate: the Sales module will call `select_batches_fefo` then
`apply_allocations` inside its own sale transaction, alongside
inserting the sale/payment rows, so everything commits or rolls back
together. Keeping this module free of that context is what lets it be
reused unchanged by Sales, Adjustments, and Transfers later.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medicine_batch import MedicineBatch
from app.models.stock_movement import MovementType, StockMovement

Allocation = tuple[MedicineBatch, int]


class InsufficientStockError(Exception):
    def __init__(self, product_id: int, requested: int, available: int) -> None:
        self.product_id = product_id
        self.requested = requested
        self.available = available
        super().__init__(f"Product {product_id}: requested {requested}, only {available} available")


async def select_batches_fefo(
    db: AsyncSession, product_id: int, qty_needed: int, lock: bool = True
) -> list[Allocation]:
    """
    Returns [(batch, qty_to_take), ...] covering qty_needed, drawing
    from the batch with the nearest expiry date first, spilling into
    the next-nearest batch if one lot isn't enough. Raises
    InsufficientStockError (without partially allocating) if total
    stock across all batches can't cover the request -- the caller's
    transaction should not proceed on a partial fill for a sale.

    Batches currently locked by an open stock take are excluded
    entirely, as if they had zero stock -- they're being physically
    counted and must not move mid-count.

    `lock` applies SELECT...FOR UPDATE so two concurrent sales can't
    both allocate against the same units -- this is the actual
    mechanism that prevents overselling, not just a documented promise.
    """
    if qty_needed <= 0:
        raise ValueError("qty_needed must be positive")

    query = (
        select(MedicineBatch)
        .where(
            MedicineBatch.product_id == product_id,
            MedicineBatch.qty_remaining > 0,
            MedicineBatch.locked_by_stock_take_id.is_(None),
        )
        .order_by(MedicineBatch.expiry_date.asc())
    )
    if lock:
        query = query.with_for_update()

    result = await db.execute(query)
    batches = result.scalars().all()

    allocations: list[Allocation] = []
    remaining = qty_needed
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.qty_remaining, remaining)
        allocations.append((batch, take))
        remaining -= take

    if remaining > 0:
        available = qty_needed - remaining
        raise InsufficientStockError(product_id, qty_needed, available)

    return allocations


async def apply_allocations(
    db: AsyncSession,
    allocations: list[Allocation],
    movement_type: MovementType,
    created_by_user_id: int | None,
    reference: str | None = None,
    reason: str | None = None,
) -> None:
    """
    Decrements each batch's qty_remaining and writes the matching
    StockMovement ledger row -- both in the caller's existing
    transaction (this function never commits). The ledger row is what
    makes the change auditable; the qty_remaining update is the cached
    derived value kept in sync alongside it.
    """
    for batch, qty in allocations:
        batch.qty_remaining -= qty
        db.add(
            StockMovement(
                batch_id=batch.id,
                movement_type=movement_type,
                quantity_delta=-qty,
                reference=reference,
                reason=reason,
                created_by_user_id=created_by_user_id,
            )
        )
