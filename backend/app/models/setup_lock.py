from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SetupLock(Base):
    """
    Exists purely so first-user creation can be made atomic. The
    service inserts a row with id=1 in the SAME transaction as
    creating the owner account; a second concurrent request's attempt
    to insert the same id=1 row hits a real primary-key conflict and
    rolls back cleanly, instead of racing a plain COUNT(*) check that
    both requests could pass before either commits.
    """

    __tablename__ = "setup_lock"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
