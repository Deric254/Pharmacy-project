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

from datetime import date, datetime, timedelta

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
            json={"provider": "NVIDIA", "api_key": "nvidia-key-3333"},
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
        assert captured_context["product_name"] == "Amoxicillin"
        assert captured_context["days_to_expiry"] == 12


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


class TestBusinessContext:
    """
    The actual gap this closes: the frontend never sent any context at
    all, so every AI answer was pure guesswork with zero grounding in
    the real pharmacy's data. Business context is now built server-
    side, fresh, on every question -- never trusting whatever a client
    might claim, and matching the dashboard's own profit-visibility
    rule exactly.
    """

    async def test_business_context_is_automatically_included_with_no_client_context(
        self, client, owner_user
    ):
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
            await service.ask(owner_user, AIAskRequest(prompt="how is my pharmacy doing today?"))

        # Real business numbers, present even though the client sent
        # nothing at all -- this is the actual feature.
        assert "today_revenue" in captured_context
        assert "today_transaction_count" in captured_context
        assert "low_stock_product_count" in captured_context

    async def test_client_sent_context_cannot_override_the_real_business_numbers(
        self, client, owner_user
    ):
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
                    prompt="what's my revenue?",
                    # A spoofing attempt -- must not win over the real number.
                    context={"today_revenue": 999999999.0},
                ),
            )

        assert captured_context["today_revenue"] != 999999999.0

    async def test_profit_excluded_from_context_without_view_profit_permission(
        self, client, owner_user, administrator_user
    ):
        async with AsyncSessionLocal() as db:
            db.add(
                AIProviderKey(
                    user_id=administrator_user.id,
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
            await service.ask(administrator_user, AIAskRequest(prompt="how's business?"))

        # Same rule the dashboard enforces -- not leaked into the AI
        # prompt just because the assistant has a wider data pipe than
        # the dashboard's own endpoint.
        assert "today_profit" not in captured_context
        assert "today_profit_margin_percent" not in captured_context

    async def test_profit_included_in_context_for_owner(self, client, owner_user):
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
            await service.ask(owner_user, AIAskRequest(prompt="how's business?"))

        assert "today_profit" in captured_context

    async def test_business_context_failure_never_breaks_the_assistant(
        self, client, owner_user, monkeypatch
    ):
        """
        Business context is enrichment, not load-bearing -- if
        computing it fails for any reason, the assistant must still
        answer using whatever context it has, never a 500.
        """
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

        class AlwaysSucceedsAdapter:
            async def ask(self, prompt, context):
                return AIResponse(text="ok despite broken context")

        def factory(provider, api_key):
            return AlwaysSucceedsAdapter()

        async def broken_kpi_dashboard(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(
            "app.services.report_service.ReportService.kpi_dashboard", broken_kpi_dashboard
        )

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=factory)
            response = await service.ask(owner_user, AIAskRequest(prompt="test"))

        assert response.answer == "ok despite broken context"

    async def test_respects_a_client_supplied_viewing_date_range(self, client, owner_user):
        """
        The real new fix: when the person is looking at a specific
        range on the Dashboard (e.g. "last month"), the assistant's
        business context reflects that range instead of always
        defaulting to today -- computed fresh server-side for that
        range, never trusting a number from the client, only the
        dates themselves.
        """
        from datetime import datetime, timedelta

        from app.models.medicine_batch import MedicineBatch
        from app.models.product import Product
        from app.models.sale import Sale, SaleItem

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
            product = Product(name="Viewed Range Test Product", default_selling_price=99.0)
            db.add(product)
            await db.flush()
            batch = MedicineBatch(
                product_id=product.id,
                batch_number="VR1",
                expiry_date=datetime(2027, 1, 1).date(),
                qty_received=10,
                qty_remaining=10,
                cost_price=40.0,
            )
            db.add(batch)
            await db.flush()

            # A real sale placed "yesterday", outside of today's window.
            yesterday = datetime.now() - timedelta(days=1)
            sale = Sale(
                cashier_user_id=owner_user.id,
                subtotal=99.0,
                discount_amount=0.0,
                total_amount=99.0,
            )
            db.add(sale)
            await db.flush()
            sale.created_at = yesterday
            db.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    batch_id=batch.id,
                    quantity=1,
                    unit_price=99.0,
                    line_total=99.0,
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

        yesterday_str = yesterday.date().isoformat()
        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=factory)
            await service.ask(
                owner_user,
                AIAskRequest(
                    prompt="how did that period go?",
                    context={
                        "viewing_start_date": yesterday_str,
                        "viewing_end_date": yesterday_str,
                    },
                ),
            )

        # The viewed-period revenue must include yesterday's real sale
        # -- proving the range was actually used, not just accepted
        # and ignored.
        assert captured_context.get("viewed_period_revenue") == 99.0

    async def test_viewed_date_range_is_respected_not_always_today(self, client, owner_user):
        """
        The real gap this closes: the assistant's business awareness
        was hardcoded to today, with zero connection to whatever range
        someone was actually looking at on the Dashboard's slicer.
        Only a date range crosses from client to server here -- every
        number is still computed fresh, server-side, for that range.
        """
        token = await _login(client, "lucy", "S3curePass!")
        headers = {"Authorization": f"Bearer {token}"}

        product = await client.post(
            "/api/v1/products",
            json={"name": "AI Context Range Product", "default_selling_price": 40.0},
            headers=headers,
        )
        product_id = product.json()["id"]
        await client.post(
            f"/api/v1/products/{product_id}/batches",
            json={
                "batch_number": "AICTX1",
                "expiry_date": "2027-06-30",
                "qty_received": 20,
                "cost_price": 15.0,
            },
            headers=headers,
        )
        sale = await client.post(
            "/api/v1/sales",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 40.0}],
            },
            headers=headers,
        )
        sale_id = sale.json()["id"]

        # Push the sale to yesterday -- outside today's default window.
        yesterday = date.today() - timedelta(days=1)
        async with AsyncSessionLocal() as db:
            from app.models.sale import Sale

            result = await db.execute(select(Sale).where(Sale.id == sale_id))
            row = result.scalar_one()
            row.created_at = datetime.combine(yesterday, datetime.min.time())
            await db.commit()

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
                    prompt="how's business?",
                    context={
                        "viewing_start_date": yesterday.isoformat(),
                        "viewing_end_date": yesterday.isoformat(),
                    },
                ),
            )

        # The viewed-period revenue must reflect yesterday's real
        # sale, not an empty "today" -- proving the range was honored.
        assert captured_context["viewed_period_revenue"] == 40.0

    async def test_malformed_viewing_dates_fall_back_to_today_silently(self, client, owner_user):
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

        class AlwaysSucceedsAdapter:
            async def ask(self, prompt, context):
                return AIResponse(text="ok")

        def factory(provider, api_key):
            return AlwaysSucceedsAdapter()

        async with AsyncSessionLocal() as db:
            service = AIAssistantService(db, adapter_factory=factory)
            # Garbage input must never crash the assistant.
            response = await service.ask(
                owner_user,
                AIAskRequest(
                    prompt="test",
                    context={"viewing_start_date": "not-a-date", "viewing_end_date": 12345},
                ),
            )

        assert response.answer == "ok"


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
