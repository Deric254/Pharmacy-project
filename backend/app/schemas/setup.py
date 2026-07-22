from pydantic import BaseModel, Field


class SetupStatusOut(BaseModel):
    needs_setup: bool


class FirstUserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8)
