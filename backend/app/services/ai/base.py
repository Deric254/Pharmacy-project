"""
AI provider adapter interface.

Adding a new provider later means writing one new adapter class here
and registering it in the map in ai_assistant_service.py -- nothing
else in the system changes. This is the Open/Closed Principle doing
real work: the panel, the fallback chain, and the key storage are all
already provider-agnostic.
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
    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        """Raises AIProviderError on any failure."""
