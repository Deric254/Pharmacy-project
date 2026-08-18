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

    async def test_a_new_connection_arriving_mid_broadcast_does_not_crash_it(
        self, owner_user, employee_user
    ):
        """
        broadcast() iterates self._connections directly while awaiting
        connection.send_json() inside that loop -- a real yield point,
        giving a genuine window for another coroutine to run. If a
        BRAND NEW user connects during that exact window (not just an
        existing user's set gaining a second connection, but a new key
        being added to the outer dict being iterated), Python's own
        dict-iteration protocol can raise "dictionary changed size
        during iteration" -- uncaught by broadcast()'s own try/except,
        which only wraps send_json() itself, not the iteration. If
        that ever happens for real, it doesn't just fail one message:
        it propagates out of dispatch_one_message and kills the
        notification dispatcher's background task permanently, for
        the rest of the app's uptime, with nothing to restart it.

        Uses an explicit asyncio.Event, not a bare `asyncio.sleep(0)`,
        to GUARANTEE the new connection lands while broadcast() is
        genuinely paused inside its send_json() await -- a timing
        race relying on scheduler luck could pass by accident without
        ever actually exercising the dangerous window, which would
        prove nothing either way.
        """
        entered_send = asyncio.Event()
        release_send = asyncio.Event()

        class GatedConnection:
            def __init__(self) -> None:
                self.sent: list[dict[str, object]] = []

            async def send_json(self, data: dict[str, object]) -> None:
                entered_send.set()
                await release_send.wait()  # held open until the test says go
                self.sent.append(data)

        manager = ConnectionManager()
        first_conn = GatedConnection()
        manager.connect(first_conn, owner_user)

        async def broadcast_task() -> None:
            await manager.broadcast({"event_type": "stock.low"}, required_permission=None)

        async def connect_new_user_once_broadcast_is_mid_flight() -> None:
            await entered_send.wait()  # broadcast is now genuinely paused inside send_json
            new_conn = GatedConnection()
            manager.connect(new_conn, employee_user)  # a genuinely new dict key
            release_send.set()  # let the paused broadcast continue

        results = await asyncio.gather(
            broadcast_task(),
            connect_new_user_once_broadcast_is_mid_flight(),
            return_exceptions=True,
        )
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, (
            f"DISPATCHER-KILLING BUG CONFIRMED: a new connection arriving mid-broadcast "
            f"raised instead of being handled: {exceptions}"
        )

    async def test_a_disconnect_mid_broadcast_does_not_crash_it(self, owner_user):
        """
        Same class of bug as the connect case above, different
        trigger: this user's OWN second tab disconnecting while a
        broadcast is actively iterating that user's connection set,
        not the outer dict. Same fix (list(...) snapshotting) has to
        cover both -- proven separately since the inner-set path and
        the outer-dict path are different objects that could, in
        principle, have been fixed at only one level by mistake.
        """
        entered_send = asyncio.Event()
        release_send = asyncio.Event()

        class GatedConnection:
            def __init__(self) -> None:
                self.sent: list[dict[str, object]] = []

            async def send_json(self, data: dict[str, object]) -> None:
                entered_send.set()
                await release_send.wait()
                self.sent.append(data)

        manager = ConnectionManager()
        tab_one = GatedConnection()
        tab_two = FakeConnection()
        manager.connect(tab_one, owner_user)
        manager.connect(tab_two, owner_user)

        async def broadcast_task() -> None:
            await manager.broadcast({"event_type": "stock.low"}, required_permission=None)

        async def disconnect_second_tab_once_broadcast_is_mid_flight() -> None:
            await entered_send.wait()
            manager.disconnect(tab_two, owner_user.id)  # mutates the set broadcast is iterating
            release_send.set()

        results = await asyncio.gather(
            broadcast_task(),
            disconnect_second_tab_once_broadcast_is_mid_flight(),
            return_exceptions=True,
        )
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, f"DISPATCHER-KILLING BUG (disconnect path): {exceptions}"


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

    async def test_sale_completed_gated_behind_reports_view(self, owner_user, employee_user):
        """
        Real gap this closes: the map lists sale.completed ->
        reports.view, but nothing actually dispatched this event type
        through dispatch_one_message before -- a typo'd permission
        string here would have gone completely undetected, since nothing
        exercised this exact routing.
        """
        manager = ConnectionManager()
        owner_conn = FakeConnection()
        employee_conn = FakeConnection()
        manager.connect(owner_conn, owner_user)
        manager.connect(employee_conn, employee_user)

        envelope = json.dumps({"event_type": "sale.completed", "payload": {"sale_id": 1}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        # ChemistOwner (seeded_roles) carries every permission,
        # including reports.view -- receives it. Employee's seeded
        # permission set is deliberately narrow (sales.create,
        # inventory.view, stocktake.perform, ai.use only) and does NOT
        # include reports.view -- confirmed directly against
        # conftest.py's actual role definitions, not assumed.
        assert len(owner_conn.sent) == 1
        assert owner_conn.sent[0]["event_type"] == "sale.completed"
        assert employee_conn.sent == []

    async def test_po_status_changed_reaches_only_purchasing_create_po(
        self, owner_user, employee_user
    ):
        manager = ConnectionManager()
        owner_conn = FakeConnection()
        employee_conn = FakeConnection()
        manager.connect(owner_conn, owner_user)
        manager.connect(employee_conn, employee_user)

        envelope = json.dumps({"event_type": "po.status_changed", "payload": {"po_id": 5}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert len(owner_conn.sent) == 1  # ChemistOwner has purchasing.create_po
        assert employee_conn.sent == []  # Employee's permission set does not

    async def test_stocktake_closed_reaches_reports_view(self, owner_user):
        manager = ConnectionManager()
        conn = FakeConnection()
        manager.connect(conn, owner_user)

        envelope = json.dumps({"event_type": "stocktake.closed", "payload": {"stock_take_id": 3}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert len(conn.sent) == 1
        assert conn.sent[0]["payload"]["stock_take_id"] == 3

    async def test_an_unrecognized_future_event_type_falls_back_to_reports_view(
        self, owner_user, employee_user
    ):
        """
        _DEFAULT_PERMISSION, not an open broadcast -- a brand new event
        type nobody has added to EVENT_PERMISSION_MAP yet must still be
        gated behind a real permission, not silently reach everyone
        connected regardless of role. Proven in both directions: an
        owner (who has reports.view) receives it, an employee (who,
        confirmed directly against conftest.py, does not have
        reports.view) does not -- ruling out both "the fallback is
        broken" and "the fallback is actually an open broadcast in
        disguise".
        """
        manager = ConnectionManager()
        owner_conn = FakeConnection()
        employee_conn = FakeConnection()
        manager.connect(owner_conn, owner_user)
        manager.connect(employee_conn, employee_user)

        envelope = json.dumps({"event_type": "totally.unrecognized.future.event", "payload": {}})
        await dispatch_one_message(manager, {"type": "message", "data": envelope})

        assert len(owner_conn.sent) == 1
        assert owner_conn.sent[0]["event_type"] == "totally.unrecognized.future.event"
        assert employee_conn.sent == []

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
            assert (
                permission in known_permission_like_values
            ), f"{event_type} maps to an unexpected permission value: {permission}"


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
