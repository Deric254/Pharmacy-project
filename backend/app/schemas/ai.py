from datetime import datetime

from pydantic import BaseModel, Field

from app.models.ai_provider_key import AIProviderName


class AIProviderKeyCreate(BaseModel):
    provider: AIProviderName
    # 16 is well below every real provider's actual key length (OpenAI,
    # Anthropic, and Google keys are all 20+ characters) -- this exists
    # specifically to catch an accidental paste/truncation error before
    # it reaches the masking logic below. Without a real floor here, a
    # key shorter than 4 characters has its ENTIRE value stored as
    # key_hint and shown in the "masked" display (••••{key_hint}) --
    # for a genuinely short string, that's not masked at all.
    api_key: str = Field(min_length=16)
    priority: int = Field(default=1, ge=1)


class AIProviderKeyOut(BaseModel):
    id: int
    provider: AIProviderName
    masked_key: str
    priority: int
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class AIAskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    context: dict[str, str | int | float | bool | None] | None = None
    # None means "start a new conversation"; the server creates one and
    # returns its id. Passing an existing id appends this turn to that
    # thread instead. Ownership is checked server-side -- see
    # AIConversationService.
    conversation_id: int | None = None


class AIAskResponse(BaseModel):
    answer: str
    provider_used: AIProviderName | None
    fallback_used: bool
    conversation_id: int


class AIConversationMessageOut(BaseModel):
    prompt: str
    answer: str
    provider_used: AIProviderName | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AIConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIConversationDetailOut(AIConversationOut):
    messages: list[AIConversationMessageOut]
