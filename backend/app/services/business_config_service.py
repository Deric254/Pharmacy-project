"""
Config service.

Read path: Redis cache first (this table is read on nearly every
screen load — receipts, dashboard header, login page branding — so it
must never hit MySQL per-request). Cache populated on first read and
refreshed on every write.

Write path: one DB transaction, then cache overwritten (not just
invalidated — overwritten immediately, so the very next read anywhere
gets the new value with no cache-miss race), then a `config.updated`
event published so every connected client (WebSocket) can refresh live
without polling.
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import BusinessConfigUpdatedEvent, publish
from app.core.redis_client import redis_client
from app.models.audit_log import AuditLog
from app.models.business_config import BusinessConfig
from app.models.user import User
from app.schemas.business_config import BusinessConfigOut, BusinessConfigUpdate

CACHE_KEY = "business_config:v1"
CACHE_TTL_SECONDS = 300  # short TTL as a safety net; writes overwrite it immediately anyway


class BusinessConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self) -> BusinessConfigOut:
        cached = await redis_client.get(CACHE_KEY)
        if cached:
            return BusinessConfigOut.model_validate(json.loads(cached))

        config = await self._get_or_create_row()
        await self.db.commit()
        out = self._to_schema(config)
        await redis_client.set(CACHE_KEY, out.model_dump_json(), ex=CACHE_TTL_SECONDS)
        return out

    async def update(self, admin: User, changes: BusinessConfigUpdate) -> BusinessConfigOut:
        config = await self._get_or_create_row()

        update_data = changes.model_dump(exclude_unset=True)
        for field, new_value in update_data.items():
            old_value = getattr(config, field)
            if field == "expiry_alert_days" and new_value is not None:
                new_value = ",".join(str(d) for d in new_value)
            if old_value != new_value:
                self.db.add(
                    AuditLog(
                        user_id=admin.id,
                        user_name_snapshot=admin.full_name,
                        action="config.updated",
                        entity_type="business_config",
                        entity_id="1",
                        old_value=str(old_value),
                        new_value=str(new_value),
                    )
                )
                setattr(config, field, new_value)

        await self.db.commit()
        await self.db.refresh(config)

        out = self._to_schema(config)
        # Overwrite cache immediately -- never leave a stale value sitting
        # until the next natural read repopulates it.
        await redis_client.set(CACHE_KEY, out.model_dump_json(), ex=CACHE_TTL_SECONDS)
        await publish(BusinessConfigUpdatedEvent())
        return out

    async def _get_or_create_row(self) -> BusinessConfig:
        result = await self.db.execute(select(BusinessConfig).where(BusinessConfig.id == 1))
        config = result.scalar_one_or_none()
        if config is None:
            config = BusinessConfig(id=1)
            self.db.add(config)
            await self.db.flush()
            await self.db.refresh(config, attribute_names=["updated_at"])
        return config

    @staticmethod
    def _to_schema(config: BusinessConfig) -> BusinessConfigOut:
        return BusinessConfigOut(
            business_name=config.business_name,
            slogan=config.slogan,
            logo_url=config.logo_url,
            theme_name=config.theme_name,
            primary_color=config.primary_color,
            secondary_color=config.secondary_color,
            receipt_header_text=config.receipt_header_text,
            receipt_footer_text=config.receipt_footer_text,
            currency=config.currency,
            tax_rate=config.tax_rate,
            tax_id=config.tax_id,
            contact_phone=config.contact_phone,
            contact_email=config.contact_email,
            address=config.address,
            default_language=config.default_language,
            timezone=config.timezone,
            low_stock_threshold_default=config.low_stock_threshold_default,
            expiry_alert_days=[int(d) for d in config.expiry_alert_days.split(",") if d],
            loyalty_program_enabled=config.loyalty_program_enabled,
            loyalty_points_per_currency_unit=config.loyalty_points_per_currency_unit,
            local_backup_dir_override=config.local_backup_dir_override,
        )
