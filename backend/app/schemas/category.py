from pydantic import BaseModel, Field

from app.schemas._text import NonBlankName


class CategoryCreate(BaseModel):
    name: NonBlankName = Field(min_length=1, max_length=80)


class CategoryOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}
