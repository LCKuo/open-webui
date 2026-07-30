import asyncio
import hashlib
import os
import time
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from open_webui.internal.db import Base, async_engine, get_async_db_context
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Integer, Text, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

_tables_ready = False
_tables_lock = asyncio.Lock()
_delivery_locks: dict[str, asyncio.Lock] = {}

EmailAccessMode = Literal['company_admins', 'all_company_members', 'selected_members', 'selected_groups']
EmailConnectorStatus = Literal['unconfigured', 'ready', 'disabled', 'error']


class EmailDailyLimitExceeded(Exception):
    pass


def _daily_start_ns() -> int:
    timezone_name = os.environ.get('INTERACT_EMAIL_DAILY_LIMIT_TIMEZONE', 'Asia/Taipei')
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo('UTC')
    start_of_day = datetime.now(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start_of_day.timestamp() * 1_000_000_000)


def _encrypt(value: str | None) -> str | None:
    clean = str(value or '').strip()
    return encrypt_data({'value': clean}) if clean else None


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    return str(decrypt_data(value).get('value') or '') or None


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class InteractEmailConnector(Base):
    __tablename__ = 'interact_email_connector'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    company_email = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    provider = Column(Text, nullable=False, default='resend')
    enabled = Column(Boolean, nullable=False, default=False)
    status = Column(Text, nullable=False, default='unconfigured')
    api_key_encrypted = Column(Text, nullable=True)
    key_last4 = Column(Text, nullable=True)
    webhook_secret_encrypted = Column(Text, nullable=True)
    from_name = Column(Text, nullable=True)
    from_address = Column(Text, nullable=False)
    reply_to = Column(Text, nullable=True)
    verified_domain = Column(Text, nullable=True)
    access_mode = Column(Text, nullable=False, default='company_admins')
    allowed_member_ids = Column(JSON, nullable=False, default=list)
    allowed_group_ids = Column(JSON, nullable=False, default=list)
    allowed_workflow_ids = Column(JSON, nullable=False, default=list)
    allowed_channel_ids = Column(JSON, nullable=False, default=list)
    cc_policy = Column(JSON, nullable=False, default=dict)
    recipient_policy = Column(JSON, nullable=False, default=dict)
    daily_send_limit = Column(Integer, nullable=False, default=500)
    max_recipients_per_send = Column(Integer, nullable=False, default=20)
    last_test_at = Column(BigInteger, nullable=True)
    last_error = Column(Text, nullable=True)
    managed_by = Column(Text, nullable=False, default='company_portal')
    control_plane_revision = Column(Integer, nullable=False, default=0)
    control_plane_status = Column(Text, nullable=False, default='active')
    quarantined_at = Column(BigInteger, nullable=True)
    last_control_plane_seen_at = Column(BigInteger, nullable=True)
    created_by = Column(Text, nullable=False)
    updated_by = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_interact_email_connector_company_updated', 'company_user_id', 'updated_at'),
        Index('ix_interact_email_connector_company_enabled', 'company_user_id', 'enabled'),
    )


class InteractEmailDelivery(Base):
    __tablename__ = 'interact_email_delivery'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    connector_id = Column(Text, nullable=False, index=True)
    workflow_id = Column(Text, nullable=True, index=True)
    workflow_run_id = Column(Text, nullable=True, index=True)
    requested_by = Column(Text, nullable=False)
    channel_id = Column(Text, nullable=True)
    provider_message_id = Column(Text, nullable=True, index=True)
    idempotency_key = Column(Text, nullable=False, unique=True)
    status = Column(Text, nullable=False, default='queued')
    recipient_count = Column(Integer, nullable=False, default=1)
    recipient_domains = Column(JSON, nullable=False, default=list)
    to_encrypted = Column(Text, nullable=False)
    cc_encrypted = Column(Text, nullable=True)
    subject_encrypted = Column(Text, nullable=False)
    content_encrypted = Column(Text, nullable=False)
    payload_hash = Column(Text, nullable=False)
    error_code = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    sent_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index('ix_interact_email_delivery_company_created', 'company_user_id', 'created_at'),
        Index('ix_interact_email_delivery_connector_created', 'connector_id', 'created_at'),
    )


