"""
Local file backup provider.

Writes directly to a folder on disk -- no network, no OAuth, nothing
to connect first. This is deliberately the default provider: the
confirmed bug this exists to close is that every backup attempt
required Google Drive to be connected first, with no offline path at
all, directly contradicting this app's whole design (one computer, no
network dependency). Google Drive stays available as an optional
additional layer for anyone who specifically wants an off-site copy.
"""

import asyncio
from pathlib import Path

from app.services.backup.base import BackupProvider, BackupProviderError


class LocalFileBackupProvider(BackupProvider):
    def __init__(self, backup_dir: Path) -> None:
        self.backup_dir = backup_dir

    async def upload(self, filename: str, data: bytes) -> str:
        # File I/O is genuinely blocking; a real database dump can be
        # a few MB, so this runs off the event loop rather than
        # stalling every other request mid-write. There's no analogous
        # concern in Google Drive's provider since that one is already
        # async over the network.
        path = self.backup_dir / filename

        def _write() -> str:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return str(path)

        try:
            return await asyncio.to_thread(_write)
        except OSError as exc:
            raise BackupProviderError(f"Could not write backup file: {exc}") from exc

    async def download(self, reference: str) -> bytes:
        path = Path(reference)

        def _read() -> bytes:
            if not path.exists():
                raise BackupProviderError(f"Backup file not found: {path}")
            return path.read_bytes()

        try:
            return await asyncio.to_thread(_read)
        except OSError as exc:
            raise BackupProviderError(f"Could not read backup file: {exc}") from exc
