"""
Google Drive backup provider.

Uses a pre-obtained OAuth refresh token (set up once by an admin via
Google's standard OAuth consent flow, stored encrypted) to mint short-
lived access tokens on demand -- this backend never needs to run an
interactive browser consent screen itself, that happens once outside
the app when the admin connects their Google account.
"""

from typing import Any

import httpx

from app.services.backup.base import BackupProvider, BackupProviderError

_TIMEOUT_SECONDS = 30.0
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=media"
_DOWNLOAD_URL_TEMPLATE = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"


class GoogleDriveBackupProvider(BackupProvider):
    def __init__(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def _get_access_token(self) -> str:
        try:
            response = await self._client.post(
                _TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return str(data["access_token"])
        except (httpx.HTTPError, KeyError) as exc:
            raise BackupProviderError(f"Google token refresh failed: {exc}") from exc

    async def upload(self, filename: str, data: bytes) -> str:
        access_token = await self._get_access_token()
        try:
            response = await self._client.post(
                _UPLOAD_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/octet-stream",
                },
                content=data,
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return str(result["id"])
        except (httpx.HTTPError, KeyError) as exc:
            raise BackupProviderError(f"Google Drive upload failed: {exc}") from exc

    async def download(self, reference: str) -> bytes:
        access_token = await self._get_access_token()
        try:
            response = await self._client.get(
                _DOWNLOAD_URL_TEMPLATE.format(file_id=reference),
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            raise BackupProviderError(f"Google Drive download failed: {exc}") from exc
