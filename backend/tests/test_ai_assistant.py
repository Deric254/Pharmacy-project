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

import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_secret, encrypt_secret
from app.models.ai_provider_key import AIProviderKey, AIProviderName
from app.schemas.ai import AIAskRequest
from app.services.ai.adapters import ClaudeAdapter, OpenAIAdapter
from app.services.ai.base import AIProviderError, AIResponse
from app.services.ai_assistant_service import AIAssistantService


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

    async def test_list_keys_only_shows_own_keys(self, client, owner_user, employee_user):
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")

        await client.post(
            "/api/v1/ai/keys",
            json={"provider": "GEMINI", "api_key": "owner-key-1111"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        await client.post(
            "/api/v1/ai/keys",
            json={"provider": "DEEPSEEK", "api_key": "employee-key-2222"},
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        owner_keys = await client.get(
            "/api/v1/ai/keys", headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert len(owner_keys.json()) == 1
        assert owner_keys.json()[0]["provider"] == "GEMINI"

        employee_keys = await client.get(
            "/api/v1/ai/keys", headers={"Authorization": f"Bearer {employee_token}"}
        )
        assert len(employee_keys.json()) == 1
        assert employee_keys.json()[0]["provider"] == "DEEPSEEK"

    async def test_delete_key(self, client, owner_user):
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "NVIDIA", "api_key": "nvidia-key-3333"},
            headers=headers,
        )
        key_id = create_resp.json()["id"]

        delete_resp = await client.delete(f"/api/v1/ai/keys/{key_id}", headers=headers)
        assert delete_resp.status_code == 204

        list_resp = await client.get("/api/v1/ai/keys", headers=headers)
        assert list_resp.json() == []

    async def test_cannot_delete_another_users_key(self, client, owner_user, employee_user):
        owner_token = await _login(client, "lucy", "S3curePass!")
        employee_token = await _login(client, "joe", "pass1234")

        create_resp = await client.post(
            "/api/v1/ai/keys",
            json={"provider": "OPENAI", "api_key": "owner-only-key-4444"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        key_id = create_resp.json()["id"]

        r = await client.delete(
            f"/api/v1/ai/keys/{key_id}", headers={"Authorization": f"Bearer {employee_token}"}
        )
        assert r.status_code == 404  # not "not yours", never confirms it exists


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
                async def ask(self, prompt, context):
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
            async def ask(self, prompt, context):
                raise AIProviderError("simulated failure")

        class WorkingAdapter:
            async def ask(self, prompt, context):
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
            async def ask(self, prompt, context):
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
            async def ask(self, prompt, context):
                raise ValueError("some unrelated bug, not an AIProviderError")

        def buggy_factory(provider, api_key):
            return BuggyAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=buggy_factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="test"))
            assert response.provider_used is None
            assert "temporarily unavailable" in response.answer

    async def test_context_is_passed_through_to_the_adapter(self, client, owner_user):
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=owner_user.id,
                    provider=AIProviderName.OPENAI,
                    encrypted_key=encrypt_secret("key"),
                    key_hint="key1",
                    priority=1,
                )
            )
            await db.commit()

        captured_context: dict[str, object] = {}

        class ContextCapturingAdapter:
            async def ask(self, prompt, context):
                captured_context.update(context)
                return AIResponse(text="ok")

        def factory(provider, api_key):
            return ContextCapturingAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=factory)
            await service.ask(
                owner_user,
                AIAskRequest(
                    prompt="why is this expiring soon?",
                    context={"product_name": "Amoxicillin", "days_to_expiry": 12},
                ),
            )
        assert captured_context == {"product_name": "Amoxicillin", "days_to_expiry": 12}


class TestPermissions:
    async def test_ask_requires_ai_use_permission(self, client, seeded_roles):
        from app.core.security import hash_password
        from app.models.role import Role
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            role_result = await db.execute(select(Role).where(Role.name == "Employee"))
            role = role_result.scalar_one()
            role.permissions = [p for p in role.permissions if p.code != "ai.use"]
            await db.commit()
            u = User(
                full_name="No AI Access",
                username="noaiaccess",
                hashed_password=hash_password("pass1234"),
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

        response = await adapter.ask("What's low on stock?", {"screen": "dashboard"})

        assert response.text == "Hello from OpenAI"
        assert captured["url"] == "https://api.openai.com/v1/chat/completions"
        assert captured["auth_header"] == "Bearer test-key-123"
        assert b"What's low on stock?" in captured["body"]
        assert b"screen" in captured["body"]  # context was included

    async def test_claude_adapter_builds_correct_request(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["api_key_header"] = request.headers.get("x-api-key")
            captured["version_header"] = request.headers.get("anthropic-version")
            return httpx.Response(200, json={"content": [{"text": "Hello from Claude"}]})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = ClaudeAdapter(api_key="claude-key-456", client=http_client)

        response = await adapter.ask("help", {})

        assert response.text == "Hello from Claude"
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["api_key_header"] == "claude-key-456"
        assert captured["version_header"] == "2023-06-01"

    async def test_adapter_raises_ai_provider_error_on_http_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid api key"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(api_key="bad-key", client=http_client)

        raised = False
        try:
            await adapter.ask("test", {})
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
            await adapter.ask("test", {})
        except AIProviderError:
            raised = True
        assert raised, "Expected AIProviderError when response doesn't match the expected shape"
