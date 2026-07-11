import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

_tables_ready = False
_tables_lock = asyncio.Lock()
_job_claim_lock = asyncio.Lock()
_event_claim_lock = asyncio.Lock()


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
    rate_limit_per_minute = Column(Integer, nullable=False, default=60)
    user_rate_limit_per_minute = Column(Integer, nullable=False, default=5)
    max_concurrent_jobs = Column(Integer, nullable=False, default=8)
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


class InteractChannelJob(Base):
    __tablename__ = 'interact_channel_job'

    id = Column(Text, primary_key=True)
    event_id = Column(Text, nullable=False, unique=True)
    channel_id = Column(Text, nullable=False)
    external_user_id = Column(Text, nullable=False)
    conversation_key = Column(Text, nullable=False)
    platform = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default='queued')
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(BigInteger, nullable=False)
    lease_owner = Column(Text, nullable=True)
    lease_expires_at = Column(BigInteger, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_interact_channel_job_available', 'status', 'available_at', 'created_at'),
        Index('ix_interact_channel_job_conversation', 'conversation_key', 'status', 'created_at'),
        Index('ix_interact_channel_job_channel_status', 'channel_id', 'status'),
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
    user_rate_limit_per_minute: int = 5
    max_concurrent_jobs: int = 8
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


class InteractChannelJobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    channel_id: str
    external_user_id: str
    conversation_key: str
    platform: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    status: str
    attempts: int
    lease_owner: str | None = None
    lease_expires_at: int | None = None


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
                        tables=[
                            InteractChannel.__table__,
                            InteractChannelEvent.__table__,
                            InteractChannelJob.__table__,
                        ],
                        checkfirst=True,
                    )
                )
                await connection.run_sync(self._ensure_channel_columns)
            _tables_ready = True

    @staticmethod
    def _ensure_channel_columns(sync_connection) -> None:
        table = InteractChannel.__table__
        columns = {
            column['name']
            for column in inspect(sync_connection).get_columns(table.name, schema=table.schema)
        }
        formatted_table = sync_connection.dialect.identifier_preparer.format_table(table)
        additions = {
            'user_rate_limit_per_minute': 'INTEGER NOT NULL DEFAULT 5',
            'max_concurrent_jobs': 'INTEGER NOT NULL DEFAULT 8',
        }
        for name, definition in additions.items():
            if name not in columns:
                sync_connection.execute(text(f'ALTER TABLE {formatted_table} ADD COLUMN {name} {definition}'))

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
            row.user_rate_limit_per_minute = data['user_rate_limit_per_minute']
            row.max_concurrent_jobs = data['max_concurrent_jobs']
            row.daily_user_limit = data['daily_user_limit']
            row.daily_bot_token_limit = data['daily_bot_token_limit']
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return self._model(row)

    async def rekey(self, old_id: str, new_id: str) -> bool:
        if old_id == new_id:
            return True
        await self.ensure_tables()
        async with get_async_db_context() as db:
            existing_new = await db.get(InteractChannel, new_id)
            if existing_new:
                return False

            row = await db.get(InteractChannel, old_id)
            if not row:
                return False

            await db.execute(
                update(InteractChannelEvent)
                .where(InteractChannelEvent.channel_id == old_id)
                .values(channel_id=new_id)
            )
            jobs = (
                await db.execute(
                    select(InteractChannelJob).where(InteractChannelJob.channel_id == old_id)
                )
            ).scalars().all()
            for job in jobs:
                job.channel_id = new_id
                job.conversation_key = f'{new_id}:{job.external_user_id}'
            row.id = new_id
            await db.commit()
            return True

    async def delete(self, channel_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, channel_id)
            if not row:
                return False
            await db.execute(delete(InteractChannelJob).where(InteractChannelJob.channel_id == channel_id))
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
        async with _event_claim_lock:
            return await self._claim_event(
                channel,
                platform_event_id,
                external_user_id,
                reserved_tokens,
                day_start,
            )

    async def _claim_event(
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
                    InteractChannelEvent.status != 'rate-limited',
                )
            )
            user_rpm = await db.scalar(
                select(func.count())
                .select_from(InteractChannelEvent)
                .where(
                    InteractChannelEvent.channel_id == channel.id,
                    InteractChannelEvent.external_user_id == external_user_id,
                    InteractChannelEvent.received_at >= now - 60,
                    InteractChannelEvent.status != 'rate-limited',
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
                else 'user-rpm'
                if int(user_rpm or 0) > channel.user_rate_limit_per_minute
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

    @staticmethod
    def _job_model(row: InteractChannelJob) -> InteractChannelJobModel:
        return InteractChannelJobModel(
            id=row.id,
            event_id=row.event_id,
            channel_id=row.channel_id,
            external_user_id=row.external_user_id,
            conversation_key=row.conversation_key,
            platform=row.platform,
            payload=json.loads(row.payload_json),
            result=json.loads(row.result_json) if row.result_json else None,
            status=row.status,
            attempts=row.attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
        )

    async def enqueue_job(
        self,
        event_id: str,
        channel_id: str,
        external_user_id: str,
        platform: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
    ) -> InteractChannelJobModel:
        await self.ensure_tables()
        now = int(time.time())
        row = InteractChannelJob(
            id=str(uuid4()),
            event_id=event_id,
            channel_id=channel_id,
            external_user_id=external_user_id,
            conversation_key=f'{channel_id}:{conversation_id or external_user_id}',
            platform=platform,
            payload_json=json.dumps(payload, ensure_ascii=False),
            status='queued',
            attempts=0,
            available_at=now,
            created_at=time.time_ns(),
            updated_at=now,
        )
        async with get_async_db_context() as db:
            db.add(row)
            try:
                await db.commit()
                await db.refresh(row)
            except IntegrityError:
                await db.rollback()
                existing = await db.scalar(select(InteractChannelJob).where(InteractChannelJob.event_id == event_id))
                if not existing:
                    raise
                if existing.status == 'dead':
                    existing.status = 'retry'
                    existing.attempts = 0
                    existing.available_at = now
                    existing.lease_owner = None
                    existing.lease_expires_at = None
                    existing.updated_at = now
                    await db.commit()
                    await db.refresh(existing)
                row = existing
            return self._job_model(row)

    async def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> InteractChannelJobModel | None:
        # SQLite ignores SELECT FOR UPDATE. This process lock preserves atomic
        # claims in the supported single-process SQLite development mode;
        # PostgreSQL row locks coordinate across production worker processes.
        async with _job_claim_lock:
            return await self._claim_next_job(worker_id, lease_seconds)

    async def _claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> InteractChannelJobModel | None:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            await db.execute(
                update(InteractChannelJob)
                .where(
                    InteractChannelJob.status.in_(['processing', 'delivery']),
                    InteractChannelJob.lease_expires_at.is_not(None),
                    InteractChannelJob.lease_expires_at <= now,
                )
                .values(
                    status='retry',
                    available_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            pending_job = aliased(InteractChannelJob)
            earliest_for_conversation = (
                select(func.min(pending_job.created_at))
                .where(
                    pending_job.conversation_key == InteractChannelJob.conversation_key,
                    pending_job.status.in_(['queued', 'retry']),
                    pending_job.available_at <= now,
                )
                .correlate(InteractChannelJob)
                .scalar_subquery()
            )
            candidates = (
                await db.execute(
                    select(InteractChannelJob)
                    .where(
                        InteractChannelJob.status.in_(['queued', 'retry']),
                        InteractChannelJob.available_at <= now,
                        InteractChannelJob.created_at == earliest_for_conversation,
                    )
                    .order_by(InteractChannelJob.created_at.asc(), InteractChannelJob.id.asc())
                    .limit(50)
                )
            ).scalars().all()

            for candidate in candidates:
                await db.execute(
                    select(InteractChannel).where(InteractChannel.id == candidate.channel_id).with_for_update()
                )
                job = await db.scalar(
                    select(InteractChannelJob)
                    .where(InteractChannelJob.id == candidate.id)
                    .with_for_update()
                )
                if not job or job.status not in {'queued', 'retry'} or job.available_at > now:
                    continue
                channel = await db.get(InteractChannel, job.channel_id)
                if not channel or not channel.enabled:
                    job.status = 'dead'
                    job.last_error = 'channel-disabled'
                    job.updated_at = now
                    continue
                active_count = await db.scalar(
                    select(func.count())
                    .select_from(InteractChannelJob)
                    .where(
                        InteractChannelJob.channel_id == job.channel_id,
                        InteractChannelJob.status.in_(['processing', 'delivery']),
                        InteractChannelJob.lease_expires_at > now,
                    )
                )
                if int(active_count or 0) >= channel.max_concurrent_jobs:
                    continue
                active_conversation = await db.scalar(
                    select(func.count())
                    .select_from(InteractChannelJob)
                    .where(
                        InteractChannelJob.conversation_key == job.conversation_key,
                        InteractChannelJob.status.in_(['processing', 'delivery']),
                        InteractChannelJob.lease_expires_at > now,
                    )
                )
                earlier_waiting = await db.scalar(
                    select(func.count())
                    .select_from(InteractChannelJob)
                    .where(
                        InteractChannelJob.conversation_key == job.conversation_key,
                        InteractChannelJob.status.in_(['queued', 'retry']),
                        InteractChannelJob.created_at < job.created_at,
                    )
                )
                if int(active_conversation or 0) or int(earlier_waiting or 0):
                    continue
                job.status = 'delivery' if job.result_json else 'processing'
                job.attempts += 1
                job.lease_owner = worker_id
                job.lease_expires_at = now + lease_seconds
                job.updated_at = now
                await db.commit()
                await db.refresh(job)
                return self._job_model(job)

            await db.commit()
            return None

    async def save_job_result(self, job_id: str, result: dict[str, Any], lease_seconds: int = 300) -> None:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelJob, job_id)
            if row:
                row.result_json = json.dumps(result, ensure_ascii=False)
                row.status = 'delivery'
                row.lease_expires_at = now + lease_seconds
                row.updated_at = now
                await db.commit()

    async def extend_job_lease(self, job_id: str, worker_id: str, lease_seconds: int = 300) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            result = await db.execute(
                update(InteractChannelJob)
                .where(
                    InteractChannelJob.id == job_id,
                    InteractChannelJob.lease_owner == worker_id,
                    InteractChannelJob.status.in_(['processing', 'delivery']),
                )
                .values(lease_expires_at=now + lease_seconds, updated_at=now)
            )
            await db.commit()
            return bool(result.rowcount)

    async def complete_job(self, job_id: str) -> None:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelJob, job_id)
            if not row:
                return
            row.status = 'completed'
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = now
            event = await db.get(InteractChannelEvent, row.event_id)
            if event:
                event.status = 'completed'
                event.completed_at = now
            await db.commit()

    async def release_job(self, job_id: str, error: str = 'worker-shutdown') -> None:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelJob, job_id)
            if not row or row.status not in {'processing', 'delivery'}:
                return
            row.status = 'delivery' if row.result_json else 'retry'
            row.available_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error = error[:1000]
            row.updated_at = now
            await db.commit()

    async def retry_job(self, job_id: str, error: str, max_attempts: int = 5) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelJob, job_id)
            if not row:
                return False
            exhausted = row.attempts >= max_attempts
            row.status = 'dead' if exhausted else 'retry'
            row.available_at = now if exhausted else now + min(300, 2 ** row.attempts)
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error = error[:1000]
            row.updated_at = now
            event = await db.get(InteractChannelEvent, row.event_id)
            if event and exhausted:
                event.status = 'delivery-failed'
                event.error = error[:1000]
                event.completed_at = now
            await db.commit()
            return not exhausted

    async def queue_health(self, company_email: str) -> dict[str, Any]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            channel_ids = (
                await db.execute(
                    select(InteractChannel.id).where(
                        InteractChannel.company_email == company_email.strip().lower()
                    )
                )
            ).scalars().all()
            if not channel_ids:
                return {'queued': 0, 'processing': 0, 'delivery': 0, 'retry': 0, 'dead': 0}
            rows = (
                await db.execute(
                    select(InteractChannelJob.status, func.count())
                    .where(InteractChannelJob.channel_id.in_(channel_ids))
                    .group_by(InteractChannelJob.status)
                )
            ).all()
            counts = {status: int(count) for status, count in rows}
            return {
                key: counts.get(key, 0)
                for key in ('queued', 'processing', 'delivery', 'retry', 'dead')
            }

    async def cleanup_jobs(self, completed_retention_days: int = 7, dead_retention_days: int = 30) -> None:
        await self.ensure_tables()
        now_ns = time.time_ns()
        completed_cutoff = now_ns - completed_retention_days * 86_400 * 1_000_000_000
        dead_cutoff = now_ns - dead_retention_days * 86_400 * 1_000_000_000
        async with get_async_db_context() as db:
            await db.execute(
                delete(InteractChannelJob).where(
                    (InteractChannelJob.status == 'completed') & (InteractChannelJob.created_at < completed_cutoff)
                    | (InteractChannelJob.status == 'dead') & (InteractChannelJob.created_at < dead_cutoff)
                )
            )
            await db.commit()


InteractChannels = InteractChannelsTable()
