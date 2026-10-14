"""
Licence gate tests: a locked copy is refused everywhere that matters (HTTP and
WebSocket), the activation endpoints stay open, and a licence opens, renews and
expires the copy. Uses throwaway keys only -- never the real owner key.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from licence_helpers import MemoryStore, make_licensing

from app.core import licence_gate
from app.core.licence_gate import (
    LICENCE_REQUIRED_CLOSE_CODE,
    LicenceGateMiddleware,
    is_gated,
    use_licensing,
)
from licensing.licensing import Licensing


@pytest.fixture
def locked():
    """A copy with a valid build key but no licence: (licensing, owner seed, owner key text)."""
    lic, key_text, seed = make_licensing()
    use_licensing(lic)
    return lic, seed, key_text


class TestGatedPaths:
    def test_api_routes_are_gated(self):
        assert is_gated("/api/v1/auth/me")
        assert is_gated("/api/v1/ws/notifications")
        assert is_gated("/api/v1/licence")  # only the licence sub-paths are open

    def test_licence_endpoints_health_and_static_files_stay_open(self):
        assert not is_gated("/api/v1/licence/state")
        assert not is_gated("/api/v1/licence/activate")
        assert not is_gated("/health")
        assert not is_gated("/assets/index-abc.js")
        assert not is_gated("/pos")


class TestLockedCopy:
    async def test_api_request_is_refused_with_402(self, client, locked):
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 402
        body = r.json()
        assert body["licence"] == {"ok": False, "kind": "unlicensed"}
        assert "licensed" in body["detail"]

    async def test_first_user_setup_is_gated_too(self, client, locked):
        r = await client.get("/api/v1/setup/status")
        assert r.status_code == 402

    async def test_health_and_licence_state_stay_reachable(self, client, locked):
        assert (await client.get("/health")).status_code == 200
        r = await client.get("/api/v1/licence/state")
        assert r.status_code == 200
        state = r.json()
        assert state["ok"] is False
        assert len(state["install_id"].replace("-", "")) == 12

    async def test_build_without_a_public_key_fails_closed(self, client):
        use_licensing(Licensing("Pharmacy ERP", "PHE", "", MemoryStore()))
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 402
        assert r.json()["licence"]["kind"] == "unconfigured"
        state = (await client.get("/api/v1/licence/state")).json()
        assert state["ok"] is False
        assert "install_id" not in state


class TestActivation:
    async def test_licence_for_this_computer_opens_the_app(self, client, locked):
        lic, seed, _ = locked
        code = (await client.get("/api/v1/licence/state")).json()["install_id"]
        token = lic.issue(seed, code, "Lucy Chemist", 30)["licence"]
        r = await client.post("/api/v1/licence/activate", json={"licence": token})
        assert r.status_code == 200
        assert r.json()["kind"] == "licensed"
        assert r.json()["user"] == "Lucy Chemist"
        # Past the gate now: the route answers for itself (401, no token) instead of 402.
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_licence_for_another_computer_is_rejected(self, client, locked):
        lic, seed, _ = locked
        token = lic.issue(seed, "ABCD-EFGH-JKLM", "Lucy", 30)["licence"]
        r = await client.post("/api/v1/licence/activate", json={"licence": token})
        assert r.status_code == 400
        assert "different computer" in r.json()["detail"]
        assert (await client.get("/api/v1/auth/me")).status_code == 402

    async def test_garbage_is_rejected(self, client, locked):
        r = await client.post("/api/v1/licence/activate", json={"licence": "not a licence"})
        assert r.status_code == 400

    async def test_renewal_must_be_for_the_same_person(self, client, locked):
        lic, seed, _ = locked
        code = lic.install_id()
        first = lic.issue(seed, code, "Lucy", 30)["licence"]
        assert (
            await client.post("/api/v1/licence/activate", json={"licence": first})
        ).status_code == 200
        other = lic.issue(seed, code, "Someone Else", 30)["licence"]
        r = await client.post("/api/v1/licence/activate", json={"licence": other})
        assert r.status_code == 400
        same = lic.issue(seed, code, "lucy", 60)["licence"]
        assert (
            await client.post("/api/v1/licence/activate", json={"licence": same})
        ).status_code == 200

    async def test_licence_stops_working_after_it_expires(self, client):
        today = {"d": date(2026, 1, 1)}
        lic, key_text, seed = make_licensing()
        lic._today = lambda: today["d"]
        use_licensing(lic)
        token = lic.issue(seed, lic.install_id(), "Lucy", 1)["licence"]
        assert (
            await client.post("/api/v1/licence/activate", json={"licence": token})
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        today["d"] += timedelta(days=3)
        licence_gate.reset_cache()
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 402
        assert "expired" in r.json()["detail"]


class TestOwnerClaim:
    async def test_wrong_owner_key_is_rejected(self, client, locked):
        other_text, _ = Licensing.make_owner_key("PHE")
        r = await client.post("/api/v1/licence/claim-owner", json={"owner_key": other_text})
        assert r.status_code == 400
        assert (await client.get("/api/v1/auth/me")).status_code == 402

    async def test_real_owner_key_opens_this_copy(self, client, locked):
        _, _, key_text = locked
        r = await client.post("/api/v1/licence/claim-owner", json={"owner_key": key_text})
        assert r.status_code == 200
        assert r.json()["kind"] == "owner"
        assert (await client.get("/api/v1/auth/me")).status_code == 401


class TestMiddleware:
    async def test_locked_websocket_handshake_is_closed_before_the_app_runs(self, locked):
        reached: list[str] = []
        sent: list[dict[str, Any]] = []

        async def inner(scope, receive, send):
            reached.append(scope["path"])

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "websocket", "path": "/api/v1/ws/notifications"}
        await LicenceGateMiddleware(inner)(scope, receive, send)
        assert reached == []
        assert sent == [{"type": "websocket.close", "code": LICENCE_REQUIRED_CLOSE_CODE}]

    async def test_licensed_websocket_and_non_api_requests_pass_through(self, client):
        # The autouse fixture made this an owner copy, so everything is let through.
        reached: list[str] = []

        async def inner(scope, receive, send):
            reached.append(scope["path"])

        mw = LicenceGateMiddleware(inner)
        for scope in (
            {"type": "websocket", "path": "/api/v1/ws/notifications"},
            {"type": "http", "path": "/api/v1/products"},
            {"type": "lifespan", "path": ""},
        ):
            await mw(scope, None, None)
        assert reached == ["/api/v1/ws/notifications", "/api/v1/products", ""]

    async def test_non_api_paths_are_not_checked_at_all(self, locked):
        lic, _, _ = locked
        calls = 0
        original = lic.state

        def counting() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return original()

        lic.state = counting  # type: ignore[method-assign]
        reached: list[str] = []

        async def inner(scope, receive, send):
            reached.append(scope["path"])

        await LicenceGateMiddleware(inner)({"type": "http", "path": "/health"}, None, None)
        assert reached == ["/health"]
        assert calls == 0

    async def test_state_is_cached_between_requests_and_reset_on_activation(self, locked):
        lic, _, _ = locked
        calls = 0
        original = lic.state

        def counting() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return original()

        lic.state = counting  # type: ignore[method-assign]
        await licence_gate.current_state()
        await licence_gate.current_state()
        assert calls == 1
        licence_gate.reset_cache()
        await licence_gate.current_state()
        assert calls == 2
