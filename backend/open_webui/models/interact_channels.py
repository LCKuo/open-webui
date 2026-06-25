import asyncio
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint, delete, func, select
from sqlalchemy.exc import IntegrityError

_tables_ready = False
_tables_lock = asyncio.Lock()


class InteractChannel(Base):
    __tablename__ = 'interact_channel'

    id = Column(Text, primary_key=True)
    company_email = Column(Text, nullable=False, index=True)
    channel_type = Column(Text, nullable=False)
    channel_identifier = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    reply_mode = Column(Text, nullable=False, default='ai')
    access_mode = Column(Text, nullable=False, default='public')
    channel_secret = Column(Text, nullable=True)
    channel_access_token = Column(Text, nullable=True)
    model_id = Column(Text, nullable=True)
    fallback_model_id = Column(Text, nullable=True)
    fallback_message = Column(Text, nullable=True)
    rate_limit_per_minute = Column(Integer, nullable=False, default=5)
    daily_user_limit = Column(Integer, nullable=False, default=50)
    daily_bot_token_limit = Column(Integer, nullable=False, default=100000)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('channel_type', 'channel_identifier', name='uq_interact_channel_platform'),)


class InteractChannelEvent(Base):
    __tablename__ = 'interact_channel_event'

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    platform_event_id = Column(Text, nullable=False)
    external_user_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='received')
    reserved_tokens = Column(Integer, nullable=False, default=0)
    billable_tokens = Column(Integer, nullable=False, default=0)
    response_text = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    received_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint('channel_id', 'platform_event_id', name='uq_interact_channel_event'),
        Index('ix_interact_channel_event_received', 'channel_id', 'received_at'),
        Index(
            'ix_interact_channel_event_user_received',
            'channel_id',
            'external_user_id',
            'received_at',
        ),
    )


class InteractChannelModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_email: str
    channel_type: str
    channel_identifier: str
    name: str
    enabled: bool
    reply_mode: str
    access_mode: str
    channel_secret: str | None = None
    channel_access_token: str | None = None
    model_id: str | None = None
    fallback_model_id: str | None = None
    fallback_message: str | None = None
    rate_limit_per_minute: int
    daily_user_limit: int
    daily_bot_token_limit: int
    updated_at: int


class InteractEventClaim(BaseModel):
    allowed: bool
    duplicate: bool
    event_id: str
    reason: str | None = None
    response_text: str | None = None
    retry_delivery: bool = False


def _encrypt_secret(value: str | None) -> str | None:
    return encrypt_data({'value': value}) if value else None


def _decrypt_secret(value: str | None) -> str | None:
    return decrypt_data(value).get('value') if value else None


