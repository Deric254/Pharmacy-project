"""
Notification dispatcher.

A single background task subscribes to the same Redis channel every
module already publishes domain events to (app.core.events), and for
each event decides who should see it based on EVENT_PERMISSION_MAP,
then hands it to the ConnectionManager to broadcast. This is the piece
that makes "real-time cross-module sync" literally true: a sale
completing, a PO changing status, a backup failing, or the business
config changing all reach a connected screen without polling.

Kept deliberately separate from the WebSocket route itself: this
module has no FastAPI/Starlette dependency at all, so its dispatch
logic (which permission gates which event type) is fully unit-testable
without any WebSocket protocol involved.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

from app.core.events import subscribe
from app.core.websocket_manager import ConnectionManager

logger = logging.getLogger(__name__)

# None means broadcast to every connected client regardless of role --
# used only for config.updated, since branding should refresh live for
# everyone. Every other event is gated behind the permission that
# governs who should reasonably care about it; unrecognized future
# event types default to reports.view rather than broadcasting openly.
EVENT_PERMISSION_MAP: dict[str, str | None] = {
    "sale.completed": "reports.view",
    "stock.low": "inventory.view",
    "batch.expiring": "inventory.view",
    "po.status_changed": "purchasing.create_po",
    "backup.failed": "backups.manage",
    "stocktake.closed": "reports.view",
    "config.updated": None,
}
_DEFAULT_PERMISSION = "reports.view"


async def dispatch_one_message(manager: ConnectionManager, raw_message: dict[str, object]) -> None:
    """
    Processes a single raw pub/sub message. Split out from the listen
    loop specifically so tests can feed messages directly without
    needing a real Redis connection.
    """
    if raw_message.get("type") != "message":
        return

    try:
        envelope = json.loads(raw_message["data"])  # type: ignore[arg-type]
        event_type = envelope["event_type"]
        payload = envelope["payload"]
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Received malformed event on notification channel, skipping")
        return

    required_permission = EVENT_PERMISSION_MAP.get(event_type, _DEFAULT_PERMISSION)
    await manager.broadcast({"event_type": event_type, "payload": payload}, required_permission)


async def run_notification_dispatcher(manager: ConnectionManager) -> None:
    """Long-running task: subscribes once, dispatches forever until cancelled."""
    pubsub = await subscribe()
    try:
        async for raw_message in _listen(pubsub):
            await dispatch_one_message(manager, raw_message)
    finally:
        await pubsub.unsubscribe()


async def _listen(pubsub: object) -> AsyncIterator[dict[str, object]]:
    async for message in pubsub.listen():  # type: ignore[attr-defined]
        yield message


def start_dispatcher_task(manager: ConnectionManager) -> asyncio.Task[None]:
    return asyncio.create_task(run_notification_dispatcher(manager))


async def stop_dispatcher_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
