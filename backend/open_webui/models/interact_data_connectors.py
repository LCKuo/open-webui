from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from open_webui.utils.interact_access import append_channel_for_selected_model
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, delete, select

_tables_ready = False
_tables_lock = asyncio.Lock()


class InteractDataConnector(Base):
    __tablename__ = 'interact_data_connector'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    name = Column(Text, nullable=False)
    connector_type = Column(Text, nullable=False)
    execution_mode = Column(Text, nullable=False, default='webui_local')
    storage_mode = Column(Text, nullable=False, default='metadata_only')
    enabled = Column(Boolean, nullable=False, default=False)
    host = Column(Text, nullable=True)
    port = Column(Integer, nullable=True)
    database_name = Column(Text, nullable=True)
    username = Column(Text, nullable=True)
    password = Column(Text, nullable=True)
    connection_string = Column(Text, nullable=True)
    ssl_mode = Column(Text, nullable=True)
    allowed_schemas = Column(Text, nullable=False, default='[]')
    allowed_tables = Column(Text, nullable=False, default='[]')
    blocked_tables = Column(Text, nullable=False, default='[]')
    allowed_columns = Column(Text, nullable=False, default='{}')
    blocked_columns = Column(Text, nullable=False, default='{}')
    max_rows = Column(Integer, nullable=False, default=100)
    query_timeout_seconds = Column(Integer, nullable=False, default=15)
    allow_write = Column(Boolean, nullable=False, default=False)
    access_mode = Column(Text, nullable=False, default='company_admins')
    allowed_member_ids = Column(Text, nullable=False, default='[]')
    allowed_group_ids = Column(Text, nullable=False, default='[]')
    allowed_model_ids = Column(Text, nullable=False, default='[]')
    allowed_channel_ids = Column(Text, nullable=False, default='[]')
    notes = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_interact_data_connector_company_enabled', 'company_user_id', 'enabled'),
        Index('ix_interact_data_connector_updated_at', 'updated_at'),
    )


