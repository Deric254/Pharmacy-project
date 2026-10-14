"""Helpers for the licence tests: a throwaway owner key and an in-memory store."""

from datetime import date
from typing import Any

from app.core.licence_gate import APP_ID, PREFIX
from licensing.licensing import Licensing


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def make_licensing(today: date | None = None) -> tuple[Licensing, str, bytes]:
    """A Licensing over a throwaway key: (instance, owner key text, owner key seed)."""
    key_text, public = Licensing.make_owner_key(PREFIX)
    kwargs: dict[str, Any] = {} if today is None else {"today": lambda: today}
    lic = Licensing(APP_ID, PREFIX, public, MemoryStore(), **kwargs)
    return lic, key_text, lic.parse_owner_key(key_text)
