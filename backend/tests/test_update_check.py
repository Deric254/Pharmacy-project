"""
Update check tests.

UpdateCheckService's HTTP calls are verified via httpx.MockTransport --
no live call to GitHub's actual API -- the same approach used for the
AI provider adapters and the Google Drive backup provider (see
test_ai_assistant.py / test_backups.py). The API-level tests only need
to prove auth-gating and correct status-code mapping, so they
monkeypatch the service method instead of re-mocking HTTP transport.
"""

import httpx

from app import __version__
from app.core.redis_client import redis_client
from app.services.update_check_service import (
    UpdateCheckError,
    UpdateCheckService,
    _is_newer,
    _normalize_version,
)


def _release(tag_name: str, assets: list[dict] | None = None, html_url: str = "") -> dict:
    return {"tag_name": tag_name, "html_url": html_url, "assets": assets or []}


def _installer_asset(version: str) -> dict:
    name = f"Pharmacy-ERP-Setup-{version}.exe"
    return {"name": name, "browser_download_url": f"https://example.com/{name}"}


def _mock_service(handler) -> UpdateCheckService:
    return UpdateCheckService(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def _login(client, username: str, password: str) -> str:
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestVersionComparison:
    def test_normalizes_leading_v(self):
        assert _normalize_version("v1.2.3") == "1.2.3"
        assert _normalize_version("1.2.3") == "1.2.3"

    def test_compares_numerically_not_lexicographically(self):
        # A naive string comparison would say "1.9.0" > "1.10.0".
        assert _is_newer("v1.10.0", "1.9.0") is True
        assert _is_newer("v1.9.0", "1.10.0") is False

    def test_equal_versions_are_not_newer(self):
        assert _is_newer("v2.0.0", "2.0.0") is False


class TestGetLatest:
    async def test_returns_none_when_already_on_latest_version(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_release(f"v{__version__}"))

        service = _mock_service(handler)
        assert await service.get_latest() is None

    async def test_returns_info_and_the_real_installer_asset_when_newer(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_release(
                    "v99.0.0",
                    assets=[
                        {
                            "name": "pharmacy-backend.exe",
                            "browser_download_url": "https://example.com/raw",
                        },
                        _installer_asset("99.0.0"),
                    ],
                    html_url="https://github.com/releases/v99.0.0",
                ),
            )

        service = _mock_service(handler)
        info = await service.get_latest()

        assert info is not None
        assert info["current_version"] == __version__
        assert info["latest_version"] == "99.0.0"
        assert info["download_url"] == "https://example.com/Pharmacy-ERP-Setup-99.0.0.exe"
        assert info["release_url"] == "https://github.com/releases/v99.0.0"

    async def test_network_failure_returns_none_rather_than_raising(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated network failure", request=request)

        service = _mock_service(handler)
        assert await service.get_latest() is None

    async def test_non_200_response_returns_none_rather_than_raising(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "rate limited"})

        service = _mock_service(handler)
        assert await service.get_latest() is None

    async def test_second_call_is_served_from_cache_not_a_second_network_call(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json=_release(f"v{__version__}"))

        service = _mock_service(handler)
        await service.get_latest()
        await service.get_latest()

        assert len(calls) == 1

    async def test_force_bypasses_the_cache(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json=_release(f"v{__version__}"))

        service = _mock_service(handler)
        await service.get_latest()
        await service.get_latest(force=True)

        assert len(calls) == 2

    async def test_caches_the_no_update_result_too_not_just_a_real_update(self):
        # A naive cache that only stores truthy results would re-hit
        # GitHub on every call for the common case (already current).
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json=_release(f"v{__version__}"))

        service = _mock_service(handler)
        await service.get_latest()
        cached = await redis_client.get("update_check:latest:v1")

        assert cached is not None
        assert len(calls) == 1

    async def test_authenticated_request_carries_the_configured_token(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "github_update_token", "gh-token-abc123")

        service = UpdateCheckService()  # no client override -- exercises the real header setup

        assert service._client.headers.get("authorization") == "Bearer gh-token-abc123"

    async def test_no_authorization_header_when_no_token_configured(self):
        service = UpdateCheckService()

        assert "authorization" not in service._client.headers


class TestGetReleases:
    async def test_excludes_releases_with_no_installer_asset(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    _release("v1.1.0", assets=[]),
                    _release("v1.0.0", assets=[_installer_asset("1.0.0")]),
                ],
            )

        service = _mock_service(handler)
        releases = await service.get_releases()

        assert len(releases) == 1
        assert releases[0]["version"] == "1.0.0"

    async def test_flags_the_currently_installed_version(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    _release(f"v{__version__}", assets=[_installer_asset(__version__)]),
                    _release("v0.0.1", assets=[_installer_asset("0.0.1")]),
                ],
            )

        service = _mock_service(handler)
        releases = await service.get_releases()

        current = next(r for r in releases if r["version"] == __version__)
        other = next(r for r in releases if r["version"] == "0.0.1")
        assert current["is_current"] is True
        assert other["is_current"] is False

    async def test_raises_on_network_failure_rather_than_returning_an_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated network failure", request=request)

        service = _mock_service(handler)
        raised = False
        try:
            await service.get_releases()
        except UpdateCheckError:
            raised = True
        assert raised

    async def test_raises_on_non_200_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "rate limited"})

        service = _mock_service(handler)
        raised = False
        try:
            await service.get_releases()
        except UpdateCheckError:
            raised = True
        assert raised


class TestUpdatesApi:
    async def test_latest_requires_auth(self, client):
        r = await client.get("/api/v1/updates/latest")
        assert r.status_code == 401

    async def test_releases_requires_auth(self, client):
        r = await client.get("/api/v1/updates/releases")
        assert r.status_code == 401

    async def test_latest_returns_the_service_result_when_authenticated(
        self, client, owner_user, monkeypatch
    ):
        async def fake_get_latest(self, *, force=False):
            return {
                "current_version": "1.0.0",
                "latest_version": "1.1.0",
                "download_url": "https://example.com/installer.exe",
                "release_url": "https://github.com/releases/v1.1.0",
            }

        monkeypatch.setattr(
            "app.services.update_check_service.UpdateCheckService.get_latest", fake_get_latest
        )

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/updates/latest", headers={"Authorization": f"Bearer {token}"}
        )

        assert r.status_code == 200
        assert r.json() == {
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "download_url": "https://example.com/installer.exe",
            "release_url": "https://github.com/releases/v1.1.0",
        }

    async def test_latest_returns_null_when_no_update_available(
        self, client, owner_user, monkeypatch
    ):
        async def fake_get_latest(self, *, force=False):
            return None

        monkeypatch.setattr(
            "app.services.update_check_service.UpdateCheckService.get_latest", fake_get_latest
        )

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/updates/latest", headers={"Authorization": f"Bearer {token}"}
        )

        assert r.status_code == 200
        assert r.json() is None

    async def test_releases_returns_502_when_github_is_unreachable(
        self, client, owner_user, monkeypatch
    ):
        async def fake_get_releases(self, *, force=False):
            raise UpdateCheckError("Could not reach GitHub to list releases.")

        monkeypatch.setattr(
            "app.services.update_check_service.UpdateCheckService.get_releases", fake_get_releases
        )

        token = await _login(client, "lucy", "S3curePass!")
        r = await client.get(
            "/api/v1/updates/releases", headers={"Authorization": f"Bearer {token}"}
        )

        assert r.status_code == 502
