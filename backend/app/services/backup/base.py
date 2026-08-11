"""
Backup provider interface.

Same Open/Closed pattern as the AI provider adapters: swapping in
Dropbox or OneDrive later means one new class implementing this
interface, nothing else in BackupService changes.
"""

from abc import ABC, abstractmethod


class BackupProviderError(Exception):
    """Raised on any upload/download failure -- auth, network, quota."""


class BackupProvider(ABC):
    @abstractmethod
    async def upload(self, filename: str, data: bytes) -> str:
        """Returns a provider-specific reference (e.g. a file ID) for later retrieval."""

    @abstractmethod
    async def download(self, reference: str) -> bytes:
        """Raises BackupProviderError if the reference can't be retrieved."""
