from datetime import datetime

from pydantic import BaseModel, Field

from app.models.ai_provider_key import AIProviderName


class AIProviderKeyCreate(BaseModel):
    provider: AIProviderName
    api_key: str = Field(min_length=1)
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


class AIAskResponse(BaseModel):
    answer: str
    provider_used: AIProviderName | None
    fallback_used: bool
