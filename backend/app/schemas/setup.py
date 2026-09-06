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
    # Deliberately a plain optional string, not ValidTimezone -- an
    # invalid value here must never block creating the actual account
    # (see SetupService.create_first_user, where this is applied
    # best-effort, after the account itself is already committed).
    # Meant to be filled in automatically from the browser's own
    # detected zone, not typed by hand, so it should essentially
    # always be valid in practice; this field just refuses to let a
    # timezone hiccup turn into a failed setup either way.
    timezone: str | None = None
