from pydantic import BaseModel, Field

from app.schemas._text import NonBlankName


class UserCreate(BaseModel):
    full_name: NonBlankName = Field(min_length=1, max_length=120)
    # Stripped + non-blank matters even more here than on a display
    # name: username is what someone types to log in every single
    # shift. A username stored with a stray trailing space from a
    # copy-paste would make login fail in a way that looks exactly
    # like a wrong password, with nothing to suggest the real cause.
    username: NonBlankName = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8)
    role_id: int
    security_question: NonBlankName = Field(min_length=1, max_length=255)
    security_answer: NonBlankName = Field(min_length=1, max_length=255)


class UserListItemOut(BaseModel):
    id: int
    full_name: str
    username: str
    role_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class RoleOut(BaseModel):
    id: int
    name: str
    description: str

    model_config = {"from_attributes": True}
