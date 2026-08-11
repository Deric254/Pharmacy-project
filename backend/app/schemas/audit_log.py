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
