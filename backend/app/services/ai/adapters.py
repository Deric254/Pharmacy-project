"""
Concrete provider adapters.

Each adapter's `_client` can be overridden (constructor param) so tests
can inject an httpx.MockTransport and verify the actual request shape
(URL, headers, payload) without hitting a live, paid, third-party API
in CI. Production code never sets this -- it defaults to a real
AsyncClient hitting the provider's real endpoint.
"""

from typing import Any

import httpx

from app.services.ai.base import AIProvider, AIProviderError, AIResponse

_TIMEOUT_SECONDS = 20.0


def _build_prompt_with_context(prompt: str, context: dict[str, object]) -> str:
    if not context:
        return prompt
    context_lines = "\n".join(f"- {key}: {value}" for key, value in context.items())
    return f"Context:\n{context_lines}\n\nQuestion: {prompt}"


class OpenAIAdapter(AIProvider):
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        try:
            response = await self._client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": _build_prompt_with_context(prompt, context)}
                    ],
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return AIResponse(text=data["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc


class ClaudeAdapter(AIProvider):
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        try:
            response = await self._client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 1000,
                    "messages": [
                        {"role": "user", "content": _build_prompt_with_context(prompt, context)}
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            return AIResponse(text=data["content"][0]["text"])
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise AIProviderError(f"Claude request failed: {exc}") from exc


class GeminiAdapter(AIProvider):
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        try:
            response = await self._client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-1.5-flash:generateContent?key={self.api_key}",
                json={
                    "contents": [{"parts": [{"text": _build_prompt_with_context(prompt, context)}]}]
                },
            )
            response.raise_for_status()
            data = response.json()
            return AIResponse(text=data["candidates"][0]["content"]["parts"][0]["text"])
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc


class DeepSeekAdapter(AIProvider):
    """DeepSeek's API is OpenAI-compatible."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        try:
            response = await self._client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "user", "content": _build_prompt_with_context(prompt, context)}
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            return AIResponse(text=data["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise AIProviderError(f"DeepSeek request failed: {exc}") from exc


class NvidiaAdapter(AIProvider):
    """NVIDIA NIM's API is also OpenAI-compatible."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(api_key)
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def ask(self, prompt: str, context: dict[str, object]) -> AIResponse:
        try:
            response = await self._client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "meta/llama-3.1-8b-instruct",
                    "messages": [
                        {"role": "user", "content": _build_prompt_with_context(prompt, context)}
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            return AIResponse(text=data["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise AIProviderError(f"NVIDIA request failed: {exc}") from exc
