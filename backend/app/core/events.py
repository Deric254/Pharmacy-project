"""
Event bus — the real-time glue between modules.

Every domain event is a typed Pydantic model (never a loose dict), and
every event carries a unique `event_id` so subscribers can dedupe on
redelivery (idempotency). Modules never call each other directly for
side effects — Sales does not import Inventory; it publishes
`SaleCompletedEvent` and Inventory subscribes. This is what keeps
modules decoupled and makes adding a new module later a matter of
writing a new subscriber, not editing existing ones.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.core.redis_client import redis_client

CHANNEL = "pharmacy_erp.events"


class DomainEvent(BaseModel):
    """Base class for every event on the bus."""

    event_type: ClassVar[str] = "domain.event"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SaleCompletedEvent(DomainEvent):
    event_type: ClassVar[str] = "sale.completed"
    sale_id: int
    customer_id: int | None
    total_amount: str  # decimal serialized as string, never float, for money


class StockLowEvent(DomainEvent):
    event_type: ClassVar[str] = "stock.low"
    product_id: int
    batch_id: int
    qty_remaining: int
    reorder_point: int


class BatchExpiringEvent(DomainEvent):
    event_type: ClassVar[str] = "batch.expiring"
    batch_id: int
    product_id: int
    expiry_date: str
    days_remaining: int


class BackupFailedEvent(DomainEvent):
    event_type: ClassVar[str] = "backup.failed"
    reason: str


class StockTakeClosedEvent(DomainEvent):
    event_type: ClassVar[str] = "stocktake.closed"
    stock_take_id: int
    shrinkage_value: str  # decimal serialized as string, never float, for money
    shrinkage_percent: float


class BusinessConfigUpdatedEvent(DomainEvent):
    event_type: ClassVar[str] = "config.updated"


async def publish(event: DomainEvent) -> None:
    envelope = {"event_type": event.event_type, "payload": event.model_dump(mode="json")}
    await redis_client.publish(CHANNEL, json.dumps(envelope))


async def subscribe() -> Any:
    """Returns a pubsub object; caller iterates `async for message in pubsub.listen()`."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL)
    return pubsub