class InteractDataConnectorQueryEvent(Base):
    __tablename__ = 'interact_data_connector_query_event'

    id = Column(Text, primary_key=True)
    connector_id = Column(Text, nullable=False, index=True)
    user_id = Column(Text, nullable=True)
    model_id = Column(Text, nullable=True)
    channel_id = Column(Text, nullable=True)
    table_name = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class InteractDataConnectorModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_user_id: str
    name: str
    connector_type: str
    execution_mode: str
    storage_mode: str
    enabled: bool
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    password: str | None = None
    connection_string: str | None = None
    ssl_mode: str | None = None
    allowed_schemas: list[str]
    allowed_tables: list[str]
    blocked_tables: list[str]
    allowed_columns: dict[str, list[str]]
    blocked_columns: dict[str, list[str]]
    max_rows: int
    query_timeout_seconds: int
    allow_write: bool
    access_mode: str
    allowed_member_ids: list[str]
    allowed_group_ids: list[str]
    allowed_model_ids: list[str]
    allowed_channel_ids: list[str]
    notes: str | None = None
    updated_at: int


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _json_dict(value: Any) -> dict[str, list[str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        clean_key = str(key).strip()
        if clean_key:
            result[clean_key] = _json_list(items)
    return result


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _encrypt_secret(value: str | None) -> str | None:
    return encrypt_data({'value': value}) if value else None


def _decrypt_secret(value: str | None) -> str | None:
    try:
        return decrypt_data(value).get('value') if value else None
    except Exception:
        return None


def _local_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _coerce_updated_at(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            from datetime import datetime

            return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
        except Exception:
            pass
    return int(time.time())


class InteractDataConnectorsTable:
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
                            InteractDataConnector.__table__,
                            InteractDataConnectorQueryEvent.__table__,
                        ],
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    def _model(self, row: InteractDataConnector) -> InteractDataConnectorModel:
        return InteractDataConnectorModel(
            id=row.id,
            company_user_id=row.company_user_id,
            name=row.name,
            connector_type=row.connector_type,
            execution_mode=row.execution_mode,
            storage_mode=row.storage_mode,
            enabled=row.enabled,
            host=row.host,
            port=row.port,
            database_name=row.database_name,
            username=row.username,
            password=_decrypt_secret(row.password),
            connection_string=_decrypt_secret(row.connection_string),
            ssl_mode=row.ssl_mode,
            allowed_schemas=_json_list(row.allowed_schemas),
            allowed_tables=_json_list(row.allowed_tables),
            blocked_tables=_json_list(row.blocked_tables),
            allowed_columns=_json_dict(row.allowed_columns),
            blocked_columns=_json_dict(row.blocked_columns),
            max_rows=row.max_rows,
            query_timeout_seconds=row.query_timeout_seconds,
            allow_write=row.allow_write,
            access_mode=row.access_mode,
            allowed_member_ids=_json_list(row.allowed_member_ids),
            allowed_group_ids=_json_list(row.allowed_group_ids),
            allowed_model_ids=_json_list(row.allowed_model_ids),
            allowed_channel_ids=_json_list(row.allowed_channel_ids),
            notes=row.notes,
            updated_at=row.updated_at,
        )

    async def upsert(self, data: dict[str, Any]) -> InteractDataConnectorModel:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractDataConnector, data['id'])
            if not row:
                row = InteractDataConnector(id=data['id'])
                db.add(row)

            row.company_user_id = data['company_user_id']
            row.name = data['name']
            row.connector_type = data['connector_type']
            row.execution_mode = data.get('execution_mode') or 'webui_local'
            row.storage_mode = data.get('storage_mode') or 'metadata_only'
            row.enabled = bool(data.get('enabled'))
            if data.get('storage_mode') == 'metadata_only':
                row.host = row.host if data.get('host') is None else _local_value(data.get('host'))
                row.port = row.port if data.get('port') is None else data.get('port')
                row.database_name = (
                    row.database_name if data.get('database_name') is None else _local_value(data.get('database_name'))
                )
                row.username = row.username if data.get('username') is None else _local_value(data.get('username'))
                row.password = row.password if not data.get('password') else _encrypt_secret(data.get('password'))
                row.connection_string = (
                    row.connection_string
                    if not data.get('connection_string')
                    else _encrypt_secret(data.get('connection_string'))
                )
                row.ssl_mode = row.ssl_mode if data.get('ssl_mode') is None else data.get('ssl_mode')
            else:
                row.host = _local_value(data.get('host'))
                row.port = data.get('port')
                row.database_name = _local_value(data.get('database_name'))
                row.username = _local_value(data.get('username'))
                row.password = _encrypt_secret(data.get('password'))
                row.connection_string = _encrypt_secret(data.get('connection_string'))
                row.ssl_mode = data.get('ssl_mode')
            row.allowed_schemas = _json_dump(_json_list(data.get('allowed_schemas')))
            row.allowed_tables = _json_dump(_json_list(data.get('allowed_tables')))
            row.blocked_tables = _json_dump(_json_list(data.get('blocked_tables')))
            row.allowed_columns = json.dumps(_json_dict(data.get('allowed_columns')), ensure_ascii=False)
            row.blocked_columns = json.dumps(_json_dict(data.get('blocked_columns')), ensure_ascii=False)
            row.max_rows = int(data.get('max_rows') or 100)
            row.query_timeout_seconds = int(data.get('query_timeout_seconds') or 15)
            row.allow_write = bool(data.get('allow_write'))
            row.access_mode = data.get('access_mode') or 'company_admins'
            row.allowed_member_ids = _json_dump(_json_list(data.get('allowed_member_ids')))
            row.allowed_group_ids = _json_dump(_json_list(data.get('allowed_group_ids')))
            row.allowed_model_ids = _json_dump(_json_list(data.get('allowed_model_ids')))
            row.allowed_channel_ids = _json_dump(_json_list(data.get('allowed_channel_ids')))
            row.notes = data.get('notes')
            row.updated_at = _coerce_updated_at(data.get('updated_at'))
            await db.commit()
            await db.refresh(row)
            return self._model(row)

    async def list_all(self) -> list[InteractDataConnectorModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractDataConnector).order_by(
                    InteractDataConnector.updated_at.desc(),
                    InteractDataConnector.name.asc(),
                )
            )
            return [self._model(row) for row in result.scalars().all()]

    async def list_by_company_user_id(self, company_user_id: str) -> list[InteractDataConnectorModel]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractDataConnector)
                .where(InteractDataConnector.company_user_id == company_user_id)
                .order_by(
                    InteractDataConnector.updated_at.desc(),
                    InteractDataConnector.name.asc(),
                )
            )
            return [self._model(row) for row in result.scalars().all()]

    async def grant_channel_for_model(
        self,
        company_user_id: str,
        model_id: str,
        channel_id: str,
    ) -> int:
        """Keep selected-channel grants aligned when a product channel is replaced."""
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractDataConnector).where(
                            InteractDataConnector.company_user_id == company_user_id,
                            InteractDataConnector.access_mode == 'selected_channels',
                        )
                    )
                )
                .scalars()
                .all()
            )
            affected = 0
            for row in rows:
                channel_ids = append_channel_for_selected_model(
                    access_mode=row.access_mode,
                    allowed_model_ids=row.allowed_model_ids,
                    allowed_channel_ids=row.allowed_channel_ids,
                    model_id=model_id,
                    channel_id=channel_id,
                )
                if channel_ids is None:
                    continue
                row.allowed_channel_ids = _json_dump(channel_ids)
                row.updated_at = int(time.time())
                affected += 1
            await db.commit()
            return affected

    async def update_local_credentials(self, connector_id: str, data: dict[str, Any]) -> InteractDataConnectorModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractDataConnector, connector_id)
            if not row:
                return None

            for field in ('host', 'port', 'database_name', 'username', 'ssl_mode'):
                if field in data:
                    setattr(row, field, _local_value(data.get(field)))

            if data.get('password'):
                row.password = _encrypt_secret(data.get('password'))
            if data.get('connection_string'):
                row.connection_string = _encrypt_secret(data.get('connection_string'))

            row.storage_mode = 'metadata_only'
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return self._model(row)

    async def update_local_credentials_for_company(
        self,
        connector_id: str,
        company_user_id: str,
        data: dict[str, Any],
    ) -> InteractDataConnectorModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractDataConnector).where(
                    InteractDataConnector.id == connector_id,
                    InteractDataConnector.company_user_id == company_user_id,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return None

            for field in ('host', 'port', 'database_name', 'username', 'ssl_mode'):
                if field in data:
                    setattr(row, field, _local_value(data.get(field)))

            if data.get('password'):
                row.password = _encrypt_secret(data.get('password'))
            if data.get('connection_string'):
                row.connection_string = _encrypt_secret(data.get('connection_string'))

            row.storage_mode = 'metadata_only'
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return self._model(row)

    async def delete(self, connector_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractDataConnector, connector_id)
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def delete_for_company(self, connector_id: str, company_user_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractDataConnector).where(
                    InteractDataConnector.id == connector_id,
                    InteractDataConnector.company_user_id == company_user_id,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def get_by_id(self, connector_id: str) -> InteractDataConnectorModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractDataConnector, connector_id)
            return self._model(row) if row else None

    async def get_enabled_by_id(self, connector_id: str) -> InteractDataConnectorModel | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            result = await db.execute(
                select(InteractDataConnector).where(
                    InteractDataConnector.id == connector_id,
                    InteractDataConnector.enabled.is_(True),
                )
            )
            row = result.scalar_one_or_none()
            return self._model(row) if row else None

    async def record_query_event(
        self,
        connector_id: str,
        user_id: str | None,
        model_id: str | None,
        channel_id: str | None,
        table_name: str | None,
        status: str,
        row_count: int = 0,
        error: str | None = None,
    ) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            db.add(
                InteractDataConnectorQueryEvent(
                    id=str(uuid4()),
                    connector_id=connector_id,
                    user_id=user_id,
                    model_id=model_id,
                    channel_id=channel_id,
                    table_name=table_name,
                    status=status,
                    row_count=row_count,
                    error=error[:1000] if error else None,
                    created_at=int(time.time()),
                )
            )
            await db.commit()


InteractDataConnectors = InteractDataConnectorsTable()
