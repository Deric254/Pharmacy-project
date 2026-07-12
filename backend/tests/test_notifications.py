"""
Notification tests. Three layers, each tested at the right level:
  1. ConnectionManager - pure unit tests with fake connections, no
     WebSocket protocol involved. This is where permission filtering
     and broadcast-to-all actually get proven.
  2. Dispatcher - unit tests feeding raw pub/sub-shaped messages
     directly, proving the event-type -> permission mapping.
  3. One genuine end-to-end integration test: a real WebSocket
     connection through the actual app, a real event published to
     Redis, and confirmation the client actually receives it - proving
     the whole pipe works, not just each piece in isolation.
"""

import asyncio
import json

import httpx
import pytest
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.core.events import StockLowEvent, publish
from app.core.security import create_token
from app.core.websocket_manager import ConnectionManager
from app.services.notification_dispatcher import EVENT_PERMISSION_MAP, dispatch_one_message


class FakeConnection:
    """Records every message sent to it -- no real WebSocket protocol needed."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, object]] = []
        self.fail = fail

    async def send_json(self, data: dict[str, object]) -> None:
        if self.fail:
            raise ConnectionError("simulated dead connection")
        self.sent.append(data)


class TestConnectionManagerPermissionFiltering:
    async def test_broadcast_with_no_permission_reaches_everyone(self, owner_user, employee_user):
        manager = ConnectionManager()
        owner_conn = FakeConnection()
        employee_conn = FakeConnection()
        manager.connect(owner_conn, owner_user)
        manager.connect(employee_conn, employee_user)

        await manager.broadcast({"event_type": "config.updated"}, required_permission=None)

        assert owner_conn.sent == [{"event_type": "config.updated"}]
        assert employee_conn.sent == [{"event_type": "config.updated"}]

    async def test_broadcast_with_permission_only_reaches_users_who_have_it(
        self, owner_user, employee_user
    ):
        manager = ConnectionManager()
        owner_conn = FakeConnection()
        employee_conn = FakeConnection()
        manager.connect(owner_conn, owner_user)
        manager.connect(employee_conn, employee_user)

        # backups.manage: owner has it (full permission set), employee doesn't.
        await manager.broadcast(
            {"event_type": "backup.failed"}, required_permission="backups.manage"
        )

        assert owner_conn.sent == [{"event_type": "backup.failed"}]
        assert employee_conn.sent == []  # correctly filtered out

    async def test_disconnect_stops_further_broadcasts(self, owner_user):
        manager = ConnectionManager()
        conn = FakeConnection()
        manager.connect(conn, owner_user)
        manager.disconnect(conn, owner_user.id)

        await manager.broadcast({"event_type": "config.updated"}, required_permission=None)

        assert conn.sent == []
        assert manager.connection_count == 0

    async def test_dead_connection_is_cleaned_up_automatically(self, owner_user):
        manager = ConnectionManager()
        dead_conn = FakeConnection(fail=True)
        manager.connect(dead_conn, owner_user)
        assert manager.connection_count == 1

        # Broadcasting must not raise even though this connection fails.
        await manager.broadcast({"event_type": "config.updated"}, required_permission=None)

        assert manager.connection_count == 0  # auto-removed after the failed send

    async def test_multiple_connections_for_the_same_user_all_receive_it(self, owner_user):
        """A user with the app open on both phone and desktop should get
        the notification on both."""
        manager = ConnectionManager()
        phone = FakeConnection()
        desktop = FakeConnection()
        manager.connect(phone, owner_user)
        manager.connect(desktop, owner_user)

        await manager.broadcast({"event_type": "stock.low"}, required_permission="inventory.view")

        assert phone.sent == [{"event_type": "stock.low"}]
        assert desktop.sent == [{"event_type": "stock.low"}]
        assert manager.connection_count == 2


class TestDispatcherEventRouting:
    async def test_non_message_type_is_ignored(self):
        manager = ConnectionManager()
        # e.g. the "subscribe" confirmation message redis sends first
        await dispatch_one_message(manager, {"type": "subscribe", "data": 1})
        # No error, nothing to assert on delivery since nothing connected

    async def test_malformed_json_does_not_crash_the_dispatcher(self):
        manager = ConnectionManager()
        await dispatch_one_message(manager, {"type": "message", "data": "not valid json{{{"})
        # Must not raise - a single bad message can't take down the dispatcher

    async def test_config_updated_reaches_a_connection_with_no_permissions_at_all(
        self, employee_user
    ):
        """config.updated is the one event with required_permission=None
        -- it must reach every connected client regardless of role."""
        manager = ConnectionManager()
        conn = FakeConnection()
        manager.connect(conn, employee_user)

        envelope = json.dumps({"event_type": "config.updated", "payload": {}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert len(conn.sent) == 1
        assert conn.sent[0]["event_type"] == "config.updated"

    async def test_backup_failed_does_not_reach_employee(self, employee_user):
        manager = ConnectionManager()
        conn = FakeConnection()
        manager.connect(conn, employee_user)

        envelope = json.dumps({"event_type": "backup.failed", "payload": {"reason": "x"}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert conn.sent == []  # employee doesn't have backups.manage

    async def test_backup_failed_reaches_owner(self, owner_user):
        manager = ConnectionManager()
        conn = FakeConnection()
        manager.connect(conn, owner_user)

        envelope = json.dumps({"event_type": "backup.failed", "payload": {"reason": "disk full"}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert len(conn.sent) == 1
        assert conn.sent[0]["payload"]["reason"] == "disk full"

    def test_every_mapped_event_has_a_sane_permission_or_is_explicitly_public(self):
        """Guards against a future event type being added to the map with
        a typo'd permission code that no role actually has."""
        known_permission_like_values = {
            "reports.view",
            "inventory.view",
            "purchasing.create_po",
            "backups.manage",
            None,
        }
        for event_type, permission in EVENT_PERMISSION_MAP.items():
            assert permission in known_permission_like_values, (
                f"{event_type} maps to an unexpected permission value: {permission}"
            )


