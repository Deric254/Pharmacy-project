"""
Concrete provider adapters.

Each adapter's `_client` can be overridden (constructor param) so tests can
inject an httpx.MockTransport and verify the actual request shape (URL,
headers, payload) without hitting a live, paid, third-party API in CI.
Production code never sets this -- it defaults to a real AsyncClient hitting
the provider's real endpoint.

Model IDs are retired on a schedule (Claude's claude-3-5-sonnet-20241022 died
on 2026-01-05 and silently failed every request until it was found), so check
each provider's deprecations page now and then. Every per-provider failure is
logged by the assistant service, which is how a dead ID shows up.
"""

from typing import Any

import httpx

from app.services.ai.base import AIProvider, AIProviderError, AIResponse

_TIMEOUT_SECONDS = 20.0


class _HttpAdapter(AIProvider):
    _NAME: str

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AIProviderError(f"{self._NAME} request failed: {exc}") from exc

    def _text(self, data: Any, path: tuple[str | int, ...]) -> AIResponse:
        try:
            for step in path:
                data = data[step]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"{self._NAME} request failed: unexpected response") from exc
        return AIResponse(text=data)


class _OpenAICompatibleAdapter(_HttpAdapter):
    _URL: str
    _MODEL: str

    async def complete(self, prompt: str) -> AIResponse:
        data = await self._post(
            self._URL,
            {"Authorization": f"Bearer {self.api_key}"},
            {"model": self._MODEL, "messages": [{"role": "user", "content": prompt}]},
        )
        return self._text(data, ("choices", 0, "message", "content"))


class OpenAIAdapter(_OpenAICompatibleAdapter):
    _NAME = "OpenAI"
    _URL = "https://api.openai.com/v1/chat/completions"
    _MODEL = "gpt-4o-mini"


class DeepSeekAdapter(_OpenAICompatibleAdapter):
    _NAME = "DeepSeek"
    _URL = "https://api.deepseek.com/chat/completions"
    _MODEL = "deepseek-chat"


class NvidiaAdapter(_OpenAICompatibleAdapter):
    _NAME = "NVIDIA"
    _URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    _MODEL = "nvidia/nemotron-3-super-120b-a12b"


class ClaudeAdapter(_HttpAdapter):
    _NAME = "Claude"

    async def complete(self, prompt: str) -> AIResponse:
        data = await self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": "claude-sonnet-5",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        return self._text(data, ("content", 0, "text"))


class GeminiAdapter(_HttpAdapter):
    _NAME = "Gemini"

    async def complete(self, prompt: str) -> AIResponse:
        # The key goes in a header, never a ?key=... query parameter: a URL ends
        # up in proxy logs and in httpx's own error messages.
        data = await self._post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent",
            {"x-goog-api-key": self.api_key},
            {"contents": [{"parts": [{"text": prompt}]}]},
        )
        return self._text(data, ("candidates", 0, "content", "parts", 0, "text"))
