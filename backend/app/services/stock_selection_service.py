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

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today
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

    `lock` applies SELECT...FOR UPDATE, kept as defense-in-depth for
    any backend that honors it -- but it is NOT what actually prevents
    two concurrent sales from overselling the same units on this app's
    current SQLite backend, which silently drops the clause entirely.
    The real guarantee is the atomic `UPDATE ... WHERE qty_remaining
    >= :qty` in apply_allocations(), below -- correct regardless of
    what this SELECT saw, since it's checked against the row's actual
    state at the moment the decrement executes.
    """
    if qty_needed <= 0:
        raise ValueError("qty_needed must be positive")

    today = await business_today(db)
    query = (
        select(MedicineBatch)
        .where(
            MedicineBatch.product_id == product_id,
            MedicineBatch.qty_remaining > 0,
            MedicineBatch.locked_by_stock_take_id.is_(None),
            MedicineBatch.expiry_date >= today,
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

    The decrement itself is an atomic `UPDATE ... WHERE qty_remaining
    >= :qty`, not a Python-level read-modify-write on the ORM object.
    That distinction is the actual safety mechanism against two
    concurrent sales both allocating against the same units -- the
    WHERE clause is checked against the row's real state at the moment
    the UPDATE runs, not whatever `select_batches_fefo` happened to
    read earlier in this same request. If a concurrent transaction
    already consumed the stock this allocation was planned against,
    the UPDATE affects zero rows and this raises rather than silently
    overwriting a correct decrement with a stale one.
    """
    for batch, qty in allocations:
        result = cast(
            "CursorResult[Any]",
            await db.execute(
                update(MedicineBatch)
                .where(MedicineBatch.id == batch.id, MedicineBatch.qty_remaining >= qty)
                .values(qty_remaining=MedicineBatch.qty_remaining - qty)
            ),
        )
        if result.rowcount == 0:
            refreshed = await db.get(MedicineBatch, batch.id)
            available_now = refreshed.qty_remaining if refreshed is not None else 0
            raise InsufficientStockError(batch.product_id, requested=qty, available=available_now)
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
