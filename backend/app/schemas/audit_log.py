from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    user_id: int | None
    user_name_snapshot: str | None
    action: str
    entity_type: str
    entity_id: str
    old_value: str | None
    new_value: str | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogPage(BaseModel):
    entries: list[AuditLogOut]
    total: int
    limit: int
    offset: int


class AuditLogFilterOptionsOut(BaseModel):
    """
    The real, currently-used values for entity_type and action -- read
    from the data itself, not a hand-maintained list, so a filter
    dropdown built from this can never offer a value that returns zero
    results, and never misses a real one either.
    """

    entity_types: list[str]
    actions: list[str]
