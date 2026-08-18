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


_APP_KNOWLEDGE = """\
This is a pharmacy management system for a single pharmacy, used by \
an Owner, an Administrator, and Employees (permissions differ by \
role -- an Employee can sell and count stock but can't see profit \
margins, manage users, or manage backups).

Where things live, for "how do I..." questions:
- POS (point of sale): the main selling screen -- search or scan a \
product, add to cart, optionally attach a customer by name or phone, \
apply a discount, choose a payment method, check out. The receipt \
prints automatically afterward if a printer is connected.
- Inventory: the product catalog, stock levels, and low-stock/expiring \
alerts. Receiving new stock happens via Purchasing's "Quick Purchase" \
-- pick a supplier, add each product with quantity, batch number, \
expiry date, and cost.
- Sales (history): past sales, viewing a receipt again, and \
processing refunds against a specific past sale.
- Customers: customer records, purchase history, and lifetime value.
- Stock Takes: counting physical stock against what the system \
expects; an open stock take locks its products out of sale until \
it's closed (or cancelled, which releases the lock without requiring \
a finished count).
- Reports/Dashboard: revenue trend, top products, top customers, \
stock valuation.
- Settings: business name, logo, currency, tax rate, backup location, \
and user accounts (Owner/Administrator only).
- Backups: routine same-device backups, plus a separate "export for a \
new device" for moving to different hardware entirely.
- AI Settings: provider keys are shared across the whole team and \
managed by the Owner or Administrator -- an Employee uses whatever \
key is already configured and never needs to add their own.
"""

_FORMATTING_RULES = """\
Write in plain, well-organized prose -- short paragraphs, and plain \
sentences most of the time. The only formatting the chat window can \
actually display is **bold** (double asterisks) and simple lists \
using "-" or "1." at the start of a line. Never use headers (#, ##), \
tables, or any other markdown syntax -- anything else shows up as \
literal stray characters on screen, not real formatting. Keep answers \
focused and skip unnecessary preamble. Every monetary figure below \
already carries the business's real currency code inline (e.g. \
"KES 1553.68") -- always use that same currency when discussing money \
in your answer, including any new figures you compute yourself (like \
a percentage of a given amount); never default to $ or USD unless \
that genuinely is the currency shown.
"""

_BUSINESS_INSIGHT_CLOSING = """\
End every single reply, no matter what was asked -- including a \
plain greeting like "good morning" with no business question in it \
at all -- with a short closing section, separated from the rest of \
your answer by a blank line and starting with **How the business is \
doing**. In 2-4 sentences, using only the real figures given above, \
cover: (1) current performance in plain terms, (2) the trajectory -- \
state the actual percent change vs the prior period if it was given, \
or say plainly that there isn't enough history yet to show a trend if \
it wasn't, never invent a direction either way, and (3) exactly one \
concrete, specific next action grounded in the real numbers given \
(e.g. a real low-stock or expiring-batch count, a real top product, a \
real revenue trend) -- not generic advice like "focus on marketing" \
that isn't actually tied to anything in the data. If no business \
figures were given at all this turn, say briefly that you don't have \
today's numbers to hand rather than inventing any -- never state a \
performance figure, a trend, or a recommendation that isn't directly \
backed by a real number that appeared above.
"""


def _build_prompt_with_context(prompt: str, context: dict[str, object]) -> str:
    name = context.get("person_asking_name")
    greeting_note = (
        f'The person asking is named "{name}" -- use their name naturally, '
        "not on every reply.\n\n"
        if name
        else ""
    )
    other_context = {k: v for k, v in context.items() if k != "person_asking_name"}
    context_lines = "\n".join(f"- {k}: {v}" for k, v in other_context.items())
    context_block = f"Current real business data:\n{context_lines}\n\n" if other_context else ""
    preamble = (
        f"{_APP_KNOWLEDGE}\n{_FORMATTING_RULES}\n{_BUSINESS_INSIGHT_CLOSING}\n"
        f"{greeting_note}{context_block}"
    )
    return f"{preamble}Question: {prompt}"


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