class TestWebSocketEndToEnd:
    """
    One real integration test: a genuine WebSocket connection through
    the actual app (httpx_ws over ASGIWebSocketTransport, same event
    loop as everything else -- not Starlette's sync TestClient, which
    this project already found causes cross-event-loop conflicts with
    the shared async Redis/DB clients), a real event published to the
    real Redis channel, and confirmation the connected client actually
    receives it end-to-end.
    """

    async def test_connect_with_invalid_token_is_rejected(self):
        from app.main import app

        async with httpx.AsyncClient(
            transport=ASGIWebSocketTransport(app=app), base_url="http://test"
        ) as http_client:
            with pytest.raises(Exception):  # noqa: B017 - handshake/close failure expected
                async with aconnect_ws(
                    "/api/v1/ws/notifications?token=not-a-real-token", client=http_client
                ):
                    pass

    async def test_stock_low_event_reaches_a_connected_client_with_inventory_view(self, owner_user):
        from app.core.websocket_manager import manager as ws_manager
        from app.main import app
        from app.services.notification_dispatcher import (
            start_dispatcher_task,
            stop_dispatcher_task,
        )

        token = create_token(subject=str(owner_user.id), token_type="access")

        # The real app's lifespan (which starts the dispatcher) isn't
        # triggered by this transport, so it's started explicitly here
        # -- this exercises the actual dispatcher code directly rather
        # than depending on ASGI lifespan semantics this particular
        # test transport doesn't implement.
        dispatcher_task = start_dispatcher_task(ws_manager)
        try:
            async with (
                httpx.AsyncClient(
                    transport=ASGIWebSocketTransport(app=app), base_url="http://test"
                ) as http_client,
                aconnect_ws(f"/api/v1/ws/notifications?token={token}", client=http_client) as ws,
            ):
                await asyncio.sleep(0.2)  # let the dispatcher's subscribe() settle
                await publish(
                    StockLowEvent(product_id=1, batch_id=1, qty_remaining=2, reorder_point=10)
                )

                message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                assert message["event_type"] == "stock.low"
                assert message["payload"]["product_id"] == 1
                assert message["payload"]["qty_remaining"] == 2
        finally:
            await stop_dispatcher_task(dispatcher_task)