class InteractEmailEvent(Base):
    __tablename__ = 'interact_email_event'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    delivery_id = Column(Text, nullable=True, index=True)
    provider_event_id = Column(Text, nullable=False, unique=True)
    provider_message_id = Column(Text, nullable=True, index=True)
    event_type = Column(Text, nullable=False)
    occurred_at = Column(BigInteger, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class WorkflowCheckpoint(Base):
    __tablename__ = 'workflow_checkpoint'

    id = Column(Text, primary_key=True)
    workflow_run_id = Column(Text, nullable=False, unique=True, index=True)
    company_user_id = Column(Text, nullable=False, index=True)
    workflow_id = Column(Text, nullable=False, index=True)
    node_id = Column(Text, nullable=False)
    wait_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='waiting')
    state_encrypted = Column(Text, nullable=False)
    prompt = Column(JSON, nullable=False)
    payload_hash = Column(Text, nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    expires_at = Column(BigInteger, nullable=False)
    consumed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class WorkflowApproval(Base):
    __tablename__ = 'workflow_approval'

    id = Column(Text, primary_key=True)
    workflow_run_id = Column(Text, nullable=False, index=True)
    checkpoint_id = Column(Text, nullable=False, index=True)
    company_user_id = Column(Text, nullable=False, index=True)
    node_id = Column(Text, nullable=False)
    payload_hash = Column(Text, nullable=False)
    decision = Column(Text, nullable=False)
    decided_by = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    decided_at = Column(BigInteger, nullable=False)


class EmailConnectorUpsertForm(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=80)
    provider: Literal['resend'] = 'resend'
    enabled: bool = False
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=500)
    webhook_secret: Optional[str] = Field(default=None, min_length=8, max_length=500)
    from_name: Optional[str] = Field(default=None, max_length=120)
    from_address: str = Field(..., min_length=3, max_length=320)
    reply_to: Optional[str] = Field(default=None, max_length=320)
    verified_domain: Optional[str] = Field(default=None, max_length=253)
    access_mode: EmailAccessMode = 'company_admins'
    allowed_member_ids: list[str] = Field(default_factory=list, max_length=500)
    allowed_group_ids: list[str] = Field(default_factory=list, max_length=500)
    allowed_workflow_ids: list[str] = Field(default_factory=list, max_length=500)
    allowed_channel_ids: list[str] = Field(default_factory=list, max_length=500)
    cc_policy: dict[str, Any] = Field(default_factory=dict)
    recipient_policy: dict[str, Any] = Field(default_factory=dict)
    daily_send_limit: int = Field(default=500, ge=1, le=100000)
    max_recipients_per_send: int = Field(default=20, ge=1, le=100)

    @field_validator('allowed_member_ids', 'allowed_group_ids', 'allowed_workflow_ids', 'allowed_channel_ids')
    @classmethod
    def clean_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    @field_validator('api_key', 'webhook_secret')
    @classmethod
    def clean_secret(cls, value: str | None) -> str | None:
        clean = str(value or '').strip()
        return clean or None

    @field_validator('from_address', 'reply_to')
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower()
        if '\r' in clean or '\n' in clean or clean.count('@') != 1 or clean.startswith('@') or clean.endswith('@'):
            raise ValueError('Enter a valid email address.')
        return clean

    @field_validator('from_name')
    @classmethod
    def validate_header_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if '\r' in clean or '\n' in clean:
            raise ValueError('Sender name cannot contain line breaks.')
        return clean or None

    @field_validator('verified_domain')
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower().rstrip('.')
        if not clean or any(part in clean for part in ('\r', '\n', '://', '/', '@', ' ')):
            raise ValueError('Enter a domain name without a protocol or path.')
        return clean

    @model_validator(mode='after')
    def validate_selected_access(self):
        if self.access_mode == 'selected_members' and not self.allowed_member_ids:
            raise ValueError('Selected member access requires at least one member.')
        if self.access_mode == 'selected_groups' and not self.allowed_group_ids:
            raise ValueError('Selected group access requires at least one group.')
        return self


class EmailConnectorModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_user_id: str
    company_email: str
    name: str
    provider: str
    enabled: bool
    status: str
    key_last4: Optional[str] = None
    has_api_key: bool = False
    has_webhook_secret: bool = False
    from_name: Optional[str] = None
    from_address: str
    reply_to: Optional[str] = None
    verified_domain: Optional[str] = None
    access_mode: str
    allowed_member_ids: list[str]
    allowed_group_ids: list[str]
    allowed_workflow_ids: list[str]
    allowed_channel_ids: list[str]
    cc_policy: dict[str, Any]
    recipient_policy: dict[str, Any]
    daily_send_limit: int
    max_recipients_per_send: int
    last_test_at: Optional[int] = None
    last_error: Optional[str] = None
    managed_by: str = 'company_portal'
    control_plane_revision: int = 0
    control_plane_status: str = 'active'
    quarantined_at: Optional[int] = None
    last_control_plane_seen_at: Optional[int] = None
    created_by: str
    updated_by: str
    created_at: int
    updated_at: int


class EmailDeliveryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_user_id: str
    connector_id: str
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    requested_by: str
    channel_id: Optional[str] = None
    provider_message_id: Optional[str] = None
    idempotency_key: str
    status: str
    recipient_count: int
    recipient_domains: list[str]
    payload_hash: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: int
    updated_at: int
    sent_at: Optional[int] = None


def _connector_model(row: InteractEmailConnector) -> EmailConnectorModel:
    return EmailConnectorModel(
        **{
            column.name: getattr(row, column.name)
            for column in InteractEmailConnector.__table__.columns
            if column.name not in {'api_key_encrypted', 'webhook_secret_encrypted'}
        },
        has_api_key=bool(row.api_key_encrypted),
        has_webhook_secret=bool(row.webhook_secret_encrypted),
    )


