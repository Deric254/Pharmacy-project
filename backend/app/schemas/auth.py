from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    username: str
    security_answer: str
    new_password: str = Field(min_length=8)


class AdminResetPasswordRequest(BaseModel):
    user_id: int
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    full_name: str
    username: str
    role_name: str
    is_active: bool

    model_config = {"from_attributes": True}
