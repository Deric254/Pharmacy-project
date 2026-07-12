"""
WebSocket connection manager.

Tracks live connections per user and the permission set each connection
was authenticated with at connect time. Broadcasting a message checks
that permission set per-connection -- a cashier's screen never receives
a supplier-balance or backup-failure push, but the owner's does. This
is the actual mechanism behind "delivered live, filtered by what each
role should see," not just a description of intended behavior.

A single module-level instance (`manager`) is shared by the WebSocket
route and the notification dispatcher background task.
"""

from collections import defaultdict
from typing import Protocol

from app.models.user import User


class SendableConnection(Protocol):
    """
    Structural type covering both a real Starlette WebSocket and the
    fake connection objects used in tests -- only send_json is needed
    here, so tests don't need to stand up a real WebSocket protocol to
    exercise the manager's actual logic (permission filtering,
    broadcast routing, dead-connection cleanup).
    """

    async def send_json(self, data: dict[str, object]) -> None: ...


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[SendableConnection]] = defaultdict(set)
        self._permissions_by_connection: dict[SendableConnection, set[str]] = {}

    def connect(self, connection: SendableConnection, user: User) -> None:
        self._connections[user.id].add(connection)
        self._permissions_by_connection[connection] = {p.code for p in user.role.permissions}

    def disconnect(self, connection: SendableConnection, user_id: int) -> None:
        self._connections[user_id].discard(connection)
        self._permissions_by_connection.pop(connection, None)
        if not self._connections[user_id]:
            del self._connections[user_id]

    @property
    def connection_count(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    async def broadcast(self, message: dict[str, object], required_permission: str | None) -> None:
        """
        required_permission=None means every connected client receives
        the message regardless of role -- used for config.updated,
        since branding should refresh live for everyone, not just
        certain roles.
        """
        dead: list[tuple[int, SendableConnection]] = []
        for user_id, connections in self._connections.items():
            for connection in connections:
                permissions = self._permissions_by_connection.get(connection, set())
                if required_permission is not None and required_permission not in permissions:
                    continue
                try:
                    await connection.send_json(message)
                except Exception:  # noqa: BLE001 - a dead/broken connection must never crash the dispatcher
                    dead.append((user_id, connection))

        for user_id, connection in dead:
            self.disconnect(connection, user_id)


manager = ConnectionManager()
