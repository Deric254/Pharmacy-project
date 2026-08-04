from pydantic import BaseModel, Field

from app.schemas._text import NonBlankName


class PermissionOut(BaseModel):
    code: str
    description: str

    model_config = {"from_attributes": True}


class RoleDetailOut(BaseModel):
    id: int
    name: str
    description: str
    is_system: bool
    permissions: list[str]
    user_count: int

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    name: NonBlankName = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=255)
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: NonBlankName | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    permission_codes: list[str] | None = None