class InteractChannelsTable:
    async def ensure_tables(self) -> None:
        global _tables_ready
        if _tables_ready:
            return
        async with _tables_lock:
            if _tables_ready:
                return
            async with async_engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection,
                        tables=[InteractChannel.__table__, InteractChannelEvent.__table__],
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    def _model(self, row: InteractChannel) -> InteractChannelModel:
        model = InteractChannelModel.model_validate(row)
        model.channel_secret = _decrypt_secret(row.channel_secret)
        model.channel_access_token = _decrypt_secret(row.channel_access_token)
        return model

    async def upsert(self, data: dict[str, Any]) -> InteractChannelModel:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, data['id'])
            if not row:
                row = InteractChannel(id=data['id'])
                db.add(row)

            row.company_email = data['company_email'].strip().lower()
            row.channel_type = data['channel_type']
            row.channel_identifier = data['channel_identifier']
            row.name = data['name']
            row.enabled = data['enabled']
            row.reply_mode = data['reply_mode']
            row.access_mode = data['access_mode']
            row.channel_secret = _encrypt_secret(data.get('channel_secret'))
            row.channel_access_token = _encrypt_secret(data.get('channel_access_token'))
            row.model_id = data.get('model_id')
            row.fallback_model_id = data.get('fallback_model_id')
            row.fallback_message = data.get('fallback_message')
            row.rate_limit_per_minute = data['rate_limit_per_minute']
            row.daily_user_limit = data['daily_user_limit']
            row.daily_bot_token_limit = data['daily_bot_token_limit']
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return self._model(row)

    async def delete(self, channel_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, channel_id)
            if not row:
                return False
            await db.execute(delete(InteractChannelEvent).where(InteractChannelEvent.channel_id == channel_id))
            await db.delete(row)
            await db.commit()
            return True

    async def get_by_id(self, channel_id: str) -> InteractChannelModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, channel_id)
            return self._model(row) if row else None

    async def get_enabled(
        self,
        channel_type: str,
        channel_identifier: str,
    ) -> InteractChannelModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractChannel).where(
                    InteractChannel.channel_type == channel_type,
                    InteractChannel.channel_identifier == channel_identifier,
                    InteractChannel.enabled.is_(True),
                )
            )
            row = result.scalar_one_or_none()
            return self._model(row) if row else None

    async def get_by_platform(
        self,
        channel_type: str,
        channel_identifier: str,
    ) -> InteractChannelModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractChannel).where(
                    InteractChannel.channel_type == channel_type,
                    InteractChannel.channel_identifier == channel_identifier,
                )
            )
            row = result.scalar_one_or_none()
            return self._model(row) if row else None

    async def claim_event(
        self,
        channel: InteractChannelModel,
        platform_event_id: str,
        external_user_id: str,
        reserved_tokens: int,
        day_start: int,
    ) -> InteractEventClaim:
        await self.ensure_tables()
        now = int(time.time())
        event_id = str(uuid4())
        async with get_async_db_context() as db:
            locked_channel_result = await db.execute(
                select(InteractChannel)
                .where(InteractChannel.id == channel.id)
                .with_for_update()
            )
            locked_channel = locked_channel_result.scalar_one_or_none()
            if not locked_channel:
                return InteractEventClaim(
                    allowed=False,
                    duplicate=False,
                    event_id='',
                    reason='channel-deleted',
                )
            if (
                not locked_channel.enabled
                or locked_channel.channel_type != channel.channel_type
                or locked_channel.channel_identifier != channel.channel_identifier
            ):
                return InteractEventClaim(
                    allowed=False,
                    duplicate=False,
                    event_id='',
                    reason='channel-disabled',
                )

            row = InteractChannelEvent(
                id=event_id,
                channel_id=channel.id,
                platform_event_id=platform_event_id,
                external_user_id=external_user_id,
                status='received',
                reserved_tokens=reserved_tokens,
                received_at=now,
            )
            db.add(row)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                result = await db.execute(
                    select(InteractChannelEvent).where(
                        InteractChannelEvent.channel_id == channel.id,
                        InteractChannelEvent.platform_event_id == platform_event_id,
                    )
                )
                existing = result.scalar_one()
                return InteractEventClaim(
                    allowed=False,
                    duplicate=True,
                    event_id=existing.id,
                    response_text=existing.response_text,
                    retry_delivery=existing.status == 'delivery-failed',
                )

            rpm = await db.scalar(
                select(func.count())
                .select_from(InteractChannelEvent)
                .where(
                    InteractChannelEvent.channel_id == channel.id,
                    InteractChannelEvent.received_at >= now - 60,
                )
            )
            daily_user = await db.scalar(
                select(func.count())
                .select_from(InteractChannelEvent)
                .where(
                    InteractChannelEvent.channel_id == channel.id,
                    InteractChannelEvent.external_user_id == external_user_id,
                    InteractChannelEvent.received_at >= day_start,
                    InteractChannelEvent.status != 'rate-limited',
                )
            )
            daily_tokens = await db.scalar(
                select(
                    func.coalesce(
                        func.sum(InteractChannelEvent.billable_tokens + InteractChannelEvent.reserved_tokens),
                        0,
                    )
                ).where(
                    InteractChannelEvent.channel_id == channel.id,
                    InteractChannelEvent.received_at >= day_start,
                    InteractChannelEvent.status.in_(
                        ['received', 'processing', 'ready', 'completed', 'delivery-failed']
                    ),
                )
            )
            reason = (
                'rpm'
                if int(rpm or 0) > channel.rate_limit_per_minute
                else 'daily-user'
                if int(daily_user or 0) > channel.daily_user_limit
                else 'daily-token'
                if int(daily_tokens or 0) > channel.daily_bot_token_limit
                else None
            )
            if reason:
                row.status = 'rate-limited'
                row.reserved_tokens = 0
                row.error = reason
                row.completed_at = now
            else:
                row.status = 'processing'
            await db.commit()
            return InteractEventClaim(
                allowed=reason is None,
                duplicate=False,
                event_id=event_id,
                reason=reason,
            )

    async def set_response(
        self,
        event_id: str,
        response_text: str,
        billable_tokens: int,
        error: str | None = None,
    ) -> None:
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelEvent, event_id)
            if row:
                if row.status != 'rate-limited':
                    row.status = 'ready'
                row.response_text = response_text
                row.billable_tokens = max(0, billable_tokens)
                row.reserved_tokens = 0
                row.error = error
                await db.commit()

    async def reserve_event_tokens(
        self,
        event_id: str,
        additional_tokens: int,
        daily_limit: int,
        day_start: int,
    ) -> bool:
        additional_tokens = max(0, int(additional_tokens))
        if additional_tokens == 0:
            return True

        async with get_async_db_context() as db:
            event = await db.get(InteractChannelEvent, event_id)
            if not event or event.status not in {'received', 'processing'}:
                return False

            await db.execute(select(InteractChannel).where(InteractChannel.id == event.channel_id).with_for_update())
            daily_tokens = await db.scalar(
                select(
                    func.coalesce(
                        func.sum(InteractChannelEvent.billable_tokens + InteractChannelEvent.reserved_tokens),
                        0,
                    )
                ).where(
                    InteractChannelEvent.channel_id == event.channel_id,
                    InteractChannelEvent.received_at >= day_start,
                    InteractChannelEvent.status.in_(
                        ['received', 'processing', 'ready', 'completed', 'delivery-failed']
                    ),
                )
            )
            if int(daily_tokens or 0) + additional_tokens > daily_limit:
                return False

            event.reserved_tokens += additional_tokens
            await db.commit()
            return True

    async def mark_delivery(self, event_id: str, error: str | None = None) -> None:
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelEvent, event_id)
            if row:
                if error:
                    row.status = 'delivery-failed'
                elif row.status != 'rate-limited':
                    row.status = 'completed'
                row.error = error[:1000] if error else row.error
                row.completed_at = int(time.time())
                await db.commit()


InteractChannels = InteractChannelsTable()
