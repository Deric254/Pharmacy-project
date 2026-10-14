"""
AI provider adapter interface.

A provider adapter is pure transport: it sends one prompt and returns the
text. Adding a provider means writing one adapter class in adapters.py and
registering it in the map in ai_assistant_service.py -- the planner, the data
lookups, the fallback chain and the key storage are all provider-agnostic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class AIProviderError(Exception):
    """Raised by an adapter on any failure -- timeout, bad key, rate limit,
    malformed response. The assistant service catches this and moves on
    to the next configured provider; it never propagates to the user."""


@dataclass
class AIResponse:
    text: str


class AIProvider(ABC):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    async def complete(self, prompt: str) -> AIResponse:
        """Raises AIProviderError on any failure."""
