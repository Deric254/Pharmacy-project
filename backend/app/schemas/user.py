from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8)
    role_id: int
    security_question: str = Field(min_length=1, max_length=255)
    security_answer: str = Field(min_length=1, max_length=255)


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
