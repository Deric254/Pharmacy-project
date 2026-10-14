"""
AI assistant tests. The properties that matter:
  1. Keys are actually encrypted at rest (verified by querying the raw
     DB column, not just trusting the code path) and never re-exposed.
  2. The fallback chain actually falls through on failure and never
     raises past the service - proven with fake adapters that
     deliberately fail, not just adapters that always succeed.
  3. The real adapters build the correct HTTP request (URL, headers,
     payload) for their provider, verified via httpx.MockTransport
     without any live network call to a paid third-party API.
"""

import asyncio
import json
import re
import time as time_module
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_secret, encrypt_secret
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.ai_provider_key import AIProviderKey, AIProviderName
from app.models.product import Product
from app.models.role import Role
from app.models.supplier import Supplier, SupplierTransaction
from app.models.user import User
from app.schemas.ai import AIAskRequest
from app.services.ai.adapters import (
    ClaudeAdapter,
    DeepSeekAdapter,
    GeminiAdapter,
    NvidiaAdapter,
    OpenAIAdapter,
)
from app.services.ai.base import AIProviderError, AIResponse
from app.services.ai.data_tools import (
    MAX_CALLS,
    MAX_RESULT_CHARS,
    MAX_ROWS,
    TOOLS,
    DataTool,
    ToolContext,
    ToolInputError,
    ToolResult,
    _bounded,
    _validate,
    available_tools,
    catalog_text,
    parse_plan,
    render_results,
    run_tool_calls,
)
from app.services.ai.prompts import FORMATTING_RULES, answer_prompt
from app.services.ai_assistant_service import AIAssistantService
from app.services.ai_conversation_service import ConversationNotFound
from app.services.business_config_service import BusinessConfigService


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


