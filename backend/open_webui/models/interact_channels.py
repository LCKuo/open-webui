import asyncio
import hashlib
import json
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    and_,
    delete,
    func,
    inspect,
    or_,
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
_workflow_session_lock = asyncio.Lock()
_line_identity_lock = asyncio.Lock()
LINE_IDENTITY_LINK_RETENTION_SECONDS = 24 * 60 * 60


class InteractChannel(Base):
    __tablename__ = 'interact_channel'

    id = Column(Text, primary_key=True)
    company_email = Column(Text, nullable=False, index=True)
    company_user_id = Column(Text, nullable=True, index=True)
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
    quota_exempt = Column(Boolean, nullable=False, default=False)

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


class InteractChannelRichMenu(Base):
    __tablename__ = 'interact_channel_rich_menu'

    channel_id = Column(Text, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    status = Column(Text, nullable=False, default='disabled')
    rich_menu_id = Column(Text, nullable=True)
    content_hash = Column(Text, nullable=True)
    workflow_ids_json = Column(Text, nullable=False, default='[]')
    liff_uri = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    synced_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)
    revision = Column(Integer, nullable=False, default=0)
    sync_token = Column(Text, nullable=True)
    sync_expires_at = Column(BigInteger, nullable=True)

    __table_args__ = (Index('ix_interact_channel_rich_menu_status', 'status', 'updated_at'),)


class InteractChannelWorkflowSession(Base):
    __tablename__ = 'interact_channel_workflow_session'

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    external_user_id = Column(Text, nullable=False)
    conversation_id = Column(Text, nullable=False)
    workflow_id = Column(Text, nullable=False)
    workflow_version_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='collecting')
    input_json = Column(Text, nullable=False, default='{}')
    current_field = Column(Text, nullable=True)
    expires_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    revision = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index('ix_interact_workflow_session_channel_user', 'channel_id', 'external_user_id'),
        Index('ix_interact_workflow_session_expiry', 'expires_at'),
    )


class InteractLineIdentityLink(Base):
    __tablename__ = 'interact_line_identity_link'

    state_hash = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    external_user_hash = Column(Text, nullable=False)
    external_user_id = Column(Text, nullable=False)
    link_token_hash = Column(Text, nullable=False)
    nonce_hash = Column(Text, nullable=True, unique=True)
    company_user_id = Column(Text, nullable=True)
    company_email = Column(Text, nullable=True)
    company_member_id = Column(Text, nullable=True)
    member_email = Column(Text, nullable=True)
    member_role = Column(Text, nullable=True)
    member_status = Column(Text, nullable=True)
    group_ids_json = Column(Text, nullable=False, default='[]')
    status = Column(Text, nullable=False, default='issued')
    expires_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    consumed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index('ix_interact_line_identity_link_channel', 'channel_id', 'status', 'expires_at'),
        Index('ix_interact_line_identity_link_user', 'channel_id', 'external_user_hash'),
    )


class InteractLineIdentityBinding(Base):
    __tablename__ = 'interact_line_identity_binding'

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    external_user_hash = Column(Text, nullable=False)
    external_user_id = Column(Text, nullable=False)
    company_user_id = Column(Text, nullable=False)
    company_email = Column(Text, nullable=False)
    company_member_id = Column(Text, nullable=True)
    member_email = Column(Text, nullable=False)
    member_role = Column(Text, nullable=False)
    member_status = Column(Text, nullable=False, default='active')
    group_ids_json = Column(Text, nullable=False, default='[]')
    role_verified_at = Column(BigInteger, nullable=False)
    linked_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('channel_id', 'external_user_hash', name='uq_interact_line_identity_channel_user'),
        Index('ix_interact_line_identity_company_member', 'company_user_id', 'company_member_id'),
        Index('ix_interact_line_identity_email', 'company_user_id', 'member_email'),
    )


class InteractChannelRichMenuVariant(Base):
    __tablename__ = 'interact_channel_rich_menu_variant'

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    audience = Column(Text, nullable=False)
    tab = Column(Text, nullable=False)
    rich_menu_id = Column(Text, nullable=True)
    alias_id = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default='pending')
    last_error = Column(Text, nullable=True)
    synced_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('channel_id', 'audience', 'tab', name='uq_interact_rich_menu_variant'),
        UniqueConstraint('channel_id', 'alias_id', name='uq_interact_rich_menu_variant_alias'),
        Index('ix_interact_rich_menu_variant_channel', 'channel_id', 'audience'),
    )


class InteractChannelModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_email: str
    company_user_id: str | None = None
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


class InteractChannelRichMenuModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    enabled: bool
    status: str
    rich_menu_id: str | None = None
    content_hash: str | None = None
    workflow_ids: list[str] = Field(default_factory=list)
    liff_uri: str | None = None
    last_error: str | None = None
    synced_at: int | None = None
    updated_at: int
    revision: int = 0


class InteractChannelWorkflowSessionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    external_user_id: str
    conversation_id: str
    workflow_id: str
    workflow_version_id: str
    status: str
    input: dict[str, Any] = Field(default_factory=dict)
    current_field: str | None = None
    expires_at: int
    created_at: int
    updated_at: int
    revision: int = 0


class InteractLineIdentityBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    company_user_id: str
    company_email: str
    company_member_id: str | None = None
    member_email: str
    member_role: str
    member_status: str
    group_ids: list[str] = Field(default_factory=list)
    role_verified_at: int
    linked_at: int
    updated_at: int
    line_user_ref: str
    external_user_id: str | None = None


class InteractChannelRichMenuVariantModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    audience: str
    tab: str
    rich_menu_id: str | None = None
    alias_id: str
    content_hash: str | None = None
    status: str
    last_error: str | None = None
    synced_at: int | None = None
    updated_at: int


def _encrypt_secret(value: str | None) -> str | None:
    return encrypt_data({'value': value}) if value else None


def _decrypt_secret(value: str | None) -> str | None:
    return decrypt_data(value).get('value') if value else None


def line_identity_hash(channel_id: str, external_user_id: str) -> str:
    return hashlib.sha256(f'{channel_id}:{external_user_id}'.encode()).hexdigest()


def opaque_value_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
                            InteractChannelRichMenu.__table__,
                            InteractChannelWorkflowSession.__table__,
                            InteractLineIdentityLink.__table__,
                            InteractLineIdentityBinding.__table__,
                            InteractChannelRichMenuVariant.__table__,
                        ],
                        checkfirst=True,
                    )
                )
                await connection.run_sync(self._ensure_legacy_columns)
            _tables_ready = True

    @staticmethod
    def _ensure_legacy_columns(sync_connection) -> None:
        additions_by_table = {
            InteractChannel.__table__: {
                'company_user_id': 'TEXT',
                'user_rate_limit_per_minute': 'INTEGER NOT NULL DEFAULT 5',
                'max_concurrent_jobs': 'INTEGER NOT NULL DEFAULT 8',
            },
            InteractChannelEvent.__table__: {
                'quota_exempt': 'BOOLEAN NOT NULL DEFAULT FALSE',
            },
            InteractChannelWorkflowSession.__table__: {
                'revision': 'INTEGER NOT NULL DEFAULT 0',
            },
            InteractChannelRichMenu.__table__: {
                'revision': 'INTEGER NOT NULL DEFAULT 0',
                'sync_token': 'TEXT',
                'sync_expires_at': 'BIGINT',
            },
        }
        for table, additions in additions_by_table.items():
            columns = {
                column['name'] for column in inspect(sync_connection).get_columns(table.name, schema=table.schema)
            }
            formatted_table = sync_connection.dialect.identifier_preparer.format_table(table)
            for name, definition in additions.items():
                if name not in columns:
                    sync_connection.execute(text(f'ALTER TABLE {formatted_table} ADD COLUMN {name} {definition}'))

    def _model(self, row: InteractChannel) -> InteractChannelModel:
        model = InteractChannelModel.model_validate(row)
        model.channel_secret = _decrypt_secret(row.channel_secret)
        model.channel_access_token = _decrypt_secret(row.channel_access_token)
        return model

    @staticmethod
    def _rich_menu_model(row: InteractChannelRichMenu) -> InteractChannelRichMenuModel:
        try:
            workflow_ids = json.loads(row.workflow_ids_json or '[]')
        except json.JSONDecodeError:
            workflow_ids = []
        return InteractChannelRichMenuModel(
            channel_id=row.channel_id,
            enabled=bool(row.enabled),
            status=row.status,
            rich_menu_id=row.rich_menu_id,
            content_hash=row.content_hash,
            workflow_ids=[str(item) for item in workflow_ids if str(item).strip()],
            liff_uri=row.liff_uri,
            last_error=row.last_error,
            synced_at=row.synced_at,
            updated_at=row.updated_at,
            revision=int(row.revision or 0),
        )

    @staticmethod
    def _workflow_session_id(
        channel_id: str,
        external_user_id: str,
        conversation_id: str,
    ) -> str:
        key = '\x1f'.join((channel_id, external_user_id, conversation_id))
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _workflow_session_model(
        row: InteractChannelWorkflowSession,
    ) -> InteractChannelWorkflowSessionModel:
        try:
            session_input = json.loads(row.input_json or '{}')
        except json.JSONDecodeError:
            session_input = {}
        return InteractChannelWorkflowSessionModel(
            id=row.id,
            channel_id=row.channel_id,
            external_user_id=row.external_user_id,
            conversation_id=row.conversation_id,
            workflow_id=row.workflow_id,
            workflow_version_id=row.workflow_version_id,
            status=row.status,
            input=session_input if isinstance(session_input, dict) else {},
            current_field=row.current_field,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    @staticmethod
    def _line_identity_model(
        row: InteractLineIdentityBinding,
        *,
        reveal_external_user_id: bool = False,
    ) -> InteractLineIdentityBindingModel:
        try:
            group_ids = json.loads(row.group_ids_json or '[]')
        except json.JSONDecodeError:
            group_ids = []
        return InteractLineIdentityBindingModel(
            id=row.id,
            channel_id=row.channel_id,
            company_user_id=row.company_user_id,
            company_email=row.company_email,
            company_member_id=row.company_member_id,
            member_email=row.member_email,
            member_role=row.member_role,
            member_status=row.member_status,
            group_ids=[str(item) for item in group_ids if str(item).strip()],
            role_verified_at=row.role_verified_at,
            linked_at=row.linked_at,
            updated_at=row.updated_at,
            line_user_ref=f'LINE-{row.external_user_hash[-8:].upper()}',
            external_user_id=(_decrypt_secret(row.external_user_id) if reveal_external_user_id else None),
        )

    @staticmethod
    def _rich_menu_variant_model(
        row: InteractChannelRichMenuVariant,
    ) -> InteractChannelRichMenuVariantModel:
        return InteractChannelRichMenuVariantModel.model_validate(row)

    async def upsert(self, data: dict[str, Any]) -> InteractChannelModel:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, data['id'])
            if not row:
                row = InteractChannel(id=data['id'])
                db.add(row)

            row.company_email = data['company_email'].strip().lower()
            row.company_user_id = data.get('company_user_id') or row.company_user_id
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
                update(InteractChannelEvent).where(InteractChannelEvent.channel_id == old_id).values(channel_id=new_id)
            )
            jobs = (
                (await db.execute(select(InteractChannelJob).where(InteractChannelJob.channel_id == old_id)))
                .scalars()
                .all()
            )
            for job in jobs:
                job.channel_id = new_id
                job.conversation_key = f'{new_id}:{job.external_user_id}'
            rich_menu = await db.get(InteractChannelRichMenu, old_id)
            if rich_menu:
                rich_menu.channel_id = new_id
            sessions = (
                (
                    await db.execute(
                        select(InteractChannelWorkflowSession).where(
                            InteractChannelWorkflowSession.channel_id == old_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for session in sessions:
                session.channel_id = new_id
                session.id = self._workflow_session_id(
                    new_id,
                    session.external_user_id,
                    session.conversation_id,
                )
            await db.execute(
                update(InteractLineIdentityLink)
                .where(InteractLineIdentityLink.channel_id == old_id)
                .values(channel_id=new_id, status='superseded')
            )
            bindings = (
                (
                    await db.execute(
                        select(InteractLineIdentityBinding).where(InteractLineIdentityBinding.channel_id == old_id)
                    )
                )
                .scalars()
                .all()
            )
            for binding in bindings:
                external_user_id = _decrypt_secret(binding.external_user_id)
                binding.channel_id = new_id
                binding.external_user_hash = line_identity_hash(new_id, external_user_id)
            await db.execute(
                update(InteractChannelRichMenuVariant)
                .where(InteractChannelRichMenuVariant.channel_id == old_id)
                .values(channel_id=new_id)
            )
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
            await db.execute(delete(InteractChannelRichMenu).where(InteractChannelRichMenu.channel_id == channel_id))
            await db.execute(
                delete(InteractChannelWorkflowSession).where(InteractChannelWorkflowSession.channel_id == channel_id)
            )
            await db.execute(delete(InteractLineIdentityLink).where(InteractLineIdentityLink.channel_id == channel_id))
            await db.execute(
                delete(InteractLineIdentityBinding).where(InteractLineIdentityBinding.channel_id == channel_id)
            )
            await db.execute(
                delete(InteractChannelRichMenuVariant).where(InteractChannelRichMenuVariant.channel_id == channel_id)
            )
            await db.delete(row)
            await db.commit()
            return True

    async def get_by_id(self, channel_id: str) -> InteractChannelModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannel, channel_id)
            return self._model(row) if row else None

    async def get_rich_menu(self, channel_id: str) -> InteractChannelRichMenuModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelRichMenu, channel_id)
            return self._rich_menu_model(row) if row else None

    async def create_line_identity_link(
        self,
        *,
        state: str,
        channel_id: str,
        external_user_id: str,
        link_token: str,
        ttl_seconds: int = 600,
    ) -> None:
        await self.ensure_tables()
        now = int(time.time())
        async with _line_identity_lock:
            async with get_async_db_context() as db:
                await db.execute(
                    delete(InteractLineIdentityLink).where(
                        or_(
                            InteractLineIdentityLink.expires_at <= now - LINE_IDENTITY_LINK_RETENTION_SECONDS,
                            and_(
                                InteractLineIdentityLink.status.in_({'consumed', 'superseded'}),
                                InteractLineIdentityLink.updated_at <= now - LINE_IDENTITY_LINK_RETENTION_SECONDS,
                            ),
                        )
                    )
                )
                await db.execute(
                    update(InteractLineIdentityLink)
                    .where(
                        InteractLineIdentityLink.channel_id == channel_id,
                        InteractLineIdentityLink.external_user_hash == line_identity_hash(channel_id, external_user_id),
                        InteractLineIdentityLink.status.in_({'issued', 'prepared'}),
                    )
                    .values(status='superseded', updated_at=now)
                )
                db.add(
                    InteractLineIdentityLink(
                        state_hash=opaque_value_hash(state),
                        channel_id=channel_id,
                        external_user_hash=line_identity_hash(channel_id, external_user_id),
                        external_user_id=_encrypt_secret(external_user_id),
                        link_token_hash=opaque_value_hash(link_token),
                        group_ids_json='[]',
                        status='issued',
                        expires_at=now + max(60, min(int(ttl_seconds), 600)),
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.commit()

    async def get_line_identity_link_preview(
        self,
        *,
        state: str,
        channel_id: str,
    ) -> dict[str, Any] | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractLineIdentityLink, opaque_value_hash(state))
            if (
                not row
                or row.channel_id != channel_id
                or row.status not in {'issued', 'prepared'}
                or row.expires_at <= int(time.time())
            ):
                return None
            return {
                'channelId': row.channel_id,
                'status': row.status,
                'expiresAt': row.expires_at,
            }

    async def prepare_line_identity_nonce(
        self,
        *,
        state: str,
        channel_id: str,
        link_token: str,
        nonce: str,
        company_user_id: str,
        company_email: str,
        company_member_id: str | None,
        member_email: str,
        member_role: str,
        member_status: str,
        group_ids: list[str],
    ) -> bool:
        await self.ensure_tables()
        now = int(time.time())
        async with _line_identity_lock:
            async with get_async_db_context() as db:
                row = await db.scalar(
                    select(InteractLineIdentityLink)
                    .where(InteractLineIdentityLink.state_hash == opaque_value_hash(state))
                    .with_for_update()
                )
                if (
                    not row
                    or row.channel_id != channel_id
                    or row.status != 'issued'
                    or row.expires_at <= now
                    or row.link_token_hash != opaque_value_hash(link_token)
                ):
                    return False
                row.nonce_hash = opaque_value_hash(nonce)
                row.company_user_id = company_user_id
                row.company_email = company_email.strip().lower()
                row.company_member_id = company_member_id
                row.member_email = member_email.strip().lower()
                row.member_role = member_role
                row.member_status = member_status
                row.group_ids_json = json.dumps(sorted(set(group_ids)), ensure_ascii=False)
                row.status = 'prepared'
                row.updated_at = now
                await db.commit()
                return True

    async def consume_line_identity_link(
        self,
        *,
        nonce: str,
        channel_id: str,
        external_user_id: str,
    ) -> InteractLineIdentityBindingModel | None:
        await self.ensure_tables()
        now = int(time.time())
        user_hash = line_identity_hash(channel_id, external_user_id)
        async with _line_identity_lock:
            async with get_async_db_context() as db:
                link = await db.scalar(
                    select(InteractLineIdentityLink)
                    .where(InteractLineIdentityLink.nonce_hash == opaque_value_hash(nonce))
                    .with_for_update()
                )
                if (
                    not link
                    or link.channel_id != channel_id
                    or link.external_user_hash != user_hash
                    or link.status not in {'prepared', 'consumed'}
                    or link.expires_at <= now
                ):
                    return None
                binding = await db.scalar(
                    select(InteractLineIdentityBinding)
                    .where(
                        InteractLineIdentityBinding.channel_id == channel_id,
                        InteractLineIdentityBinding.external_user_hash == user_hash,
                    )
                    .with_for_update()
                )
                if link.status == 'consumed':
                    return (
                        self._line_identity_model(
                            binding,
                            reveal_external_user_id=True,
                        )
                        if binding
                        else None
                    )
                if (
                    not link.company_user_id
                    or not link.company_email
                    or not link.member_email
                    or not link.member_role
                    or not link.member_status
                ):
                    return None
                if not binding:
                    binding = InteractLineIdentityBinding(
                        id=str(uuid4()),
                        channel_id=channel_id,
                        external_user_hash=user_hash,
                        linked_at=now,
                    )
                    db.add(binding)
                binding.external_user_id = link.external_user_id
                binding.company_user_id = link.company_user_id
                binding.company_email = link.company_email
                binding.company_member_id = link.company_member_id
                binding.member_email = link.member_email
                binding.member_role = link.member_role
                binding.member_status = link.member_status
                binding.group_ids_json = link.group_ids_json or '[]'
                binding.role_verified_at = now
                binding.updated_at = now
                link.status = 'consumed'
                link.consumed_at = now
                link.updated_at = now
                await db.commit()
                await db.refresh(binding)
                return self._line_identity_model(binding, reveal_external_user_id=True)

    async def get_line_identity_binding(
        self,
        *,
        channel_id: str,
        external_user_id: str,
        reveal_external_user_id: bool = False,
    ) -> InteractLineIdentityBindingModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractLineIdentityBinding).where(
                    InteractLineIdentityBinding.channel_id == channel_id,
                    InteractLineIdentityBinding.external_user_hash == line_identity_hash(channel_id, external_user_id),
                )
            )
            return (
                self._line_identity_model(
                    row,
                    reveal_external_user_id=reveal_external_user_id,
                )
                if row
                else None
            )

    async def list_line_identity_bindings(
        self,
        *,
        channel_id: str | None = None,
        company_user_id: str | None = None,
        company_member_id: str | None = None,
        member_email: str | None = None,
        reveal_external_user_id: bool = False,
    ) -> list[InteractLineIdentityBindingModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            stmt = select(InteractLineIdentityBinding)
            if channel_id:
                stmt = stmt.where(InteractLineIdentityBinding.channel_id == channel_id)
            if company_user_id:
                stmt = stmt.where(InteractLineIdentityBinding.company_user_id == company_user_id)
            if company_member_id:
                stmt = stmt.where(InteractLineIdentityBinding.company_member_id == company_member_id)
            elif member_email:
                stmt = stmt.where(InteractLineIdentityBinding.member_email == member_email.strip().lower())
            rows = (await db.execute(stmt.order_by(InteractLineIdentityBinding.linked_at.desc()))).scalars().all()
            return [
                self._line_identity_model(
                    row,
                    reveal_external_user_id=reveal_external_user_id,
                )
                for row in rows
            ]

    async def update_line_identity_binding(
        self,
        binding_id: str,
        *,
        company_member_id: str | None,
        member_email: str,
        member_role: str,
        member_status: str,
        group_ids: list[str],
    ) -> InteractLineIdentityBindingModel | None:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractLineIdentityBinding, binding_id)
            if not row:
                return None
            row.company_member_id = company_member_id
            row.member_email = member_email.strip().lower()
            row.member_role = member_role
            row.member_status = member_status
            row.group_ids_json = json.dumps(sorted(set(group_ids)), ensure_ascii=False)
            row.role_verified_at = now
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return self._line_identity_model(row, reveal_external_user_id=True)

    async def delete_line_identity_binding(
        self,
        *,
        binding_id: str,
        company_user_id: str,
        channel_id: str | None = None,
    ) -> InteractLineIdentityBindingModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractLineIdentityBinding, binding_id)
            if (
                not row
                or row.company_user_id != company_user_id
                or (channel_id is not None and row.channel_id != channel_id)
            ):
                return None
            model = self._line_identity_model(row, reveal_external_user_id=True)
            await db.delete(row)
            await db.commit()
            return model

    async def upsert_rich_menu_variant(
        self,
        *,
        channel_id: str,
        audience: str,
        tab: str,
        alias_id: str,
        rich_menu_id: str | None,
        content_hash: str | None,
        status: str,
        last_error: str | None = None,
        synced: bool = False,
    ) -> InteractChannelRichMenuVariantModel:
        await self.ensure_tables()
        now = int(time.time())
        variant_id = hashlib.sha256(f'{channel_id}:{audience}:{tab}'.encode()).hexdigest()
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelRichMenuVariant, variant_id)
            if not row:
                row = InteractChannelRichMenuVariant(
                    id=variant_id,
                    channel_id=channel_id,
                    audience=audience,
                    tab=tab,
                    alias_id=alias_id,
                    updated_at=now,
                )
                db.add(row)
            row.alias_id = alias_id
            row.rich_menu_id = rich_menu_id
            row.content_hash = content_hash
            row.status = status
            row.last_error = last_error[:1000] if last_error else None
            row.updated_at = now
            if synced:
                row.synced_at = now
            await db.commit()
            await db.refresh(row)
            return self._rich_menu_variant_model(row)

    async def commit_rich_menu_set(
        self,
        *,
        channel_id: str,
        sync_token: str,
        expected_revision: int,
        rich_menu_id: str,
        content_hash: str,
        workflow_ids: list[str],
        variants: list[dict[str, Any]],
    ) -> InteractChannelRichMenuModel | None:
        """Atomically publish the overall state and every role-menu variant."""
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            state = await db.scalar(
                select(InteractChannelRichMenu)
                .where(InteractChannelRichMenu.channel_id == channel_id)
                .with_for_update()
            )
            if not state or state.sync_token != sync_token or int(state.revision or 0) != int(expected_revision):
                return None
            variant_ids: list[str] = []
            for item in variants:
                audience = str(item['audience'])
                tab = str(item['tab'])
                variant_id = hashlib.sha256(f'{channel_id}:{audience}:{tab}'.encode()).hexdigest()
                variant_ids.append(variant_id)
                row = await db.get(InteractChannelRichMenuVariant, variant_id)
                if not row:
                    row = InteractChannelRichMenuVariant(
                        id=variant_id,
                        channel_id=channel_id,
                        audience=audience,
                        tab=tab,
                        updated_at=now,
                    )
                    db.add(row)
                row.alias_id = str(item['alias_id'])
                row.rich_menu_id = str(item['rich_menu_id'])
                row.content_hash = str(item['content_hash'])
                row.status = 'active'
                row.last_error = None
                row.synced_at = now
                row.updated_at = now
            await db.execute(
                delete(InteractChannelRichMenuVariant).where(
                    InteractChannelRichMenuVariant.channel_id == channel_id,
                    InteractChannelRichMenuVariant.id.not_in(variant_ids),
                )
            )
            state.status = 'active'
            state.rich_menu_id = rich_menu_id
            state.content_hash = content_hash
            state.workflow_ids_json = json.dumps(workflow_ids, ensure_ascii=False)
            state.last_error = None
            state.synced_at = now
            state.sync_token = None
            state.sync_expires_at = None
            state.updated_at = now
            await db.commit()
            await db.refresh(state)
            return self._rich_menu_model(state)

    async def list_rich_menu_variants(
        self,
        channel_id: str,
    ) -> list[InteractChannelRichMenuVariantModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractChannelRichMenuVariant)
                        .where(InteractChannelRichMenuVariant.channel_id == channel_id)
                        .order_by(
                            InteractChannelRichMenuVariant.audience,
                            InteractChannelRichMenuVariant.tab,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [self._rich_menu_variant_model(row) for row in rows]

    async def delete_rich_menu_variants(self, channel_id: str) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            await db.execute(
                delete(InteractChannelRichMenuVariant).where(InteractChannelRichMenuVariant.channel_id == channel_id)
            )
            await db.commit()

    async def start_workflow_session(
        self,
        *,
        channel_id: str,
        external_user_id: str,
        conversation_id: str,
        workflow_id: str,
        workflow_version_id: str,
        session_input: dict[str, Any] | None = None,
        current_field: str | None = None,
        ttl_seconds: int = 1800,
    ) -> InteractChannelWorkflowSessionModel:
        await self.ensure_tables()
        now = int(time.time())
        session_id = self._workflow_session_id(channel_id, external_user_id, conversation_id)
        async with _workflow_session_lock:
            for attempt in range(2):
                async with get_async_db_context() as db:
                    row = await db.scalar(
                        select(InteractChannelWorkflowSession)
                        .where(InteractChannelWorkflowSession.id == session_id)
                        .with_for_update()
                    )
                    if not row:
                        row = InteractChannelWorkflowSession(
                            id=session_id,
                            created_at=now,
                            revision=0,
                        )
                        db.add(row)
                    else:
                        row.revision = int(row.revision or 0) + 1
                    row.channel_id = channel_id
                    row.external_user_id = external_user_id
                    row.conversation_id = conversation_id
                    row.workflow_id = workflow_id
                    row.workflow_version_id = workflow_version_id
                    row.status = 'collecting'
                    row.input_json = json.dumps(session_input or {}, ensure_ascii=False)
                    row.current_field = current_field
                    row.expires_at = now + max(300, min(int(ttl_seconds), 86400))
                    row.updated_at = now
                    try:
                        await db.commit()
                    except IntegrityError:
                        await db.rollback()
                        if attempt == 0:
                            continue
                        raise
                    await db.refresh(row)
                    return self._workflow_session_model(row)
        raise RuntimeError('Workflow session could not be created.')

    async def get_workflow_session(
        self,
        *,
        channel_id: str,
        external_user_id: str,
        conversation_id: str,
    ) -> InteractChannelWorkflowSessionModel | None:
        await self.ensure_tables()
        session_id = self._workflow_session_id(channel_id, external_user_id, conversation_id)
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelWorkflowSession, session_id)
            if not row:
                return None
            if row.expires_at <= int(time.time()):
                await db.delete(row)
                await db.commit()
                return None
            return self._workflow_session_model(row)

    async def update_workflow_session(
        self,
        session_id: str,
        *,
        status: str,
        session_input: dict[str, Any],
        current_field: str | None = None,
        ttl_seconds: int = 1800,
        expected_revision: int | None = None,
    ) -> InteractChannelWorkflowSessionModel | None:
        await self.ensure_tables()
        now = int(time.time())
        async with _workflow_session_lock:
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(InteractChannelWorkflowSession)
                    .where(InteractChannelWorkflowSession.id == session_id)
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if not row or (expected_revision is not None and int(row.revision or 0) != int(expected_revision)):
                    return None
                row.status = status
                row.input_json = json.dumps(session_input, ensure_ascii=False)
                row.current_field = current_field
                row.expires_at = now + max(300, min(int(ttl_seconds), 86400))
                row.updated_at = now
                row.revision = int(row.revision or 0) + 1
                await db.commit()
                await db.refresh(row)
                return self._workflow_session_model(row)

    async def delete_workflow_session(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> bool:
        await self.ensure_tables()
        async with _workflow_session_lock:
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(InteractChannelWorkflowSession)
                    .where(InteractChannelWorkflowSession.id == session_id)
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if not row or (expected_revision is not None and int(row.revision or 0) != int(expected_revision)):
                    return False
                await db.delete(row)
                await db.commit()
                return True

    async def cleanup_workflow_sessions(self) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            await db.execute(
                delete(InteractChannelWorkflowSession).where(
                    InteractChannelWorkflowSession.expires_at <= int(time.time())
                )
            )
            await db.commit()

    async def configure_rich_menu(
        self,
        channel_id: str,
        *,
        enabled: bool,
        liff_uri: str | None = None,
    ) -> InteractChannelRichMenuModel:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelRichMenu, channel_id)
            created = row is None
            if not row:
                row = InteractChannelRichMenu(channel_id=channel_id)
                db.add(row)
            changed = bool(row.enabled) != bool(enabled) or row.liff_uri != liff_uri
            row.enabled = bool(enabled)
            row.liff_uri = liff_uri
            if created or changed or row.status in {'error', 'disabled'}:
                row.status = 'pending'
                row.last_error = None
            if created or changed:
                row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return self._rich_menu_model(row)

    async def mark_rich_menu_pending(self, channel_id: str) -> bool:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractChannelRichMenu, channel_id)
            if not row or not row.enabled:
                return False
            row.status = 'pending'
            row.last_error = None
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            await db.commit()
            return True

    async def claim_rich_menu_sync(
        self,
        channel_id: str,
        expected_revision: int,
        lease_seconds: int = 600,
    ) -> str | None:
        """Acquire a cross-process Rich Menu synchronization lease."""
        now = int(time.time())
        token = str(uuid4())
        async with get_async_db_context() as db:
            result = await db.execute(
                update(InteractChannelRichMenu)
                .where(
                    InteractChannelRichMenu.channel_id == channel_id,
                    InteractChannelRichMenu.revision == int(expected_revision),
                    or_(
                        InteractChannelRichMenu.sync_token.is_(None),
                        InteractChannelRichMenu.sync_expires_at.is_(None),
                        InteractChannelRichMenu.sync_expires_at <= now,
                    ),
                )
                .values(
                    status='syncing',
                    sync_token=token,
                    sync_expires_at=now + max(60, lease_seconds),
                    last_error=None,
                    updated_at=now,
                )
            )
            await db.commit()
            return token if result.rowcount else None

    async def release_rich_menu_sync(self, channel_id: str, sync_token: str) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            result = await db.execute(
                update(InteractChannelRichMenu)
                .where(
                    InteractChannelRichMenu.channel_id == channel_id,
                    InteractChannelRichMenu.sync_token == sync_token,
                )
                .values(
                    status='pending',
                    sync_token=None,
                    sync_expires_at=None,
                    updated_at=now,
                )
            )
            await db.commit()
            return bool(result.rowcount)

    async def update_rich_menu_status(
        self,
        channel_id: str,
        *,
        status: str,
        rich_menu_id: str | None = None,
        content_hash: str | None = None,
        workflow_ids: list[str] | None = None,
        last_error: str | None = None,
        synced: bool = False,
        sync_token: str | None = None,
        expected_revision: int | None = None,
    ) -> InteractChannelRichMenuModel | None:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractChannelRichMenu)
                .where(InteractChannelRichMenu.channel_id == channel_id)
                .with_for_update()
            )
            if not row:
                return None
            if sync_token is not None and (
                row.sync_token != sync_token
                or (expected_revision is not None and int(row.revision or 0) != int(expected_revision))
            ):
                return None
            row.status = status
            row.rich_menu_id = rich_menu_id
            row.content_hash = content_hash
            if workflow_ids is not None:
                row.workflow_ids_json = json.dumps(workflow_ids, ensure_ascii=False)
            row.last_error = last_error[:1000] if last_error else None
            if synced:
                row.synced_at = now
            if sync_token is not None:
                row.sync_token = None
                row.sync_expires_at = None
            row.updated_at = now
            model = self._rich_menu_model(row)
            await db.commit()
            return model

    async def list_rich_menu_channel_ids(
        self,
        *,
        statuses: set[str] | None = None,
    ) -> list[str]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            stmt = select(InteractChannelRichMenu.channel_id)
            if statuses:
                stmt = stmt.where(InteractChannelRichMenu.status.in_(statuses))
            return list((await db.execute(stmt)).scalars().all())

    async def list_line_channels_for_company(
        self,
        *,
        company_user_id: str | None = None,
        company_email: str | None = None,
        channel_ids: list[str] | None = None,
    ) -> list[InteractChannelModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            stmt = select(InteractChannel).where(InteractChannel.channel_type == 'line')
            if channel_ids:
                stmt = stmt.where(InteractChannel.id.in_(channel_ids))
            if company_user_id:
                stmt = stmt.where(InteractChannel.company_user_id == company_user_id)
            elif company_email:
                stmt = stmt.where(InteractChannel.company_email == company_email.strip().lower())
            else:
                return []
            return [self._model(row) for row in (await db.execute(stmt)).scalars().all()]

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
        quota_exempt: bool = False,
    ) -> InteractEventClaim:
        async with _event_claim_lock:
            return await self._claim_event(
                channel,
                platform_event_id,
                external_user_id,
                reserved_tokens,
                day_start,
                quota_exempt,
            )

    async def _claim_event(
        self,
        channel: InteractChannelModel,
        platform_event_id: str,
        external_user_id: str,
        reserved_tokens: int,
        day_start: int,
        quota_exempt: bool = False,
    ) -> InteractEventClaim:
        await self.ensure_tables()
        now = int(time.time())
        event_id = str(uuid4())
        async with get_async_db_context() as db:
            locked_channel_result = await db.execute(
                select(InteractChannel).where(InteractChannel.id == channel.id).with_for_update()
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
                reserved_tokens=0 if quota_exempt else reserved_tokens,
                received_at=now,
                quota_exempt=quota_exempt,
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
                    InteractChannelEvent.quota_exempt.is_(False),
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
                    InteractChannelEvent.quota_exempt.is_(False),
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
                    InteractChannelEvent.quota_exempt.is_(False),
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
                    InteractChannelEvent.quota_exempt.is_(False),
                    InteractChannelEvent.status.in_(
                        ['received', 'processing', 'ready', 'completed', 'delivery-failed']
                    ),
                )
            )
            reason = (
                None
                if quota_exempt
                else (
                    'rpm'
                    if int(rpm or 0) > locked_channel.rate_limit_per_minute
                    else 'user-rpm'
                    if int(user_rpm or 0) > locked_channel.user_rate_limit_per_minute
                    else 'daily-user'
                    if int(daily_user or 0) > locked_channel.daily_user_limit
                    else 'daily-token'
                    if int(daily_tokens or 0) > locked_channel.daily_bot_token_limit
                    else None
                )
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

    async def promote_event_quota(
        self,
        event_id: str,
        channel: InteractChannelModel,
        external_user_id: str,
        reserved_tokens: int,
        day_start: int,
    ) -> InteractEventClaim:
        """Atomically turn a guided interaction into a quota-counted execution."""
        async with _event_claim_lock:
            await self.ensure_tables()
            now = int(time.time())
            async with get_async_db_context() as db:
                channel_row = await db.scalar(
                    select(InteractChannel).where(InteractChannel.id == channel.id).with_for_update()
                )
                event = await db.scalar(
                    select(InteractChannelEvent).where(InteractChannelEvent.id == event_id).with_for_update()
                )
                if not channel_row or not event:
                    return InteractEventClaim(
                        allowed=False,
                        duplicate=False,
                        event_id=event_id,
                        reason='channel-deleted',
                    )
                if not channel_row.enabled:
                    return InteractEventClaim(
                        allowed=False,
                        duplicate=False,
                        event_id=event_id,
                        reason='channel-disabled',
                    )
                if not event.quota_exempt:
                    return InteractEventClaim(
                        allowed=event.status != 'rate-limited',
                        duplicate=False,
                        event_id=event_id,
                        reason=event.error if event.status == 'rate-limited' else None,
                    )

                event.quota_exempt = False
                event.reserved_tokens = max(0, int(reserved_tokens))
                rpm = await db.scalar(
                    select(func.count())
                    .select_from(InteractChannelEvent)
                    .where(
                        InteractChannelEvent.channel_id == channel.id,
                        InteractChannelEvent.received_at >= now - 60,
                        InteractChannelEvent.quota_exempt.is_(False),
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
                        InteractChannelEvent.quota_exempt.is_(False),
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
                        InteractChannelEvent.quota_exempt.is_(False),
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
                        InteractChannelEvent.quota_exempt.is_(False),
                        InteractChannelEvent.status.in_(
                            ['received', 'processing', 'ready', 'completed', 'delivery-failed']
                        ),
                    )
                )
                reason = (
                    'rpm'
                    if int(rpm or 0) > channel_row.rate_limit_per_minute
                    else 'user-rpm'
                    if int(user_rpm or 0) > channel_row.user_rate_limit_per_minute
                    else 'daily-user'
                    if int(daily_user or 0) > channel_row.daily_user_limit
                    else 'daily-token'
                    if int(daily_tokens or 0) > channel_row.daily_bot_token_limit
                    else None
                )
                if reason:
                    event.status = 'rate-limited'
                    event.reserved_tokens = 0
                    event.error = reason
                    event.completed_at = now
                else:
                    event.status = 'processing'
                    event.error = None
                await db.commit()
                return InteractEventClaim(
                    allowed=reason is None,
                    duplicate=False,
                    event_id=event_id,
                    reason=reason,
                )

    async def release_event_quota(self, event_id: str) -> bool:
        """Undo a guided execution reservation when no job was created."""
        async with _event_claim_lock:
            await self.ensure_tables()
            async with get_async_db_context() as db:
                event = await db.scalar(
                    select(InteractChannelEvent).where(InteractChannelEvent.id == event_id).with_for_update()
                )
                if not event or event.status not in {'received', 'processing'}:
                    return False
                event.quota_exempt = True
                event.reserved_tokens = 0
                event.status = 'processing'
                event.error = None
                event.completed_at = None
                await db.commit()
                return True

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
            candidate_ids = (
                (
                    await db.execute(
                        select(InteractChannelJob.id)
                        .where(
                            InteractChannelJob.status.in_(['queued', 'retry']),
                            InteractChannelJob.available_at <= now,
                            InteractChannelJob.created_at == earliest_for_conversation,
                        )
                        .order_by(InteractChannelJob.created_at.asc(), InteractChannelJob.id.asc())
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )

            for candidate_id in candidate_ids:
                job = await db.scalar(
                    select(InteractChannelJob).where(InteractChannelJob.id == candidate_id).with_for_update()
                )
                if not job or job.status not in {'queued', 'retry'} or job.available_at > now:
                    continue
                channel = await db.scalar(
                    select(InteractChannel).where(InteractChannel.id == job.channel_id).with_for_update()
                )
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

    async def save_job_result(
        self,
        job_id: str,
        worker_id: str,
        result: dict[str, Any],
        lease_seconds: int = 300,
    ) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractChannelJob)
                .where(
                    InteractChannelJob.id == job_id,
                    InteractChannelJob.lease_owner == worker_id,
                    InteractChannelJob.status == 'processing',
                )
                .with_for_update()
            )
            if not row:
                return False
            row.result_json = json.dumps(result, ensure_ascii=False)
            row.status = 'delivery'
            row.lease_expires_at = now + lease_seconds
            row.updated_at = now
            await db.commit()
            return True

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

    async def complete_job(self, job_id: str, worker_id: str) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractChannelJob)
                .where(
                    InteractChannelJob.id == job_id,
                    InteractChannelJob.lease_owner == worker_id,
                    InteractChannelJob.status.in_(['processing', 'delivery', 'dead']),
                )
                .with_for_update()
            )
            if not row:
                return False
            row.status = 'completed'
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = now
            event = await db.get(InteractChannelEvent, row.event_id)
            if event:
                event.status = 'completed'
                event.completed_at = now
            await db.commit()
            return True

    async def release_job(
        self,
        job_id: str,
        worker_id: str,
        error: str = 'worker-shutdown',
    ) -> bool:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractChannelJob)
                .where(
                    InteractChannelJob.id == job_id,
                    InteractChannelJob.lease_owner == worker_id,
                    InteractChannelJob.status.in_(['processing', 'delivery']),
                )
                .with_for_update()
            )
            if not row:
                return False
            row.status = 'delivery' if row.result_json else 'retry'
            row.available_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error = error[:1000]
            row.updated_at = now
            await db.commit()
            return True

    async def retry_job(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        max_attempts: int = 5,
    ) -> bool | None:
        now = int(time.time())
        async with get_async_db_context() as db:
            row = await db.scalar(
                select(InteractChannelJob)
                .where(
                    InteractChannelJob.id == job_id,
                    InteractChannelJob.lease_owner == worker_id,
                    InteractChannelJob.status.in_(['processing', 'delivery']),
                )
                .with_for_update()
            )
            if not row:
                return None
            exhausted = row.attempts >= max_attempts
            row.status = 'dead' if exhausted else 'retry'
            row.available_at = now if exhausted else now + min(300, 2**row.attempts)
            if not exhausted:
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
                (
                    await db.execute(
                        select(InteractChannel.id).where(InteractChannel.company_email == company_email.strip().lower())
                    )
                )
                .scalars()
                .all()
            )
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
            return {key: counts.get(key, 0) for key in ('queued', 'processing', 'delivery', 'retry', 'dead')}

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
