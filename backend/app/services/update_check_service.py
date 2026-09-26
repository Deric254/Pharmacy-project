"""
Update check service.

Talks to GitHub on the backend's behalf instead of the Electron
renderer calling api.github.com directly. Two concrete wins over the
old renderer-side call:

1. An optional `github_update_token` setting (blank by default)
   authenticates the request, moving it off GitHub's unauthenticated
   60-requests/hour-per-IP limit onto the much larger authenticated
   limit -- worth having because every desktop install behind the same
   hospital/office NAT shares one public IP against that limit.
2. The result is cached in Redis for CACHE_TTL_SECONDS (same cache-
   aside pattern as business_config_service.py), so opening the app --
   or the Settings page, which mounts its own independent check --
   doesn't repeat the external call every time within the cache
   window. The renderer now only ever waits on a local loopback call
   to this backend, never directly on GitHub.

`client` is injectable the same way GoogleDriveBackupProvider's is:
production leaves it unset and gets a real httpx.AsyncClient; tests
inject an httpx.MockTransport-backed one instead of making a live call.
"""

import json
from typing import Any, cast

import httpx

from app import __version__
from app.core.config import get_settings
from app.core.redis_client import redis_client

_REPO = "Deric254/Pharmacy-project"
_TIMEOUT_SECONDS = 8.0
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
_LATEST_CACHE_KEY = "update_check:latest:v1"
_RELEASES_CACHE_KEY = "update_check:releases:v1"
_INSTALLER_PREFIX = "Pharmacy-ERP-Setup-"


class UpdateCheckError(Exception):
    """Raised when GitHub can't be reached or returns a non-2xx response."""


def _normalize_version(tag: str) -> str:
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _is_newer(latest: str, current: str) -> bool:
    a = [int(part) for part in _normalize_version(latest).split(".")]
    b = [int(part) for part in _normalize_version(current).split(".")]
    for i in range(max(len(a), len(b))):
        diff = (a[i] if i < len(a) else 0) - (b[i] if i < len(b) else 0)
        if diff != 0:
            return diff > 0
    return False


def _installer_download_url(assets: list[dict[str, Any]]) -> str | None:
    for asset in assets:
        name = asset.get("name", "")
        if name.startswith(_INSTALLER_PREFIX) and name.endswith(".exe"):
            url = asset.get("browser_download_url")
            return url if isinstance(url, str) else None
    return None


class UpdateCheckService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            settings = get_settings()
            headers = {"Accept": "application/vnd.github+json"}
            if settings.github_update_token:
                headers["Authorization"] = f"Bearer {settings.github_update_token}"
            self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=headers)

    async def get_latest(self, *, force: bool = False) -> dict[str, Any] | None:
        """Returns None when already on the latest version, or on any failure -- this
        is a best-effort background check, never something worth surfacing as an
        error to a pharmacy owner running the app."""
        if not force:
            cached = await redis_client.get(_LATEST_CACHE_KEY)
            if cached is not None:
                return cast("dict[str, Any] | None", json.loads(cached))

        try:
            response = await self._client.get(
                f"https://api.github.com/repos/{_REPO}/releases/latest"
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None

        release = response.json()
        info: dict[str, Any] | None = None
        if _is_newer(release.get("tag_name", ""), __version__):
            info = {
                "current_version": __version__,
                "latest_version": _normalize_version(release.get("tag_name", "")),
                "download_url": _installer_download_url(release.get("assets", [])),
                "release_url": release.get("html_url", ""),
            }

        await redis_client.set(_LATEST_CACHE_KEY, json.dumps(info), ex=_CACHE_TTL_SECONDS)
        return info

    async def get_releases(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Raises UpdateCheckError on failure -- unlike get_latest(), this backs an
        explicit "release history" screen the user asked to see, so a failure needs
        to be visible rather than silently swallowed."""
        if not force:
            cached = await redis_client.get(_RELEASES_CACHE_KEY)
            if cached is not None:
                return cast("list[dict[str, Any]]", json.loads(cached))

        try:
            response = await self._client.get(f"https://api.github.com/repos/{_REPO}/releases")
        except httpx.HTTPError as exc:
            raise UpdateCheckError("Could not reach GitHub to list releases.") from exc
        if response.status_code != 200:
            raise UpdateCheckError("Could not reach GitHub to list releases.")

        options = []
        for release in response.json():
            download_url = _installer_download_url(release.get("assets", []))
            if download_url is None:
                continue
            options.append(
                {
                    "version": _normalize_version(release.get("tag_name", "")),
                    "download_url": download_url,
                    "release_url": release.get("html_url", ""),
                    "is_current": _normalize_version(release.get("tag_name", "")) == __version__,
                }
            )

        await redis_client.set(_RELEASES_CACHE_KEY, json.dumps(options), ex=_CACHE_TTL_SECONDS)
        return options