class TestKeyManagement:
    async def test_add_key_returns_masked_value_never_raw(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/ai/keys",
            json={
                "provider": "OPENAI",
                "api_key": "sk-supersecretlongkeyvalue1234",
                "priority": 1,
            },
            headers=headers,
        )
        assert r.status_code == 201
        assert r.json()["masked_key"] == "••••1234"
        assert "sk-supersecretlongkeyvalue1234" not in r.text

    async def test_a_key_shorter_than_16_characters_is_rejected(self, client, owner_user):
        """
        Real gap this closes: without a real minimum here, a key
        shorter than 4 characters had its ENTIRE value stored as
        key_hint and shown in the "masked" display (••••{key_hint}) --
        for a short string, that's not masked at all. Every genuine
        provider key (OpenAI, Anthropic, Google) is 20+ characters, so
        16 rejects an accidental paste/truncation error without risk
        of ever rejecting a real key.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": "short-key-123"},  # 13 chars
            headers=headers,
        )
        assert r.status_code == 422

    async def test_a_key_exactly_16_characters_is_accepted(self, client, owner_user):
        """The boundary itself, not just comfortably above/below it."""
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        sixteen_char_key = "exactly-sixteen1"
        assert len(sixteen_char_key) == 16

        r = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": sixteen_char_key},
            headers=headers,
        )
        assert r.status_code == 201
        # Never the full value, even at the boundary -- only the real
        # last-4-characters masking, proving this isn't accidentally
        # falling into the "whole key stored as hint" branch anymore.
        assert r.json()["masked_key"] == "••••een1"

    async def test_key_is_actually_encrypted_in_the_database(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        raw_key = "sk-a-very-real-looking-secret-key-98765"

        await client.post(
            "/api/v1/ai/keys",
            json={"provider": "CLAUDE", "api_key": raw_key, "priority": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AIProviderKey))
            key_row = result.scalars().first()
            assert key_row is not None
            assert key_row.encrypted_key != raw_key  # never stored in plaintext
            assert raw_key not in key_row.encrypted_key
            assert decrypt_secret(key_row.encrypted_key) == raw_key  # round-trips correctly

    async def test_keys_are_shared_across_managers_not_siloed_per_user(
        self, client, owner_user, administrator_user
    ):
        """
        The real fix: keys are a shared business resource. A key
        added by the Owner must be visible to an Administrator too --
        not siloed as if it belonged only to whoever happened to add
        it. This is exactly what closes the reported bug where a key
        added by one account was invisible ("no key configured") to
        every other account, including the one actually trying to use
        the assistant.
        """
        owner_token = await _login(client, "lucy", "S3curePass!")
        admin_token = await _login(client, "sam", "AdminPass1")

        await client.post(
            "/api/v1/ai/keys",
            json={"provider": "GEMINI", "api_key": "owner-added-key-1111"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        admin_keys = await client.get(
            "/api/v1/ai/keys", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert len(admin_keys.json()) == 1
        assert admin_keys.json()[0]["provider"] == "GEMINI"

    async def test_employee_cannot_add_or_list_or_delete_keys(self, client, employee_user):
        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}

        add = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": "employee-attempt-1111"},
            headers=headers,
        )
        assert add.status_code == 403

        listing = await client.get("/api/v1/ai/keys", headers=headers)
        assert listing.status_code == 403

        delete = await client.delete("/api/v1/ai/keys/1", headers=headers)
        assert delete.status_code == 403

    async def test_administrator_can_delete_a_key_the_owner_added(
        self, client, owner_user, administrator_user
    ):
        owner_token = await _login(client, "lucy", "S3curePass!")
        admin_token = await _login(client, "sam", "AdminPass1")

        create_resp = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": "owner-added-key-4444"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        key_id = create_resp.json()["id"]

        r = await client.delete(
            f"/api/v1/ai/keys/{key_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r.status_code == 204

    async def test_employee_can_actually_use_a_key_the_owner_added(
        self, client, owner_user, employee_user
    ):
        """
        The exact real-world scenario from the bug report: the Owner
        adds a key, and an Employee asking a question must be able to
        actually use it -- not see "no key configured" despite one
        genuinely existing in the system.
        """
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")

        await client.post(
            "/api/v1/ai/keys",
            json={"provider": "GEMINI", "api_key": "shared-team-key-9999"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        r = await client.post(
            "/api/v1/ai/ask",
            json={"prompt": "test"},
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        # It will fail to actually reach a real provider with a fake
        # key (expected, no network calls in tests) -- what matters is
        # it did NOT hit the "no key configured" guidance message.
        assert "No AI provider is configured" not in r.json().get("answer", "")

    async def test_delete_key(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "NVIDIA", "api_key": "nvidia-key-33333"},
            headers=headers,
        )
        key_id = create_resp.json()["id"]

        delete_resp = await client.delete(f"/api/v1/ai/keys/{key_id}", headers=headers)
        assert delete_resp.status_code == 204

        list_resp = await client.get("/api/v1/ai/keys", headers=headers)
        assert list_resp.json() == []


class TestFallbackChain:
    """
    These tests call AIAssistantService directly with a fake adapter
    factory -- deliberately not going through real provider HTTP calls,
    since this is exactly the logic (does it actually fall through on
    failure?) that matters, independent of any specific provider's API.
    """

    async def test_no_keys_configured_returns_graceful_message(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db)
            response = await service.ask(owner_user, AIAskRequest(prompt="help me"))
            assert response.provider_used is None
            assert response.fallback_used is False
            assert "No AI provider" in response.answer

    async def test_single_working_provider_succeeds_without_fallback(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.OPENAI,
                    encrypted_key=encrypt_secret("fake-key"),
                    key_hint="fake",
                    priority=1,
                )
            )
            await db.commit()

        def working_factory(provider, api_key):
            class FakeAdapter:
                async def complete(self, prompt):
                    return AIResponse(text=f"Answer from {provider.value}")

            return FakeAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=working_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="what's low on stock?"))
            assert response.provider_used == AIProviderName.OPENAI
            assert response.fallback_used is False
            assert "Answer from OPENAI" in response.answer

    async def test_first_provider_fails_second_succeeds(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            db.add_all(
                [
                    AIProviderKey(
                        user_id=owner_user.id,
                        provider=AIProviderName.OPENAI,
                        encrypted_key=encrypt_secret("broken-key"),
                        key_hint="roke",
                        priority=1,
                    ),
                    AIProviderKey(
                        user_id=owner_user.id,
                        provider=AIProviderName.CLAUDE,
                        encrypted_key=encrypt_secret("working-key"),
                        key_hint="king",
                        priority=2,
                    ),
                ]
            )
            await db.commit()

        class FailingAdapter:
            async def complete(self, prompt):
                raise AIProviderError("simulated failure")

        class WorkingAdapter:
            async def complete(self, prompt):
                return AIResponse(text="Claude saved the day")

        def fallback_factory(provider, api_key):
            return FailingAdapter() if provider == AIProviderName.OPENAI else WorkingAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=fallback_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="help"))
            assert response.provider_used == AIProviderName.CLAUDE
            assert response.fallback_used is True  # proves it actually fell through
            assert "Claude saved the day" in response.answer

    async def test_all_providers_fail_returns_graceful_message_not_an_exception(
        self, client, owner_user
    ):
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.GEMINI,
                    encrypted_key=encrypt_secret("dead-key"),
                    key_hint="key1",
                    priority=1,
                )
            )
            await db.commit()

        class AlwaysFailsAdapter:
            async def complete(self, prompt):
                raise AIProviderError("provider is down")

        def always_fails_factory(provider, api_key):
            return AlwaysFailsAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=always_fails_factory)
            # This must NOT raise -- the panel can never show a 500.
            response = await service.ask(owner_user, AIAskRequest(prompt="anything"))
            assert response.provider_used is None
            assert response.fallback_used is True
            assert "temporarily unavailable" in response.answer

    async def test_unexpected_adapter_exception_also_falls_through_gracefully(
        self, client, owner_user
    ):
        """
        Guards against a regression where only AIProviderError is
        caught -- a genuinely unexpected bug in one adapter (a typo, a
        library exception type change) must still not crash the panel.
        """
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.NVIDIA,
                    encrypted_key=encrypt_secret("whatever"),
                    key_hint="ever",
                    priority=1,
                )
            )
            await db.commit()

        class BuggyAdapter:
            async def complete(self, prompt):
                raise ValueError("some unrelated bug, not an AIProviderError")

        def buggy_factory(provider, api_key):
            return BuggyAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=buggy_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="test"))
            assert response.provider_used is None
            assert "temporarily unavailable" in response.answer


class TestPermissions:
    async def test_ask_requires_ai_use_permission(self, client, seeded_roles):
        from app.core.security import hash_password
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "Employee"))
            role = role_result.scalar_one()
            role.permissions = [p for p in role.permissions if p.code != "ai.use"]
            await db.commit()
            u = User(
                full_name="No AI Access",
                username="noaiaccess",
                hashed_password=await hash_password("pass1234"),
                role_id=role.id,
            )
            db.add(u)
            await db.commit()

        token = await _login(client, "noaiaccess", "pass1234")
        r = await client.post(
            "/api/v1/ai/ask",
            json={"prompt": "help"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestRealAdapterRequestShape:
    """
    These verify the actual HTTP request each real adapter builds --
    URL, headers, payload -- using httpx.MockTransport, so there's no
    live call to a paid third-party API, but the request construction
    itself is genuinely exercised end-to-end through the adapter code.
    """

    async def test_openai_adapter_builds_correct_request(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth_header"] = request.headers.get("authorization")
            captured["body"] = request.read()
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Hello from OpenAI"}}]}
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(api_key="test-key-123", client=http_client)

        response = await adapter.complete("What's low on stock?")

        assert response.text == "Hello from OpenAI"
        assert captured["url"] == "https://api.openai.com/v1/chat/completions"
        assert captured["auth_header"] == "Bearer test-key-123"
        assert b"What's low on stock?" in captured["body"]

    async def test_claude_adapter_builds_correct_request(self):
        """
        Real gap this closes: unlike the Gemini/DeepSeek/NVIDIA tests
        below, this test never asserted which model ID was actually in
        the request body -- so when claude-3-5-sonnet-20241022 (the
        model ID that used to be here) was retired by Anthropic, this
        test kept passing right through it, exactly the same silent-
        failure mode already documented and closed for the other three
        providers. Asserting the model string in the body is what
        makes a future retirement fail here instead of live.
        """
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["api_key_header"] = request.headers.get("x-api-key")
            captured["version_header"] = request.headers.get("anthropic-version")
            captured["body"] = request.read()
            return httpx.Response(200, json={"content": [{"text": "Hello from Claude"}]})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = ClaudeAdapter(api_key="claude-key-456", client=http_client)

        response = await adapter.complete("help")

        assert response.text == "Hello from Claude"
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["api_key_header"] == "claude-key-456"
        assert captured["version_header"] == "2023-06-01"
        assert b"claude-sonnet-5" in captured["body"]

    async def test_gemini_adapter_builds_correct_request(self):
        """
        Real gap this closes: Gemini, DeepSeek, and Nvidia had zero
        request-shape coverage despite this module's own docstring
        saying that's how adapters are meant to be tested -- which is
        exactly how a retired model ID (gemini-1.5-flash, shut down by
        Google) went unnoticed until it started failing live with a
        404. This locks in the current model ID so a future retirement
        fails a test instead of a user's live "Ask AI" click.
        """
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["api_key_header"] = request.headers.get("x-goog-api-key")
            captured["body"] = request.read()
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]},
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(api_key="gemini-key-789", client=http_client)

        response = await adapter.complete("What's expiring soon?")

        assert response.text == "Hello from Gemini"
        assert captured["url"] == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent"
        )
        # Header, not a ?key=... query param -- a key in the URL ends up
        # in proxy logs, error message strings, and (previously) this
        # exact test's own assertion history. See adapters.py's comment
        # on this same header for why that's a real leakage path, not
        # just a style preference.
        assert captured["api_key_header"] == "gemini-key-789"
        assert "key=" not in captured["url"]
        assert b"What's expiring soon?" in captured["body"]

    async def test_deepseek_adapter_builds_correct_request(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth_header"] = request.headers.get("authorization")
            captured["body"] = request.read()
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Hello from DeepSeek"}}]}
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = DeepSeekAdapter(api_key="deepseek-key-321", client=http_client)

        response = await adapter.complete("Any low stock?")

        assert response.text == "Hello from DeepSeek"
        assert captured["url"] == "https://api.deepseek.com/chat/completions"
        assert captured["auth_header"] == "Bearer deepseek-key-321"
        assert b"deepseek-chat" in captured["body"]
        assert b"Any low stock?" in captured["body"]

    async def test_nvidia_adapter_builds_correct_request(self):
        """
        Real gap this closes: the previous model ID
        (meta/llama-3.1-8b-instruct) was pulled from NVIDIA's hosted
        inference catalog and started returning 410 Gone -- confirmed
        against NVIDIA's own current documentation, not guessed. This
        locks in the replacement so the same silent failure mode is
        caught here instead of live.
        """
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth_header"] = request.headers.get("authorization")
            captured["body"] = request.read()
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Hello from Nvidia"}}]}
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = NvidiaAdapter(api_key="nvidia-key-654", client=http_client)

        response = await adapter.complete("Reorder suggestions?")

        assert response.text == "Hello from Nvidia"
        assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert captured["auth_header"] == "Bearer nvidia-key-654"
        assert b"nvidia/nemotron-3-super-120b-a12b" in captured["body"]

    async def test_adapter_raises_ai_provider_error_on_http_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid api key"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(api_key="bad-key", client=http_client)

        raised = False
        try:
            await adapter.complete("test")
        except AIProviderError:
            raised = True
        assert raised, "Expected AIProviderError on a 401 response"

    async def test_adapter_raises_ai_provider_error_on_malformed_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(api_key="key", client=http_client)

        raised = False
        try:
            await adapter.complete("test")
        except AIProviderError:
            raised = True
        assert raised, "Expected AIProviderError when response doesn't match the expected shape"


class TestNoKeyGuidance:
    """
    The real gap this closes: before, "no key configured" was a dead
    end for everyone -- no indication of what to actually do next.
    Owner/Administrator (can self-serve) get told to add their own
    key; Employee gets told to escalate, matching the same hierarchy
    signal (users.manage) already used elsewhere in this codebase.
    """

    async def test_owner_told_to_add_their_own_key(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db)
            response = await service.ask(owner_user, AIAskRequest(prompt="how's business?"))
        assert "Add an API key" in response.answer
        assert "escalate" not in response.answer.lower()

    async def test_employee_told_to_escalate_not_self_serve(self, client, employee_user):
        from sqlalchemy import select as _select

        from app.core.database import AsyncSessionLocal as _Session
        from app.models.user import User as _User

        async with _Session() as db:
            result = await db.execute(_select(_User).where(_User.username == "joe"))
            joe = result.scalar_one()
            service = AIAssistantService(db)
            response = await service.ask(joe, AIAskRequest(prompt="how's business?"))
        assert "owner or administrator" in response.answer.lower()
        assert "Add an API key" not in response.answer


class TestConversationHistory:
    """
    The real gap this closes: every /ai/ask call used to be entirely
    stateless -- no history saved anywhere, no way to start a fresh
    thread or delete an old one, and the frontend's own on-screen log
    was lost on every refresh. These tests verify the three properties
    that actually matter for a real, persisted, per-user chat history:
    a thread survives across separate ask() calls, a thread is
    strictly private to the user who started it (not even readable by
    another authenticated user guessing ids), and deleting a thread
    really removes its messages, not just the parent row.
    """

    @staticmethod
    def _working_factory(provider, api_key):
        class FakeAdapter:
            async def complete(self, prompt):
                return AIResponse(text=f"answer to: {prompt}")

        return FakeAdapter()

    async def _give_owner_a_key(self, owner_user) -> None:
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.OPENAI,
                    encrypted_key=encrypt_secret("fake-key"),
                    key_hint="fake",
                    priority=1,
                )
            )
            await db.commit()

    async def test_ask_without_conversation_id_creates_a_new_conversation(self, owner_user):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="how's business today?"))

        assert response.conversation_id is not None
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AIConversation).where(AIConversation.id == response.conversation_id)
            )
            conversation = result.scalar_one()
            assert conversation.user_id == owner_user.id
            assert conversation.title == "how's business today?"

            messages_result = await db.execute(
                select(AIConversationMessage).where(
                    AIConversationMessage.conversation_id == conversation.id
                )
            )
            messages = list(messages_result.scalars().all())
            assert len(messages) == 1
            assert messages[0].prompt == "how's business today?"
            assert "answer to:" in messages[0].answer

    async def test_second_ask_with_same_conversation_id_appends_not_replaces(self, owner_user):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            first = await service.ask(owner_user, AIAskRequest(prompt="first question"))

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            second = await service.ask(
                owner_user,
                AIAskRequest(prompt="second question", conversation_id=first.conversation_id),
            )

        assert second.conversation_id == first.conversation_id
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AIConversationMessage)
                .where(AIConversationMessage.conversation_id == first.conversation_id)
                .order_by(AIConversationMessage.created_at)
            )
            messages = list(result.scalars().all())
            assert len(messages) == 2
            assert messages[0].prompt == "first question"
            assert messages[1].prompt == "second question"

            conv_result = await db.execute(
                select(AIConversation).where(AIConversation.id == first.conversation_id)
            )
            conversation = conv_result.scalar_one()
            # Title stays as it was set on turn one -- a label for
            # finding the thread again, not a live summary.
            assert conversation.title == "first question"

    async def test_ask_with_someone_elses_conversation_id_raises_not_found(
        self, owner_user, employee_user
    ):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            owners_conversation = await service.ask(
                owner_user, AIAskRequest(prompt="owner's private question")
            )

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.username == "joe"))
            joe = result.scalar_one()
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            try:
                await service.ask(
                    joe,
                    AIAskRequest(
                        prompt="trying to read someone else's thread",
                        conversation_id=owners_conversation.conversation_id,
                    ),
                )
                raise AssertionError("expected ConversationNotFound")
            except ConversationNotFound:
                pass

    async def test_list_conversations_only_shows_own_via_http(
        self, client, owner_user, employee_user
    ):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            await service.ask(owner_user, AIAskRequest(prompt="owner's question"))

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.username == "joe"))
            joe = result.scalar_one()
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            await service.ask(joe, AIAskRequest(prompt="employee's question"))

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get("/api/v1/ai/conversations", headers=headers)
        assert r.status_code == 200, r.text
        titles = [c["title"] for c in r.json()]
        assert titles == ["owner's question"]

    async def test_get_someone_elses_conversation_is_404_via_http(
        self, client, owner_user, employee_user
    ):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            owners_conversation = await service.ask(
                owner_user, AIAskRequest(prompt="owner's private question")
            )

        token = await _login(client, "joe", "pass1234")
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get(
            f"/api/v1/ai/conversations/{owners_conversation.conversation_id}", headers=headers
        )
        assert r.status_code == 404

    async def test_delete_conversation_removes_its_messages_too(self, client, owner_user):
        await self._give_owner_a_key(owner_user)
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            conversation = await service.ask(owner_user, AIAskRequest(prompt="to be deleted"))

        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.delete(
            f"/api/v1/ai/conversations/{conversation.conversation_id}", headers=headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            conv_result = await db.execute(
                select(AIConversation).where(AIConversation.id == conversation.conversation_id)
            )
            assert conv_result.scalar_one_or_none() is None

            msg_result = await db.execute(
                select(AIConversationMessage).where(
                    AIConversationMessage.conversation_id == conversation.conversation_id
                )
            )
            assert list(msg_result.scalars().all()) == []

        # Deleting again (already gone) is a 404, not a 500 -- deletes
        # in this codebase are idempotent-safe, never crash on repeat.
        r2 = await client.delete(
            f"/api/v1/ai/conversations/{conversation.conversation_id}", headers=headers
        )
        assert r2.status_code == 404

    async def test_long_prompt_produces_truncated_title_with_ellipsis(self, owner_user):
        await self._give_owner_a_key(owner_user)
        long_prompt = "what is the current stock level of " + "paracetamol " * 15
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=self._working_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt=long_prompt))

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AIConversation).where(AIConversation.id == response.conversation_id)
            )
            conversation = result.scalar_one()
            assert len(conversation.title) <= 61  # 60 chars + the ellipsis char
            assert conversation.title.endswith("…")


class TestEventLoopNotBlockedDuringSlowResponse:
    """
    The real property that matters for a shared single-process
    backend: a slow LLM response (the AI "thinking" for several real
    seconds) must never stall anything else the backend is doing at
    the same time -- another cashier's sale, a dashboard load, another
    person's own AI question. This is only true because every
    provider adapter awaits a genuine httpx.AsyncClient call (see
    adapters.py) rather than blocking synchronously; this test proves
    the effect directly rather than trusting that from reading the
    code alone.
    """

    async def test_slow_ai_response_does_not_delay_unrelated_concurrent_work(self, owner_user):
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.OPENAI,
                    encrypted_key=encrypt_secret("fake-key"),
                    key_hint="fake",
                    priority=1,
                )
            )
            await db.commit()

        class SlowAdapter:
            async def complete(self, prompt):
                await asyncio.sleep(1.0)  # a real "thinking" LLM response
                return AIResponse(text="slow answer")

        def factory(provider, api_key):
            return SlowAdapter()

        async def slow_ai_call() -> float:
            async with AsyncSessionLocal() as db:
                service = AIAssistantService(db, adapter_factory=factory)
                t0 = time_module.perf_counter()
                await service.ask(owner_user, AIAskRequest(prompt="slow question"))
                return time_module.perf_counter() - t0

        async def unrelated_work() -> int:
            # Simulates something else the backend is doing at the
            # same time -- ticks every 50ms. If the AI call were
            # blocking, these ticks would bunch up and arrive late
            # instead of on schedule.
            ticks = 0
            for _ in range(15):
                await asyncio.sleep(0.05)
                ticks += 1
            return ticks

        t_start = time_module.perf_counter()
        ai_duration, ticks = await asyncio.gather(slow_ai_call(), unrelated_work())
        total = time_module.perf_counter() - t_start

        assert ticks == 15  # unrelated work fully completed, not starved
        # If the AI call blocked the event loop, total would be close
        # to ai_duration + (15 * 0.05) =~ 1.75s (serialized). Running
        # concurrently, it should be close to max(ai_duration, 0.75s)
        # =~ 1.0s -- well under the serialized figure either way.
        assert total < ai_duration + 0.5


_PLANNER_START = "You choose which read-only data lookups"


class _ScriptedProvider:
    """A fake model: answers the planner prompt with `plan` and anything else
    with `answer`, and records every prompt it was sent."""

    def __init__(self, plan: str, answer: str = "Here is your answer.") -> None:
        self.plan = plan
        self.answer = answer
        self.prompts: list[str] = []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        return AIResponse(text=self.plan if prompt.startswith(_PLANNER_START) else self.answer)

    def factory(self):
        return lambda provider, api_key: self

    @property
    def planner_prompt(self) -> str:
        return next(p for p in self.prompts if p.startswith(_PLANNER_START))

    @property
    def answer_prompt(self) -> str:
        return next(p for p in self.prompts if not p.startswith(_PLANNER_START))


async def _add_key(user, provider=AIProviderName.OPENAI, priority=1) -> None:
    async with AsyncSessionLocal() as db:
        db.add(
            AIProviderKey(
                user_id=user.id,
                provider=provider,
                encrypted_key=encrypt_secret("fake-key-123456"),
                key_hint="3456",
                priority=priority,
            )
        )
        await db.commit()


async def _ask(user, provider: _ScriptedProvider, prompt="question", **request):
    async with AsyncSessionLocal() as db:
        service = AIAssistantService(db, adapter_factory=provider.factory())
        return await service.ask(user, AIAskRequest(prompt=prompt, **request))


async def _supplier_owed(name: str, owed: float) -> None:
    async with AsyncSessionLocal() as db:
        supplier = Supplier(name=name)
        db.add(supplier)
        await db.flush()
        db.add(SupplierTransaction(supplier_id=supplier.id, amount=owed, reference="po:1"))
        await db.commit()


def _plan(*calls: tuple[str, dict]) -> str:
    return json.dumps({"calls": [{"tool": t, "args": a} for t, a in calls]})


class TestAssistantAnswersFromRealData:
    async def test_the_looked_up_data_reaches_the_answer_prompt(self, client, owner_user):
        await _add_key(owner_user)
        await _supplier_owed("Acme Pharma", 1250.0)
        model = _ScriptedProvider(_plan(("suppliers", {})), answer="Acme is owed 1,250.")

        response = await _ask(owner_user, model, "who do we owe money?")

        assert response.answer == "Acme is owed 1,250."
        assert "Acme Pharma" in model.answer_prompt
        assert "1250" in model.answer_prompt
        assert model.planner_prompt.count("suppliers(") == 1  # offered to the planner

    async def test_several_lookups_in_one_plan_all_run(self, client, owner_user):
        await _add_key(owner_user)
        await _supplier_owed("Multi Supplier", 300.0)
        model = _ScriptedProvider(
            _plan(
                ("suppliers", {}),
                ("supplier_summary", {"start_date": "2026-01-01", "end_date": "2026-12-31"}),
            )
        )

        await _ask(owner_user, model)

        assert "Multi Supplier" in model.answer_prompt
        assert "supplier_summary" in model.answer_prompt
        assert "total_purchased" in model.answer_prompt

    async def test_a_plan_wrapped_in_prose_and_code_fences_still_works(self, client, owner_user):
        await _add_key(owner_user)
        await _supplier_owed("Fenced Supplier", 10.0)
        wrapped = f"Sure, here you go:\n```json\n{_plan(('suppliers', {}))}\n```"
        model = _ScriptedProvider(wrapped)

        await _ask(owner_user, model)

        assert "Fenced Supplier" in model.answer_prompt

    async def test_an_empty_plan_answers_without_any_data_block(self, client, owner_user):
        await _add_key(owner_user)
        model = _ScriptedProvider('{"calls": []}', answer="Good morning!")

        response = await _ask(owner_user, model, "good morning")

        assert response.answer == "Good morning!"
        assert "Data looked up" not in model.answer_prompt

    async def test_an_unreadable_plan_falls_back_to_todays_overview(self, client, owner_user):
        await _add_key(owner_user)
        model = _ScriptedProvider("I would rather just chat.")

        await _ask(owner_user, model, "how are we doing?")

        assert "Data looked up" in model.answer_prompt
        assert "business_overview" in model.answer_prompt

    async def test_a_bad_lookup_is_reported_to_the_model_not_hidden(self, client, owner_user):
        await _add_key(owner_user)
        model = _ScriptedProvider(
            _plan(
                ("drop_all_tables", {}),
                ("top_products", {"start_date": "not-a-date", "end_date": "2026-01-02"}),
                ("low_stock", {"colour": "red"}),
            )
        )

        response = await _ask(owner_user, model)

        assert response.answer  # the person still gets an answer
        assert "unknown or unavailable lookup" in model.answer_prompt
        assert "must be a date written YYYY-MM-DD" in model.answer_prompt
        assert "unknown argument(s): colour" in model.answer_prompt

    async def test_follow_up_questions_see_the_conversation_so_far(self, client, owner_user):
        await _add_key(owner_user)
        first = _ScriptedProvider('{"calls": []}', answer="Revenue was 500.")
        opening = await _ask(owner_user, first, "what was revenue in June?")

        second = _ScriptedProvider('{"calls": []}')
        await _ask(owner_user, second, "and in July?", conversation_id=opening.conversation_id)

        assert "what was revenue in June?" in second.planner_prompt
        assert "Revenue was 500." in second.answer_prompt

    async def test_the_dashboard_range_is_a_hint_not_a_number(self, client, owner_user):
        await _add_key(owner_user)
        model = _ScriptedProvider('{"calls": []}')

        await _ask(
            owner_user,
            model,
            context={"viewing_start_date": "2026-03-01", "viewing_end_date": "2026-03-31"},
        )
        assert "2026-03-01 to 2026-03-31" in model.planner_prompt

        ignored = _ScriptedProvider('{"calls": []}')
        await _ask(owner_user, ignored, context={"viewing_start_date": "junk"})
        assert "dashboard open on" not in ignored.planner_prompt

    async def test_the_prompt_states_the_businesss_currency(self, client, owner_user):
        await _add_key(owner_user)
        model = _ScriptedProvider('{"calls": []}')

        await _ask(owner_user, model)

        async with AsyncSessionLocal() as db:
            currency = (await BusinessConfigService(db).get()).currency
        assert f"All money is in {currency}" in model.answer_prompt


class TestAssistantRespectsPermissions:
    async def test_an_employee_is_never_offered_data_their_pages_hide(self, client, employee_user):
        await _add_key(employee_user)
        await _supplier_owed("Hidden Supplier", 999.0)
        model = _ScriptedProvider(_plan(("suppliers", {}), ("business_overview", {})))

        await _ask(employee_user, model)

        offered = model.planner_prompt
        assert "low_stock(" in offered and "recent_sales(" in offered  # inventory + sales
        for hidden in ("suppliers(", "business_overview(", "profit_summary(", "top_customers("):
            assert hidden not in offered
        # Asking for them anyway is refused, and nothing leaks.
        assert "Hidden Supplier" not in model.answer_prompt
        assert model.answer_prompt.count("unknown or unavailable lookup") == 2

    async def test_profit_lookups_need_the_profit_permission(
        self, client, owner_user, administrator_user
    ):
        await _add_key(owner_user)
        owner_model = _ScriptedProvider('{"calls": []}')
        await _ask(owner_user, owner_model)
        assert "profit_summary(" in owner_model.planner_prompt

        admin_model = _ScriptedProvider(
            _plan(("profit_summary", {"start_date": "2026-01-01", "end_date": "2026-01-31"}))
        )
        await _ask(administrator_user, admin_model)
        assert "profit_summary(" not in admin_model.planner_prompt
        assert "unknown or unavailable lookup" in admin_model.answer_prompt

    async def test_product_cost_and_margin_are_hidden_without_profit_permission(
        self, client, owner_user
    ):
        headers = {"Authorization": "Bearer " + await _login(client, "lucy", "S3curePass!")}
        async with AsyncSessionLocal() as db:
            supplier = Supplier(name="S")
            product = Product(name="Costed Product")
            db.add_all([supplier, product])
            await db.commit()
            supplier_id, product_id = supplier.id, product.id
        bought = await client.post(
            "/api/v1/purchase-orders/quick-purchase",
            json={
                "supplier_id": supplier_id,
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": 5,
                        "batch_number": "B1",
                        "expiry_date": "2097-01-01",
                        "unit_cost": 8.0,
                        "selling_price": 15.0,
                    }
                ],
            },
            headers=headers,
        )
        assert bought.status_code == 201, bought.text

        async with AsyncSessionLocal() as db:
            call = [{"tool": "find_products", "args": {"search": "Costed"}}]
            tools = available_tools({"inventory.view"})
            hidden = await run_tool_calls(ToolContext(db, include_profit=False), tools, call)
            shown = await run_tool_calls(ToolContext(db, include_profit=True), tools, call)

        assert hidden[0].data[0]["current_selling_price"] == 15.0
        assert "current_cost" not in hidden[0].data[0]
        assert "margin_percent" not in hidden[0].data[0]
        assert shown[0].data[0]["current_cost"] == 8.0


class TestAssistantProviderFailures:
    async def test_the_plan_step_falls_back_to_the_next_provider(self, client, owner_user):
        await _add_key(owner_user, AIProviderName.OPENAI, priority=1)
        await _add_key(owner_user, AIProviderName.CLAUDE, priority=2)
        good = _ScriptedProvider('{"calls": []}', answer="From Claude")

        class Broken:
            async def complete(self, prompt):
                raise AIProviderError("down")

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(
                db,
                adapter_factory=lambda provider, key: Broken()
                if provider == AIProviderName.OPENAI
                else good,
            )
            response = await service.ask(owner_user, AIAskRequest(prompt="hello"))

        assert response.answer == "From Claude"
        assert response.provider_used == AIProviderName.CLAUDE
        assert response.fallback_used is True

    async def test_when_every_provider_fails_the_plan_the_answer_step_is_not_attempted(
        self, client, owner_user
    ):
        await _add_key(owner_user)
        calls = 0

        class Broken:
            async def complete(self, prompt):
                nonlocal calls
                calls += 1
                raise AIProviderError("down")

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=lambda provider, key: Broken())
            response = await service.ask(owner_user, AIAskRequest(prompt="hello"))

        assert "temporarily unavailable" in response.answer
        assert response.provider_used is None
        assert calls == 1  # one provider, one planning attempt, no wasted second round


class TestDataTools:
    @staticmethod
    def _tool(name: str):
        return next(t for t in TOOLS if t.name == name)

    def test_each_permission_unlocks_only_its_tools(self):
        assert {t.name for t in available_tools({"inventory.view"})} == {
            "low_stock",
            "expiring_stock",
            "stock_valuation",
            "find_products",
        }
        assert available_tools(set()) == []
        everything = {p for t in TOOLS for p in t.permissions}
        assert {t.name for t in available_tools(everything)} == {t.name for t in TOOLS}

    def test_the_catalog_describes_every_argument(self):
        text = catalog_text([self._tool("top_products"), self._tool("sales_over_time")])

        assert "top_products(start_date: date YYYY-MM-DD, required" in text
        assert "limit: integer 1-30, default 10" in text
        assert "group_by: one of day|month, default day" in text

    def test_arguments_are_validated_strictly(self):
        top = self._tool("top_products")
        good = {"start_date": "2026-01-01", "end_date": "2026-01-31"}

        assert _validate(top, good) == {
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 1, 31),
            "limit": 10,
        }
        assert _validate(top, {**good, "limit": 9999})["limit"] == 30  # clamped, not trusted
        for bad, message in (
            ({"end_date": "2026-01-31"}, "missing required argument: start_date"),
            ({**good, "start_date": "2026-02-30"}, "YYYY-MM-DD"),
            ({**good, "limit": "many"}, "whole number"),
            ({**good, "start_date": "2026-03-01"}, "must not be after"),
            ({**good, "extra": 1}, "unknown argument(s): extra"),
            ("not an object", "must be an object"),
        ):
            with pytest.raises(ToolInputError, match=re.escape(message)):
                _validate(top, bad)
        with pytest.raises(ToolInputError, match="one of day, month"):
            _validate(self._tool("sales_over_time"), {**good, "group_by": "year"})
        with pytest.raises(ToolInputError, match="1 to 80"):
            _validate(self._tool("find_products"), {"search": "x" * 81})

    def test_plans_are_parsed_leniently_but_never_trusted(self):
        assert parse_plan('{"calls": []}') == []
        assert parse_plan('noise {"calls": [{"tool": "low_stock", "args": {}}]} noise') == [
            {"tool": "low_stock", "args": {}}
        ]
        assert parse_plan("no json here") is None
        assert parse_plan("{broken json") is None
        assert parse_plan('{"something": "else"}') is None
        assert len(parse_plan(json.dumps({"calls": [{"tool": "low_stock"}] * 20}))) == MAX_CALLS

    def test_long_results_are_cut_and_say_so(self):
        rows = [{"n": i} for i in range(MAX_ROWS + 15)]

        assert _bounded(rows) == {"rows": rows[:MAX_ROWS], "rows_omitted": 15}
        assert _bounded({"entries": rows, "total": 75}) == {
            "entries": rows[:MAX_ROWS],
            "entries_omitted": 15,
            "total": 75,
        }
        assert _bounded({"entries": rows[:3]}) == {"entries": rows[:3]}
        huge = render_results([ToolResult("t", {}, "x" * (MAX_RESULT_CHARS * 2), None)])
        assert len(huge) < MAX_RESULT_CHARS + 100 and "cut: too long" in huge

    async def test_a_failing_lookup_is_reported_and_the_rest_still_run(self, client, owner_user):
        async def explode(ctx, args):
            raise RuntimeError("database on fire")

        async def fine(ctx, args):
            return {"ok": True}

        tools = [
            DataTool("explodes", "x", ("reports.view",), {}, explode),
            DataTool("fine", "x", ("reports.view",), {}, fine),
        ]
        async with AsyncSessionLocal() as db:
            results = await run_tool_calls(
                ToolContext(db, include_profit=False),
                tools,
                [{"tool": "explodes"}, {"tool": "fine"}],
            )

        assert [(r.tool, r.error) for r in results] == [
            ("explodes", "the lookup failed"),
            ("fine", None),
        ]
        assert results[1].data == {"ok": True}

    async def test_every_tool_runs_against_an_empty_database_without_error(
        self, client, owner_user
    ):
        """Each wrapper calls a real service with real arguments: a typo in a
        keyword or a changed service signature surfaces here, not live."""
        sample = {"start_date": "2026-01-01", "end_date": "2026-01-31", "search": "a"}
        results: list[ToolResult] = []
        async with AsyncSessionLocal() as db:
            ctx = ToolContext(db, include_profit=True)
            for tool in TOOLS:
                args = {name: sample[name] for name, p in tool.params.items() if p.default is None}
                results += await run_tool_calls(
                    ctx, list(TOOLS), [{"tool": tool.name, "args": args}]
                )

        assert [(r.tool, r.error) for r in results if r.error] == []
        assert [r.tool for r in results] == [t.name for t in TOOLS]


class TestPrompts:
    def test_the_chat_formatting_promise_matches_what_the_screen_can_show(self):
        assert "simple tables" in FORMATTING_RULES
        assert "Never use headers" in FORMATTING_RULES
        assert "no closing summary" in FORMATTING_RULES

    def test_the_answer_prompt_forbids_inventing_figures_and_writing_data(self):
        prompt = answer_prompt("q", date(2026, 1, 1), "KES", "Deric", '{"x":1}', [])

        assert "Never invent or estimate a figure" in prompt
        assert "can only read and analyse data" in prompt
        assert "KES 1,553.68" in prompt

    def test_without_data_there_are_no_data_rules(self):
        prompt = answer_prompt("hi", date(2026, 1, 1), "", "Deric", None, [])

        assert "Rules for using the data" not in prompt
        assert "All money is in" not in prompt
