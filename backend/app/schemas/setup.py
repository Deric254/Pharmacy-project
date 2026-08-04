from pydantic import BaseModel, Field

from app.schemas._text import NonBlankName


class SetupStatusOut(BaseModel):
    needs_setup: bool


class FirstUserCreate(BaseModel):
    full_name: NonBlankName = Field(min_length=1, max_length=120)
    username: NonBlankName = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8)
    security_question: NonBlankName = Field(min_length=1, max_length=255)
    security_answer: NonBlankName = Field(min_length=1, max_length=255)