class InteractEmailTable:
    async def ensure_tables(self) -> None:
        global _tables_ready
        if _tables_ready:
            return
        async with _tables_lock:
            if _tables_ready:
                return
            tables = [
                InteractEmailConnector.__table__,
                InteractEmailDelivery.__table__,
                InteractEmailEvent.__table__,
                WorkflowCheckpoint.__table__,
                WorkflowApproval.__table__,
            ]
            async with async_engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables, checkfirst=True)
                )
                await connection.run_sync(self._ensure_control_plane_columns)
            _tables_ready = True

    @staticmethod
    def _ensure_control_plane_columns(sync_connection) -> None:
        table = InteractEmailConnector.__table__
        columns = {
            column['name'] for column in inspect(sync_connection).get_columns(table.name, schema=table.schema)
        }
        additions = {
            'managed_by': "TEXT NOT NULL DEFAULT 'company_portal'",
            'control_plane_revision': 'INTEGER NOT NULL DEFAULT 0',
            'control_plane_status': "TEXT NOT NULL DEFAULT 'active'",
            'quarantined_at': 'BIGINT',
            'last_control_plane_seen_at': 'BIGINT',
        }
        formatted_table = sync_connection.dialect.identifier_preparer.format_table(table)
        for name, definition in additions.items():
            if name not in columns:
                sync_connection.execute(text(f'ALTER TABLE {formatted_table} ADD COLUMN {name} {definition}'))

    async def list_connectors(
        self,
        company_user_id: str,
        *,
        include_quarantined: bool = True,
    ) -> list[EmailConnectorModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            statement = select(InteractEmailConnector).where(
                InteractEmailConnector.company_user_id == company_user_id
            )
            if not include_quarantined:
                statement = statement.where(InteractEmailConnector.control_plane_status == 'active')
            rows = (
                (
                    await db.execute(
                        statement.order_by(InteractEmailConnector.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_connector_model(row) for row in rows]

    async def get_connector(self, connector_id: str) -> Optional[InteractEmailConnector]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            return await db.get(InteractEmailConnector, connector_id)

    async def upsert_connector(
        self,
        company_user_id: str,
        company_email: str,
        actor_id: str,
        form: EmailConnectorUpsertForm,
        *,
        control_plane_revision: int | None = None,
        managed_by: str | None = None,
    ) -> EmailConnectorModel:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractEmailConnector, form.id) if form.id else None
            if row and row.company_user_id != company_user_id:
                raise PermissionError('Email connector belongs to another company.')
            if (
                row
                and control_plane_revision is not None
                and control_plane_revision < int(row.control_plane_revision or 0)
            ):
                raise ValueError('Control-plane revision is older than the runtime connector.')
            now = int(time.time_ns())
            if not row:
                row = InteractEmailConnector(
                    id=form.id or str(uuid4()),
                    company_user_id=company_user_id,
                    company_email=company_email.strip().lower(),
                    created_by=actor_id,
                    created_at=now,
                )
                db.add(row)
            values = form.model_dump(exclude={'id', 'api_key', 'webhook_secret'})
            values['from_address'] = str(form.from_address).lower()
            values['reply_to'] = str(form.reply_to).lower() if form.reply_to else None
            for key, value in values.items():
                setattr(row, key, value)
            if form.api_key:
                row.api_key_encrypted = _encrypt(form.api_key)
                row.key_last4 = form.api_key[-4:]
            if form.webhook_secret:
                row.webhook_secret_encrypted = _encrypt(form.webhook_secret)
            if control_plane_revision is not None:
                row.control_plane_revision = control_plane_revision
                row.control_plane_status = 'active'
                row.quarantined_at = None
                row.last_control_plane_seen_at = now
            if managed_by:
                row.managed_by = managed_by
            row.status = (
                'ready' if row.enabled and row.api_key_encrypted else 'disabled' if not row.enabled else 'unconfigured'
            )
            row.updated_by = actor_id
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return _connector_model(row)

    async def quarantine_connector(self, connector_id: str, company_user_id: str) -> EmailConnectorModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractEmailConnector, connector_id)
            if not row or row.company_user_id != company_user_id:
                return None
            now = int(time.time_ns())
            row.enabled = False
            row.status = 'disabled'
            row.control_plane_status = 'orphaned'
            row.quarantined_at = row.quarantined_at or now
            row.last_control_plane_seen_at = now
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return _connector_model(row)

    async def delete_connector(self, connector_id: str, company_user_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                delete(InteractEmailConnector).where(
                    InteractEmailConnector.id == connector_id,
                    InteractEmailConnector.company_user_id == company_user_id,
                )
            )
            await db.commit()
            return bool(result.rowcount)

    async def set_connector_health(
        self,
        connector_id: str,
        company_user_id: str,
        ok: bool,
        error: str | None = None,
    ) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractEmailConnector, connector_id)
            if not row or row.company_user_id != company_user_id:
                return
            row.status = 'ready' if ok and row.enabled else 'disabled' if ok else 'error'
            row.last_test_at = int(time.time_ns())
            row.last_error = error[:1000] if error else None
            row.updated_at = int(time.time_ns())
            await db.commit()

    async def create_delivery(
        self,
        *,
        daily_limit: int | None = None,
        **values: Any,
    ) -> tuple[EmailDeliveryModel, bool]:
        await self.ensure_tables()
        connector_id = str(values['connector_id'])
        delivery_lock = _delivery_locks.setdefault(connector_id, asyncio.Lock())
        async with delivery_lock:
            async with get_async_db_context() as db:
                existing = (
                    await db.execute(
                        select(InteractEmailDelivery).where(
                            InteractEmailDelivery.idempotency_key == values['idempotency_key']
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    return EmailDeliveryModel.model_validate(existing), False
                if daily_limit is not None:
                    await db.execute(
                        select(InteractEmailConnector.id)
                        .where(InteractEmailConnector.id == connector_id)
                        .with_for_update()
                    )
                    delivery_count = (
                        await db.execute(
                            select(func.count(InteractEmailDelivery.id)).where(
                                InteractEmailDelivery.connector_id == connector_id,
                                InteractEmailDelivery.created_at >= _daily_start_ns(),
                                InteractEmailDelivery.status.not_in(['rejected', 'cancelled']),
                            )
                        )
                    ).scalar_one()
                    if delivery_count >= daily_limit:
                        raise EmailDailyLimitExceeded
                now = int(time.time_ns())
                row = InteractEmailDelivery(id=str(uuid4()), created_at=now, updated_at=now, **values)
                db.add(row)
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    existing = (
                        await db.execute(
                            select(InteractEmailDelivery).where(
                                InteractEmailDelivery.idempotency_key == values['idempotency_key']
                            )
                        )
                    ).scalar_one()
                    return EmailDeliveryModel.model_validate(existing), False
                await db.refresh(row)
                return EmailDeliveryModel.model_validate(row), True

    async def update_delivery(self, delivery_id: str, **values: Any) -> Optional[EmailDeliveryModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractEmailDelivery, delivery_id)
            if not row:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = int(time.time_ns())
            await db.commit()
            await db.refresh(row)
            return EmailDeliveryModel.model_validate(row)

    async def list_deliveries(
        self,
        company_user_id: str,
        limit: int = 50,
        workflow_ids: set[str] | None = None,
    ) -> list[EmailDeliveryModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            statement = select(InteractEmailDelivery).where(
                InteractEmailDelivery.company_user_id == company_user_id
            )
            if workflow_ids:
                statement = statement.where(
                    InteractEmailDelivery.workflow_id.in_(workflow_ids)
                )
            rows = (
                (
                    await db.execute(
                        statement
                        .order_by(InteractEmailDelivery.created_at.desc())
                        .limit(max(1, min(limit, 500)))
                    )
                )
                .scalars()
                .all()
            )
            return [EmailDeliveryModel.model_validate(row) for row in rows]

    async def get_delivery_by_provider_message_id(self, provider_message_id: str) -> Optional[InteractEmailDelivery]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            return (
                await db.execute(
                    select(InteractEmailDelivery).where(
                        InteractEmailDelivery.provider_message_id == provider_message_id
                    )
                )
            ).scalar_one_or_none()

    async def get_delivery_by_idempotency_key(self, idempotency_key: str) -> Optional[EmailDeliveryModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(InteractEmailDelivery).where(InteractEmailDelivery.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            return EmailDeliveryModel.model_validate(row) if row else None

    async def insert_event(
        self,
        company_user_id: str,
        delivery_id: str | None,
        provider_event_id: str,
        provider_message_id: str | None,
        event_type: str,
        occurred_at: int,
        payload: dict[str, Any],
    ) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            existing = (
                await db.execute(
                    select(InteractEmailEvent.id).where(InteractEmailEvent.provider_event_id == provider_event_id)
                )
            ).scalar_one_or_none()
            if existing:
                return False
            db.add(
                InteractEmailEvent(
                    id=str(uuid4()),
                    company_user_id=company_user_id,
                    delivery_id=delivery_id,
                    provider_event_id=provider_event_id,
                    provider_message_id=provider_message_id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    payload=payload,
                    created_at=int(time.time_ns()),
                )
            )
            try:
                await db.commit()
                return True
            except IntegrityError:
                await db.rollback()
                return False

    async def count_daily_deliveries(self, connector_id: str) -> int:
        await self.ensure_tables()
        start = _daily_start_ns()
        async with get_async_db_context() as db:
            rows = (
                await db.execute(
                    select(InteractEmailDelivery.id).where(
                        InteractEmailDelivery.connector_id == connector_id,
                        InteractEmailDelivery.created_at >= start,
                        InteractEmailDelivery.status.not_in(['rejected', 'cancelled']),
                    )
                )
            ).all()
            return len(rows)

    async def save_checkpoint(
        self,
        workflow_run_id: str,
        company_user_id: str,
        workflow_id: str,
        node_id: str,
        wait_type: str,
        state: dict[str, Any],
        prompt: dict[str, Any],
        payload_hash: str | None,
        ttl_seconds: int = 1800,
    ) -> dict[str, Any]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(WorkflowCheckpoint)
                    .where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = int(time.time_ns())
            if not row:
                row = WorkflowCheckpoint(
                    id=str(uuid4()),
                    workflow_run_id=workflow_run_id,
                    company_user_id=company_user_id,
                    workflow_id=workflow_id,
                    created_at=now,
                )
                db.add(row)
            row.node_id = node_id
            row.wait_type = wait_type
            row.status = 'waiting'
            row.state_encrypted = _encrypt_json(state)
            row.prompt = prompt
            row.payload_hash = payload_hash
            row.expires_at = now + ttl_seconds * 1_000_000_000
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return {'id': row.id, 'revision': row.revision}

    async def get_checkpoint(self, workflow_run_id: str) -> Optional[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
                )
            ).scalar_one_or_none()
            if not row:
                return None
            return {
                'id': row.id,
                'workflow_run_id': row.workflow_run_id,
                'company_user_id': row.company_user_id,
                'workflow_id': row.workflow_id,
                'node_id': row.node_id,
                'wait_type': row.wait_type,
                'status': row.status,
                'state': _decrypt_json(row.state_encrypted),
                'prompt': row.prompt,
                'payload_hash': row.payload_hash,
                'revision': row.revision,
                'expires_at': row.expires_at,
            }

    async def consume_checkpoint(
        self,
        workflow_run_id: str,
        company_user_id: str,
        actor_id: str,
        decision: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> Optional[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(WorkflowCheckpoint)
                    .where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = int(time.time_ns())
            if (
                not row
                or row.company_user_id != company_user_id
                or row.status != 'waiting'
                or row.revision != expected_revision
                or row.expires_at <= now
            ):
                return None
            row.status = 'consumed'
            row.consumed_at = now
            row.updated_at = now
            row.revision += 1
            if row.wait_type == 'approval':
                db.add(
                    WorkflowApproval(
                        id=str(uuid4()),
                        workflow_run_id=workflow_run_id,
                        checkpoint_id=row.id,
                        company_user_id=company_user_id,
                        node_id=row.node_id,
                        payload_hash=row.payload_hash or '',
                        decision=decision,
                        decided_by=actor_id,
                        reason=reason,
                        decided_at=now,
                    )
                )
            await db.commit()
            return {
                'node_id': row.node_id,
                'wait_type': row.wait_type,
                'state': _decrypt_json(row.state_encrypted),
                'payload_hash': row.payload_hash,
            }

    def decrypt_connector_api_key(self, connector: InteractEmailConnector) -> str | None:
        return _decrypt(connector.api_key_encrypted)

    def decrypt_webhook_secret(self, connector: InteractEmailConnector) -> str | None:
        return _decrypt(connector.webhook_secret_encrypted)


def _encrypt_json(value: dict[str, Any]) -> str:
    return encrypt_data(value)


def _decrypt_json(value: str) -> dict[str, Any]:
    decoded = decrypt_data(value)
    return decoded if isinstance(decoded, dict) else {}


InteractEmail = InteractEmailTable()
