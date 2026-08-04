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

import base64
import io
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

# Logo is displayed at small sizes everywhere it's actually shown --
# a sidebar icon, a login-screen mark, and a receipt logo capped at
# 2cm tall (see receipt_service.py). 480px on the longest side is
# generous headroom for all of those and visibly lossless at every
# real display size, while capping what receipt generation has to
# decode. The frontend already limits the source upload to 2MB (see
# SettingsPage.tsx's MAX_LOGO_FILE_BYTES), but that's still enough
# pixel data (e.g. a phone photo used as a logo) to make PIL/reportlab
# fully decode a multi-megapixel image on every single receipt print
# -- confirmed as the actual cause of "receipt works but is slow":
# receipts are generated fresh on every request, never cached (see
# receipt_service.py's own docstring on why), so an unshrunk logo pays
# that full decode cost again and again, forever, on every sale.
# Resizing once here, at save time, means every later receipt decodes
# a small image instead of re-paying that cost on every print.
_LOGO_MAX_DIMENSION = 480


def _shrink_logo(logo_url: str | None) -> str | None:
    if not logo_url or not logo_url.startswith("data:"):
        return logo_url
    try:
        header, b64_data = logo_url.split(",", 1)
        image_bytes = base64.b64decode(b64_data)

        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(image_bytes))
        img.load()
        if img.width <= _LOGO_MAX_DIMENSION and img.height <= _LOGO_MAX_DIMENSION:
            return logo_url  # already small; don't re-encode and lose quality for nothing

        img.thumbnail((_LOGO_MAX_DIMENSION, _LOGO_MAX_DIMENSION), PILImage.LANCZOS)
        # PNG keeps transparency (common for logos) and, at these small
        # dimensions, is reliably smaller than the original upload
        # regardless of its original format.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        out_buffer = io.BytesIO()
        img.save(out_buffer, format="PNG", optimize=True)
        resized_b64 = base64.b64encode(out_buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{resized_b64}"
    except Exception:  # noqa: BLE001 - a bad/unreadable image must never block saving config
        return logo_url


class BusinessConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self) -> BusinessConfigOut:
        cached = await redis_client.get(CACHE_KEY)
        if cached:
            return BusinessConfigOut.model_validate(json.loads(cached))

        config = await self._get_or_create_row()
        # Self-healing for businesses that saved a logo before this
        # shrink existed: caught once here, on a cache miss (at most
        # every CACHE_TTL_SECONDS), persisted so it's a one-time cost
        # rather than a wait on every single receipt print forever.
        # _shrink_logo itself is a no-op (returns the same value) once
        # the stored logo is already small, so this converges to doing
        # nothing at all after the first read post-upgrade.
        shrunk = _shrink_logo(config.logo_url)
        if shrunk != config.logo_url:
            config.logo_url = shrunk
        await self.db.commit()
        out = self._to_schema(config)
        await redis_client.set(CACHE_KEY, out.model_dump_json(), ex=CACHE_TTL_SECONDS)
        return out

    async def update(self, admin: User, changes: BusinessConfigUpdate) -> BusinessConfigOut:
        config = await self._get_or_create_row()

        update_data = changes.model_dump(exclude_unset=True)
        if "logo_url" in update_data:
            update_data["logo_url"] = _shrink_logo(update_data["logo_url"])
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
