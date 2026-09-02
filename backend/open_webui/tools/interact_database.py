from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse

from fastapi import Request

from open_webui.env import DATA_DIR, DATABASE_URL
from open_webui.models.groups import Groups
from open_webui.models.interact_data_connectors import (
    InteractDataConnectorModel,
    InteractDataConnectors,
)

IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
SAFE_FILTER_OPS = {'$eq', '$ne', '$gt', '$gte', '$lt', '$lte', '$contains', '$in', '$nin'}


@dataclass
class QueryContext:
    user_id: str | None
    user_role: str | None
    model_id: str | None
    channel_id: str | None
    channel_source: str | None
    company_user_id: str | None
    company_member_id: str | None
    company_member_role: str | None
    group_ids: list[str]


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _metadata_value(metadata: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return value
    interact_channel = metadata.get('interact_channel')
    if isinstance(interact_channel, dict):
        for key in keys:
            value = interact_channel.get(key)
            if value is not None:
                return value
    return None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        clean = value.strip()
        return clean or None
    if isinstance(value, dict):
        for key in ('id', 'model_id', 'modelId'):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _context(__user__: dict | None, __metadata__: dict | None) -> QueryContext:
    user = __user__ or {}
    metadata = __metadata__ or {}
    interact_company = metadata.get('interact_company') if isinstance(metadata.get('interact_company'), dict) else {}
    return QueryContext(
        user_id=_string_value(user.get('id')),
        user_role=_string_value(user.get('role')),
        model_id=_string_value(_metadata_value(metadata, 'model_id', 'modelId', 'model')),
        channel_id=_string_value(_metadata_value(metadata, 'channelId', 'channel_id')),
        channel_source=_string_value(_metadata_value(metadata, 'source', 'channelSource', 'channel_source')),
        company_user_id=(
            _string_value(user.get('companyUserId'))
            or _string_value(user.get('company_user_id'))
            or _string_value(interact_company.get('companyUserId'))
            or _string_value(interact_company.get('company_user_id'))
            or _string_value(_metadata_value(metadata, 'companyUserId', 'company_user_id'))
        ),
        company_member_id=(
            _string_value(user.get('companyMemberId'))
            or _string_value(user.get('company_member_id'))
            or _string_value(interact_company.get('accessSubjectId'))
            or _string_value(interact_company.get('access_subject_id'))
            or _string_value(interact_company.get('companyMemberId'))
            or _string_value(interact_company.get('company_member_id'))
            or _string_value(
                _metadata_value(
                    metadata,
                    'accessSubjectId',
                    'access_subject_id',
                    'companyMemberId',
                    'company_member_id',
                )
            )
        ),
        company_member_role=(
            _string_value(user.get('companyMemberRole'))
            or _string_value(user.get('company_member_role'))
            or _string_value(interact_company.get('companyMemberRole'))
            or _string_value(interact_company.get('company_member_role'))
            or _string_value(_metadata_value(metadata, 'companyMemberRole', 'company_member_role'))
        ),
        group_ids=[
            str(item) for item in (_metadata_value(metadata, 'groupIds', 'group_ids') or []) if str(item).strip()
        ],
    )


async def _resolve_context_groups(ctx: QueryContext) -> None:
    if ctx.group_ids or not ctx.user_id:
        return
    ctx.group_ids = [group.id for group in await Groups.get_groups_by_member_id(ctx.user_id)]


def _is_admin(ctx: QueryContext) -> bool:
    return ctx.user_role == 'admin'


def _is_company_admin_for_connector(connector: InteractDataConnectorModel, ctx: QueryContext) -> bool:
    if _is_admin(ctx):
        return True
    if not ctx.company_user_id or ctx.company_user_id != connector.company_user_id:
        return False
    if not ctx.company_member_id:
        if _is_channel_request(ctx):
            return False
        return True
    return (ctx.company_member_role or '').lower() in {'owner', 'admin'}


def _is_channel_request(ctx: QueryContext) -> bool:
    return (ctx.channel_source or '').lower() == 'channel' or bool(ctx.channel_id)


def _sqlite_path_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {'sqlite', 'sqlite+aiosqlite', 'sqlite+pysqlite'}:
        return None
    if parsed.path:
        path = unquote(parsed.path)
        if re.match(r'^/[A-Za-z]:[\\/]', path):
            path = path[1:]
        return path
    return None


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(Path(value).resolve()).lower()
    except Exception:
        return str(value).strip().lower()


def _normalize_host(value: str | None) -> str | None:
    return value.strip().lower().strip('[]').rstrip('.') if value else None


def _is_restricted_saas_host(value: str | None) -> bool:
    host = _normalize_host(value)
    if not host:
        return False
    if host in {'localhost', 'host.docker.internal'} or host.endswith('.localhost'):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _db_url_target(url: str | None) -> dict[str, str | None]:
    if not url:
        return {'host': None, 'port': None, 'database': None, 'sqlite_path': None}
    parsed = urlparse(url)
    if parsed.scheme.startswith('sqlite'):
        return {
            'host': None,
            'port': None,
            'database': None,
            'sqlite_path': _normalize_path(_sqlite_path_from_url(url)),
        }
    return {
        'host': _normalize_host(parsed.hostname),
        'port': parsed.port and str(parsed.port),
        'database': unquote(parsed.path.lstrip('/')) or None,
        'sqlite_path': None,
    }


def _connector_target(connector: InteractDataConnectorModel) -> dict[str, str | None]:
    if connector.connection_string:
        target = _db_url_target(connector.connection_string)
        if any(target.values()):
            return target
    if connector.connector_type == 'sqlite':
        return {
            'host': None,
            'port': None,
            'database': None,
            'sqlite_path': _normalize_path(connector.host),
        }
    return {
        'host': _normalize_host(connector.host),
        'port': connector.port and str(connector.port),
        'database': connector.database_name,
        'sqlite_path': None,
    }


def _matches_protected_target(target: dict[str, str | None], protected: dict[str, str | None]) -> bool:
    if protected.get('sqlite_path') and target.get('sqlite_path'):
        return protected['sqlite_path'] == target['sqlite_path']
    if not protected.get('host') or not target.get('host') or protected['host'] != target['host']:
        return False
    if protected.get('port') and target.get('port') and protected['port'] != target['port']:
        return False
    if protected.get('database') and target.get('database') and protected['database'] != target['database']:
        return False
    return True


def _assert_not_internal_connector(connector: InteractDataConnectorModel) -> None:
    if (
        connector.connector_type == 'sqlite'
        and connector.id != 'webui_local'
        and os.environ.get('INTERACT_DATABASE_ALLOW_SYNCED_SQLITE', '').strip().lower()
        not in {'1', 'true', 'yes', 'on'}
    ):
        raise PermissionError(
            'Synced SQLite connectors are disabled by default. Use a network database, '
            'or explicitly enable local SQLite connectors for a private WebUI node.'
        )

    target = _connector_target(connector)
    protected_targets = [_db_url_target(DATABASE_URL)]
    blocked_hosts = {
        _normalize_host(host)
        for host in os.environ.get('INTERACT_DATABASE_BLOCKED_HOSTS', '').split(',')
        if _normalize_host(host)
    }

    if target.get('host') and target['host'] in blocked_hosts:
        raise PermissionError('This database target is blocked by Interact Web Ai policy.')
    if (
        connector.execution_mode == 'saas_webui'
        and target.get('host')
        and _is_restricted_saas_host(target['host'])
        and os.environ.get('INTERACT_DATABASE_ALLOW_SAAS_PRIVATE_HOSTS', '').strip().lower()
        not in {'1', 'true', 'yes', 'on'}
    ):
        raise PermissionError(
            'SaaS data connectors cannot target localhost, link-local, private, or reserved network hosts.'
        )
    if any(_matches_protected_target(target, protected) for protected in protected_targets):
        raise PermissionError('This database target is reserved for Interact Web Ai internal storage.')


def _local_webui_connector() -> InteractDataConnectorModel:
    sqlite_path = _sqlite_path_from_url(DATABASE_URL) or str(Path(DATA_DIR) / 'webui.db')
    return InteractDataConnectorModel(
        id='webui_local',
        company_user_id='local',
        name='Local WebUI SQLite',
        connector_type='sqlite',
        execution_mode='webui_local',
        storage_mode='encrypted_credentials',
        enabled=True,
        host=None,
        port=None,
        database_name='webui',
        username=None,
        password=None,
        connection_string=f'sqlite:///{sqlite_path}',
        ssl_mode=None,
        allowed_schemas=[],
        allowed_tables=['user'],
        blocked_tables=[],
        allowed_columns={
            'user': [
                'id',
                'name',
                'email',
                'role',
                'username',
                'created_at',
                'updated_at',
                'last_active_at',
            ]
        },
        blocked_columns={},
        max_rows=50,
        query_timeout_seconds=10,
        allow_write=False,
        access_mode='company_admins',
        allowed_member_ids=[],
        allowed_group_ids=[],
        allowed_model_ids=[],
        allowed_channel_ids=[],
        notes='Built-in read-only connector for WebUI administrators.',
        updated_at=0,
    )


async def _resolve_default_connector(ctx: QueryContext) -> InteractDataConnectorModel:
    if not ctx.company_user_id:
        if _is_admin(ctx) and not _is_channel_request(ctx):
            return _local_webui_connector()
        raise PermissionError('No company identity is available for this data connector request.')

    connectors = await InteractDataConnectors.list_by_company_user_id(ctx.company_user_id)
    candidates = []
    for connector in connectors:
        if _connector_denial_reason(connector, ctx) is None:
            candidates.append(connector)

    if not candidates:
        if not connectors:
            raise PermissionError(
                'No data connector is synced for this company. '
                f'context company={ctx.company_user_id or "unknown"}, model={ctx.model_id or "unknown"}, '
                f'channel={ctx.channel_id or "unknown"}.'
            )
        details = ' | '.join(_connector_debug_summary(connector, ctx) for connector in connectors[:5])
        raise PermissionError(
            'No enabled data connector is assigned to this user/model/channel. '
            f'context company={ctx.company_user_id or "unknown"}, model={ctx.model_id or "unknown"}, '
            f'channel={ctx.channel_id or "unknown"}. connectors: {details}'
        )
    if len(candidates) > 1:
        options = ', '.join(f'{connector.id} ({connector.name})' for connector in candidates[:5])
        raise PermissionError(f'Multiple data connectors are available. Specify connector_id. Available: {options}')
    _assert_not_internal_connector(candidates[0])
    return candidates[0]


async def _load_connector(connector_id: str | None, ctx: QueryContext) -> InteractDataConnectorModel:
    connector_id = (connector_id or '').strip()
    if not connector_id or connector_id == 'webui_local':
        if ctx.company_user_id:
            return await _resolve_default_connector(ctx)
        connector_id = 'webui_local'

    if connector_id == 'webui_local':
        if not _is_admin(ctx) or _is_channel_request(ctx):
            raise PermissionError('Only interactive WebUI administrators can use the built-in local WebUI connector.')
        return _local_webui_connector()

    connector = await InteractDataConnectors.get_enabled_by_id(connector_id)
    if not connector:
        raise PermissionError('Data connector not found or disabled.')
    _assert_not_internal_connector(connector)
    return connector


async def _load_connector_for_service_scan(connector_id: str) -> InteractDataConnectorModel:
    connector = await InteractDataConnectors.get_by_id(connector_id)
    if not connector:
        raise PermissionError('Data connector not found.')
    _assert_not_internal_connector(connector)
    return connector


DATABASE_ERROR_MESSAGES = {
    'DB-COMPANY-CONTEXT-MISSING': '目前請求缺少企業身分，無法判斷可使用的資料來源。',
    'DB-CONNECTOR-NOT-CONFIGURED': '此企業尚未設定可用的資料庫連接器。',
    'DB-CONNECTOR-DISABLED': '資料庫連接器不存在或目前已停用。',
    'DB-MODEL-NOT-ALLOWED': '目前模型未獲授權使用這個資料庫連接器。',
    'DB-CHANNEL-NOT-ALLOWED': '目前通訊渠道未獲授權使用這個資料庫連接器。',
    'DB-USER-NOT-ALLOWED': '目前使用者或企業身分未獲授權使用這個資料庫連接器。',
    'DB-CONNECTOR-AMBIGUOUS': '可用的資料庫連接器不只一個，必須明確指定資料來源。',
    'DB-TABLE-NOT-ALLOWED': '要求的資料表不存在或未列入授權白名單。',
    'DB-COLUMN-NOT-ALLOWED': '要求的欄位不存在或未列入授權白名單。',
    'DB-QUERY-INVALID': '資料庫查詢條件或參數格式不正確。',
    'DB-AUTHENTICATION-FAILED': '資料庫連線驗證失敗，請由管理員檢查連接器憑證。',
    'DB-CONNECTION-FAILED': '目前無法連線到資料庫，請由管理員檢查服務狀態。',
    'DB-TIMEOUT': '資料庫查詢逾時，請縮小查詢範圍後再試。',
    'DB-INTERNAL-ERROR': '資料庫工具發生未分類錯誤，請聯繫管理員並提供錯誤代碼。',
}


def _database_error_code(error: Exception) -> str:
    message = str(error).lower()
    if 'no company identity' in message:
        return 'DB-COMPANY-CONTEXT-MISSING'
    if 'no data connector is synced' in message:
        return 'DB-CONNECTOR-NOT-CONFIGURED'
    if 'multiple data connectors' in message:
        return 'DB-CONNECTOR-AMBIGUOUS'
    if 'model ' in message and ('not assigned' in message or 'no model assigned' in message):
        return 'DB-MODEL-NOT-ALLOWED'
    if 'channel ' in message and 'not assigned' in message:
        return 'DB-CHANNEL-NOT-ALLOWED'
    if (
        'another company' in message
        or 'not company admin' in message
        or 'member ' in message
        and 'not assigned' in message
    ):
        return 'DB-USER-NOT-ALLOWED'
    if 'user/model/channel is not allowed' in message or 'no enabled data connector is assigned' in message:
        return 'DB-USER-NOT-ALLOWED'
    if 'connector not found or disabled' in message or 'data connector not found' in message:
        return 'DB-CONNECTOR-DISABLED'
    if 'table is not allowed' in message or 'table is blocked' in message or 'no allowed tables' in message:
        return 'DB-TABLE-NOT-ALLOWED'
    if 'column does not exist' in message or 'column is not allowed' in message or 'column is blocked' in message:
        return 'DB-COLUMN-NOT-ALLOWED'
    if 'no readable columns' in message:
        return 'DB-COLUMN-NOT-ALLOWED'
    if 'timeout' in message or 'timed out' in message or 'statement timeout' in message:
        return 'DB-TIMEOUT'
    if (
        'authentication failed' in message
        or 'password authentication failed' in message
        or 'access denied for user' in message
    ):
        return 'DB-AUTHENTICATION-FAILED'
    if any(
        marker in message
        for marker in ('connection refused', 'could not connect', 'cannot connect', 'server closed the connection')
    ):
        return 'DB-CONNECTION-FAILED'
    if isinstance(error, (PermissionError, ValueError)):
        return 'DB-QUERY-INVALID'
    return 'DB-INTERNAL-ERROR'


def _database_error_result(error: Exception) -> dict[str, Any]:
    code = _database_error_code(error)
    return {
        'ok': False,
        'error_code': code,
        'message': DATABASE_ERROR_MESSAGES[code],
    }


def _connector_context_allowed(connector: InteractDataConnectorModel, ctx: QueryContext) -> bool:
    if connector.id != 'webui_local':
        if not connector.allowed_model_ids:
            return False
        if not ctx.model_id or ctx.model_id not in connector.allowed_model_ids:
            return False
    elif connector.allowed_model_ids and (not ctx.model_id or ctx.model_id not in connector.allowed_model_ids):
        return False
    if (
        connector.allowed_channel_ids
        and _is_channel_request(ctx)
        and (not ctx.channel_id or ctx.channel_id not in connector.allowed_channel_ids)
    ):
        return False
    if connector.access_mode == 'company_admins':
        return _is_company_admin_for_connector(connector, ctx)
    if connector.access_mode == 'selected_members':
        return bool(
            ctx.company_user_id
            and ctx.company_user_id == connector.company_user_id
            and (
                (ctx.company_member_id and ctx.company_member_id in connector.allowed_member_ids)
                or set(ctx.group_ids).intersection(connector.allowed_group_ids)
            )
        )
    if connector.access_mode == 'all_company_members':
        return bool(
            ctx.company_user_id
            and ctx.company_user_id == connector.company_user_id
            and (not _is_channel_request(ctx) or bool(ctx.company_member_id))
        )
    if connector.access_mode == 'selected_channels':
        return bool(
            ctx.company_user_id == connector.company_user_id
            and _is_channel_request(ctx)
            and ctx.channel_id
            and ctx.channel_id in connector.allowed_channel_ids
        )
    return False


def _connector_denial_reason(connector: InteractDataConnectorModel, ctx: QueryContext) -> str | None:
    if not connector.enabled:
        return 'disabled'
    if connector.id != 'webui_local':
        if not connector.allowed_model_ids:
            return 'no model assigned'
        if not ctx.model_id:
            return 'missing model id'
        if ctx.model_id not in connector.allowed_model_ids:
            return f'model {ctx.model_id} is not assigned'
    elif connector.allowed_model_ids and (not ctx.model_id or ctx.model_id not in connector.allowed_model_ids):
        return f'model {ctx.model_id or "unknown"} is not assigned'
    if (
        connector.allowed_channel_ids
        and _is_channel_request(ctx)
        and (not ctx.channel_id or ctx.channel_id not in connector.allowed_channel_ids)
    ):
        return f'channel {ctx.channel_id or "unknown"} is not assigned'
    if connector.access_mode == 'company_admins' and not _is_company_admin_for_connector(connector, ctx):
        return f'user is not company admin/member owner for company {connector.company_user_id}'
    if connector.access_mode == 'selected_members' and not (
        ctx.company_user_id
        and ctx.company_user_id == connector.company_user_id
        and (
            (ctx.company_member_id and ctx.company_member_id in connector.allowed_member_ids)
            or set(ctx.group_ids).intersection(connector.allowed_group_ids)
        )
    ):
        return (
            f'member {ctx.company_member_id or "unknown"} and groups {ctx.group_ids} '
            f'are not assigned for company {connector.company_user_id}'
        )
    if connector.access_mode == 'all_company_members' and not (
        ctx.company_user_id and ctx.company_user_id == connector.company_user_id
    ):
        return 'user belongs to another company'
    if connector.access_mode == 'selected_channels' and not (
        ctx.company_user_id == connector.company_user_id
        and _is_channel_request(ctx)
        and ctx.channel_id
        and ctx.channel_id in connector.allowed_channel_ids
    ):
        return f'channel {ctx.channel_id or "unknown"} is not assigned for this company'
    if connector.access_mode not in {'company_admins', 'selected_members', 'all_company_members', 'selected_channels'}:
        return f'unsupported access mode {connector.access_mode}'
    return None


def _connector_debug_summary(connector: InteractDataConnectorModel, ctx: QueryContext) -> str:
    return (
        f'{connector.id} ({connector.name}): {_connector_denial_reason(connector, ctx) or "allowed"}; '
        f'enabled={connector.enabled}; models={connector.allowed_model_ids}; '
        f'channels={connector.allowed_channel_ids}; access={connector.access_mode}'
    )


def _normalize_table(table: str) -> str:
    clean = table.strip()
    if not clean or len(clean) > 200:
        raise ValueError('Invalid table name.')
    parts = clean.split('.')
    if len(parts) > 2 or any(not IDENTIFIER_RE.match(part) for part in parts):
        raise ValueError('Invalid table name.')
    return '.'.join(parts)


def _normalize_column(column: str) -> str:
    clean = column.strip()
    if not IDENTIFIER_RE.match(clean):
        raise ValueError(f'Invalid column name: {column}')
    return clean


def _policy_for_table(policy: dict[str, list[str]], table: str) -> list[str]:
    return policy.get(table) or policy.get(table.split('.')[-1]) or []


def _check_table_policy(connector: InteractDataConnectorModel, table: str) -> None:
    allowed = connector.allowed_tables
    if not allowed:
        raise PermissionError('This connector has no allowed tables.')
    if table not in allowed and table.split('.')[-1] not in allowed:
        raise PermissionError(f'Table is not allowed: {table}')
    if table in connector.blocked_tables or table.split('.')[-1] in connector.blocked_tables:
        raise PermissionError(f'Table is blocked: {table}')


def _allowed_output_columns(
    connector: InteractDataConnectorModel,
    table: str,
    requested_columns: list[str] | None,
    available_columns: list[str],
) -> list[str]:
    allowed_columns = _policy_for_table(connector.allowed_columns, table)
    blocked_columns = set(_policy_for_table(connector.blocked_columns, table))
    available = set(available_columns)

    if requested_columns:
        selected = [_normalize_column(column) for column in requested_columns]
    elif allowed_columns:
        selected = allowed_columns
    else:
        selected = available_columns

    result: list[str] = []
    for column in selected:
        if column not in available:
            raise PermissionError(f'Column does not exist: {column}')
        if allowed_columns and column not in allowed_columns:
            raise PermissionError(f'Column is not allowed: {column}')
        if column in blocked_columns:
            raise PermissionError(f'Column is blocked: {column}')
        result.append(column)
    if not result:
        raise PermissionError('No readable columns remain after connector policy.')
    return result


def _filter_columns(filters: dict[str, Any] | None) -> list[str]:
    if not filters:
        return []
    return [_normalize_column(column) for column in filters.keys()]


def _order_columns(order_by: list[dict[str, str]] | None) -> list[str]:
    if not order_by:
        return []
    return [_normalize_column(item.get('column', '')) for item in order_by]


def _build_order_sql(quote_identifier, order_by: list[dict[str, str]] | None) -> str:
    if not order_by:
        return ''

    items: list[str] = []
    for item in order_by:
        column = _normalize_column(item.get('column', ''))
        direction = str(item.get('direction') or 'asc').strip().lower()
        if direction not in {'asc', 'desc'}:
            raise ValueError(f'Unsupported order direction: {direction}')
        items.append(f'{quote_identifier(column)} {direction.upper()}')

    return f' ORDER BY {", ".join(items)}'


def _build_where_sql(
    quote_identifier,
    filters: dict[str, Any] | None,
) -> tuple[str, list[Any]]:
    if not filters:
        return '', []

    clauses: list[str] = []
    values: list[Any] = []
    for column, value in filters.items():
        clean_column = _normalize_column(column)
        quoted = quote_identifier(clean_column)
        if isinstance(value, dict):
            if len(value) != 1:
                raise ValueError(f'Filter for {column} must contain exactly one operator.')
            op, operand = next(iter(value.items()))
            if op not in SAFE_FILTER_OPS:
                raise ValueError(f'Unsupported filter operator: {op}')
        else:
            op, operand = '$eq', value

        if op in {'$in', '$nin'}:
            if not isinstance(operand, list) or not operand or len(operand) > 100:
                raise ValueError(f'{op} requires a list with 1 to 100 values.')
            placeholders = ', '.join('?' for _item in operand)
            clauses.append(f'{quoted} {"NOT IN" if op == "$nin" else "IN"} ({placeholders})')
            values.extend(operand)
        elif op == '$contains':
            clauses.append(f'{quoted} LIKE ?')
            values.append(f'%{operand}%')
        else:
            sql_op = {
                '$eq': '=',
                '$ne': '!=',
                '$gt': '>',
                '$gte': '>=',
                '$lt': '<',
                '$lte': '<=',
            }[op]
            clauses.append(f'{quoted} {sql_op} ?')
            values.append(operand)

    return f' WHERE {" AND ".join(clauses)}', values


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_sqlite_table(value: str) -> str:
    return '.'.join(_quote_sqlite_identifier(part) for part in value.split('.'))


def _sqlite_connect(connector: InteractDataConnectorModel) -> sqlite3.Connection:
    path = _sqlite_path_from_url(connector.connection_string or '') or connector.host
    if not path:
        raise ValueError('SQLite connector requires a file path or sqlite connection string.')
    connection = sqlite3.connect(
        f'file:{Path(path).resolve()}?mode=ro', uri=True, timeout=connector.query_timeout_seconds
    )
    connection.row_factory = sqlite3.Row
    return connection


def _sqlite_columns(connector: InteractDataConnectorModel, table: str) -> list[str]:
    with _sqlite_connect(connector) as connection:
        rows = connection.execute(f'PRAGMA table_info({_quote_sqlite_table(table)})').fetchall()
    return [str(row['name']) for row in rows]


def _sqlite_schema(connector: InteractDataConnectorModel, table: str | None) -> list[dict[str, Any]]:
    tables = [_normalize_table(table)] if table else [_normalize_table(item) for item in connector.allowed_tables]
    schemas = []
    with _sqlite_connect(connector) as connection:
        for table_name in tables:
            _check_table_policy(connector, table_name)
            rows = connection.execute(f'PRAGMA table_info({_quote_sqlite_table(table_name)})').fetchall()
            available = [str(row['name']) for row in rows]
            visible = _allowed_output_columns(connector, table_name, None, available)
            schemas.append(
                {
                    'table': table_name,
                    'columns': [{'name': row['name'], 'type': row['type']} for row in rows if row['name'] in visible],
                }
            )
    return schemas


def _sqlite_scan_schema(connector: InteractDataConnectorModel, max_tables: int) -> dict[str, Any]:
    max_tables = max(1, min(int(max_tables or 200), 500))
    tables: list[dict[str, Any]] = []
    with _sqlite_connect(connector) as connection:
        table_rows = connection.execute(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            LIMIT ?
            """,
            (max_tables,),
        ).fetchall()
        for table_row in table_rows:
            table_name = str(table_row['name'])
            column_rows = connection.execute(f'PRAGMA table_info({_quote_sqlite_table(table_name)})').fetchall()
            fk_rows = connection.execute(f'PRAGMA foreign_key_list({_quote_sqlite_table(table_name)})').fetchall()
            tables.append(
                {
                    'name': table_name,
                    'schema': None,
                    'table': table_name,
                    'type': str(table_row['type']).upper(),
                    'columns': [
                        {
                            'name': str(row['name']),
                            'type': str(row['type']),
                            'nullable': not bool(row['notnull']),
                            'default': row['dflt_value'],
                            'ordinal': int(row['cid']) + 1,
                            'primary_key': bool(row['pk']),
                        }
                        for row in column_rows
                    ],
                    'primary_key': [str(row['name']) for row in column_rows if row['pk']],
                    'foreign_keys': [
                        {
                            'column': str(row['from']),
                            'references_table': str(row['table']),
                            'references_column': str(row['to']),
                            'constraint_name': None,
                        }
                        for row in fk_rows
                    ],
                }
            )
    return {
        'connector_id': connector.id,
        'connector_type': connector.connector_type,
        'scanned_at': dt.datetime.now(dt.UTC).isoformat(),
        'tables': tables,
    }


def _sqlite_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    order_by: list[dict[str, str]] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _sqlite_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    order_columns = _order_columns(order_by)
    _allowed_output_columns(connector, table, order_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_quote_sqlite_identifier, filters)
    order_sql = _build_order_sql(_quote_sqlite_identifier, order_by)
    sql = (
        f'SELECT {", ".join(_quote_sqlite_identifier(column) for column in selected_columns)} '
        f'FROM {_quote_sqlite_table(table)}{where_sql}{order_sql} LIMIT ?'
    )
    with _sqlite_connect(connector) as connection:
        rows = connection.execute(sql, [*values, capped_limit]).fetchall()
    return {
        'connector_id': connector.id,
        'table': table,
        'columns': selected_columns,
        'row_count': len(rows),
        'rows': [dict(row) for row in rows],
        'limit': capped_limit,
    }


def _sqlite_count(
    connector: InteractDataConnectorModel,
    table: str,
    filters: dict[str, Any] | None,
    group_by: list[str] | None,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _sqlite_columns(connector, table)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    group_columns = _allowed_output_columns(connector, table, group_by, available_columns) if group_by else []
    where_sql, values = _build_where_sql(_quote_sqlite_identifier, filters)
    group_select = ', '.join(_quote_sqlite_identifier(column) for column in group_columns)
    select_sql = f'{group_select}, COUNT(*) AS count' if group_columns else 'COUNT(*) AS count'
    group_sql = f' GROUP BY {group_select}' if group_columns else ''
    limit_sql = f' LIMIT {max(1, int(connector.max_rows))}' if group_columns else ''
    sql = f'SELECT {select_sql} FROM {_quote_sqlite_table(table)}{where_sql}{group_sql}{limit_sql}'
    with _sqlite_connect(connector) as connection:
        rows = connection.execute(sql, values).fetchall()
    if group_columns:
        return {
            'connector_id': connector.id,
            'table': table,
            'operation': 'count',
            'group_by': group_columns,
            'row_count': len(rows),
            'rows': [dict(row) for row in rows],
            'filters': filters or {},
        }
    count = int(rows[0][0] if rows else 0)
    return {
        'connector_id': connector.id,
        'table': table,
        'operation': 'count',
        'count': count,
        'filters': filters or {},
    }


def _pg_dsn(connector: InteractDataConnectorModel) -> str:
    if connector.connection_string:
        return connector.connection_string
    if not connector.host or not connector.database_name:
        raise ValueError('PostgreSQL connector requires host and database name or a connection string.')
    auth = ''
    if connector.username:
        auth = quote(connector.username)
        if connector.password:
            auth += ':' + quote(connector.password)
        auth += '@'
    port = f':{connector.port}' if connector.port else ''
    query = ''
    if connector.ssl_mode:
        query = '?' + '&'.join(f'{quote(key)}={quote(value)}' for key, value in {'sslmode': connector.ssl_mode}.items())
    return f'postgresql://{auth}{connector.host}{port}/{quote(connector.database_name)}{query}'


def _pg_quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _pg_table_parts(table: str) -> tuple[str, str]:
    parts = table.split('.')
    if len(parts) == 1:
        return 'public', parts[0]
    return parts[0], parts[1]


def _pg_table_sql(table: str) -> str:
    schema, name = _pg_table_parts(table)
    return f'{_pg_quote_identifier(schema)}.{_pg_quote_identifier(name)}'


def _pg_connect(connector: InteractDataConnectorModel):
    import psycopg

    dsn = _pg_dsn(connector)
    parsed = urlparse(dsn)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['options'] = (
        f'{query.get("options", "")} -c statement_timeout={connector.query_timeout_seconds * 1000}'.strip()
    )
    safe_dsn = urlunparse(parsed._replace(query='&'.join(f'{quote(k)}={quote(v)}' for k, v in query.items())))
    return psycopg.connect(safe_dsn, autocommit=True)


def _pg_columns(connector: InteractDataConnectorModel, table: str) -> list[str]:
    schema, name = _pg_table_parts(table)
    with _pg_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, name),
            )
            return [row[0] for row in cursor.fetchall()]


def _pg_schema(connector: InteractDataConnectorModel, table: str | None) -> list[dict[str, Any]]:
    tables = [_normalize_table(table)] if table else [_normalize_table(item) for item in connector.allowed_tables]
    schemas = []
    with _pg_connect(connector) as connection:
        with connection.cursor() as cursor:
            for table_name in tables:
                _check_table_policy(connector, table_name)
                schema, name = _pg_table_parts(table_name)
                cursor.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, name),
                )
                rows = cursor.fetchall()
                available = [row[0] for row in rows]
                visible = _allowed_output_columns(connector, table_name, None, available)
                schemas.append(
                    {
                        'table': table_name,
                        'columns': [{'name': row[0], 'type': row[1]} for row in rows if row[0] in visible],
                    }
                )
    return schemas


def _pg_scan_schema(  # noqa: C901
    connector: InteractDataConnectorModel, max_tables: int
) -> dict[str, Any]:
    max_tables = max(1, min(int(max_tables or 200), 500))
    allowed_schemas = set(connector.allowed_schemas or [])
    tables: list[dict[str, Any]] = []
    with _pg_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    namespace_row.nspname,
                    relation_row.relname,
                    CASE relation_row.relkind
                        WHEN 'v' THEN 'VIEW'
                        WHEN 'm' THEN 'MATERIALIZED VIEW'
                        WHEN 'p' THEN 'PARTITIONED TABLE'
                        ELSE 'BASE TABLE'
                    END,
                    pg_catalog.obj_description(relation_row.oid, 'pg_class')
                FROM pg_catalog.pg_class relation_row
                JOIN pg_catalog.pg_namespace namespace_row
                    ON namespace_row.oid = relation_row.relnamespace
                WHERE namespace_row.nspname NOT IN ('pg_catalog', 'information_schema')
                    AND relation_row.relkind IN ('r', 'v', 'm', 'p')
                ORDER BY namespace_row.nspname, relation_row.relname
                LIMIT %s
                """,
                (max_tables,),
            )
            table_rows = [row for row in cursor.fetchall() if not allowed_schemas or row[0] in allowed_schemas][
                :max_tables
            ]
            table_keys = {(row[0], row[1]) for row in table_rows}

            cursor.execute(
                """
                SELECT
                    namespace_row.nspname,
                    type_row.typname,
                    array_agg(enum_row.enumlabel ORDER BY enum_row.enumsortorder)
                FROM pg_catalog.pg_type type_row
                JOIN pg_catalog.pg_namespace namespace_row
                    ON namespace_row.oid = type_row.typnamespace
                JOIN pg_catalog.pg_enum enum_row
                    ON enum_row.enumtypid = type_row.oid
                GROUP BY namespace_row.nspname, type_row.typname
                """
            )
            enum_values = {(schema, type_name): list(values or []) for schema, type_name, values in cursor.fetchall()}

            columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
            cursor.execute(
                """
                SELECT
                    source_namespace.nspname,
                    source_table.relname,
                    constraint_row.conname,
                    array_agg(source_column.attname ORDER BY source_key.position)
                FROM pg_catalog.pg_constraint constraint_row
                JOIN pg_catalog.pg_class source_table
                    ON source_table.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace source_namespace
                    ON source_namespace.oid = source_table.relnamespace
                JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
                    AS source_key(attnum, position) ON TRUE
                JOIN pg_catalog.pg_attribute source_column
                    ON source_column.attrelid = source_table.oid
                    AND source_column.attnum = source_key.attnum
                WHERE constraint_row.contype = 'u'
                GROUP BY source_namespace.nspname, source_table.relname, constraint_row.conname
                ORDER BY source_namespace.nspname, source_table.relname, constraint_row.conname
                """
            )
            unique_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for schema, table, constraint, columns in cursor.fetchall():
                if (schema, table) in table_keys:
                    unique_by_table.setdefault((schema, table), []).append(
                        {'name': constraint, 'columns': list(columns or [])}
                    )

            cursor.execute(
                """
                SELECT
                    column_row.table_schema,
                    column_row.table_name,
                    column_row.column_name,
                    column_row.data_type,
                    column_row.is_nullable,
                    column_row.column_default,
                    column_row.ordinal_position,
                    column_row.udt_schema,
                    column_row.udt_name,
                    pg_catalog.col_description(relation_row.oid, attribute_row.attnum)
                FROM information_schema.columns column_row
                JOIN pg_catalog.pg_namespace namespace_row
                    ON namespace_row.nspname = column_row.table_schema
                JOIN pg_catalog.pg_class relation_row
                    ON relation_row.relnamespace = namespace_row.oid
                    AND relation_row.relname = column_row.table_name
                JOIN pg_catalog.pg_attribute attribute_row
                    ON attribute_row.attrelid = relation_row.oid
                    AND attribute_row.attname = column_row.column_name
                    AND attribute_row.attnum > 0
                    AND NOT attribute_row.attisdropped
                WHERE column_row.table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY column_row.table_schema, column_row.table_name, column_row.ordinal_position
                """
            )
            for (
                schema,
                table,
                column,
                data_type,
                nullable,
                default,
                ordinal,
                udt_schema,
                udt_name,
                description,
            ) in cursor.fetchall():
                if (schema, table) not in table_keys:
                    continue
                allowed_values = enum_values.get((udt_schema, udt_name), [])
                unique_keys = unique_by_table.get((schema, table), [])
                columns_by_table.setdefault((schema, table), []).append(
                    {
                        'name': column,
                        'type': data_type,
                        'nullable': nullable == 'YES',
                        'default': default,
                        'ordinal': ordinal,
                        'primary_key': False,
                        'unique': any(item['columns'] == [column] for item in unique_keys),
                        'description': description or '',
                        **({'allowed_values': allowed_values} if allowed_values else {}),
                    }
                )

            cursor.execute(
                """
                SELECT
                    source_namespace.nspname,
                    source_table.relname,
                    source_column.attname,
                    constraint_row.conname
                FROM pg_catalog.pg_constraint constraint_row
                JOIN pg_catalog.pg_class source_table
                    ON source_table.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace source_namespace
                    ON source_namespace.oid = source_table.relnamespace
                JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
                    AS source_key(attnum, position) ON TRUE
                JOIN pg_catalog.pg_attribute source_column
                    ON source_column.attrelid = source_table.oid
                    AND source_column.attnum = source_key.attnum
                WHERE constraint_row.contype = 'p'
                ORDER BY source_namespace.nspname, source_table.relname, source_key.position
                """
            )
            primary_by_table: dict[tuple[str, str], list[str]] = {}
            for schema, table, column, _constraint in cursor.fetchall():
                if (schema, table) in table_keys:
                    primary_by_table.setdefault((schema, table), []).append(column)

            cursor.execute(
                """
                SELECT
                    constraint_row.conname,
                    source_namespace.nspname,
                    source_table.relname,
                    source_column.attname,
                    target_namespace.nspname,
                    target_table.relname,
                    target_column.attname
                FROM pg_catalog.pg_constraint constraint_row
                JOIN pg_catalog.pg_class source_table
                    ON source_table.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace source_namespace
                    ON source_namespace.oid = source_table.relnamespace
                JOIN pg_catalog.pg_class target_table
                    ON target_table.oid = constraint_row.confrelid
                JOIN pg_catalog.pg_namespace target_namespace
                    ON target_namespace.oid = target_table.relnamespace
                JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
                    AS source_key(attnum, position) ON TRUE
                JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY
                    AS target_key(attnum, position) ON target_key.position = source_key.position
                JOIN pg_catalog.pg_attribute source_column
                    ON source_column.attrelid = source_table.oid
                    AND source_column.attnum = source_key.attnum
                JOIN pg_catalog.pg_attribute target_column
                    ON target_column.attrelid = target_table.oid
                    AND target_column.attnum = target_key.attnum
                WHERE constraint_row.contype = 'f'
                ORDER BY source_namespace.nspname, source_table.relname, source_key.position
                """
            )
            foreign_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for constraint, schema, table, column, ref_schema, ref_table, ref_column in cursor.fetchall():
                if (schema, table) not in table_keys:
                    continue
                foreign_by_table.setdefault((schema, table), []).append(
                    {
                        'column': column,
                        'references_table': f'{ref_schema}.{ref_table}',
                        'references_column': ref_column,
                        'constraint_name': constraint,
                    }
                )

            for schema, table, table_type, description in table_rows:
                key = (schema, table)
                pk = primary_by_table.get(key, [])
                columns = columns_by_table.get(key, [])
                for column in columns:
                    column['primary_key'] = column['name'] in pk
                tables.append(
                    {
                        'name': f'{schema}.{table}',
                        'schema': schema,
                        'table': table,
                        'type': table_type,
                        'description': description or '',
                        'columns': columns,
                        'primary_key': pk,
                        'unique_keys': unique_by_table.get(key, []),
                        'foreign_keys': foreign_by_table.get(key, []),
                    }
                )
            cursor.execute(
                """
                SELECT namespace_row.nspname, pg_catalog.obj_description(namespace_row.oid, 'pg_namespace')
                FROM pg_catalog.pg_namespace namespace_row
                WHERE namespace_row.nspname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY namespace_row.nspname
                """
            )
            schema_descriptions = {
                schema: description
                for schema, description in cursor.fetchall()
                if description and (not allowed_schemas or schema in allowed_schemas)
            }
            cursor.execute(
                """
                SELECT pg_catalog.shobj_description(database_row.oid, 'pg_database')
                FROM pg_catalog.pg_database database_row
                WHERE database_row.datname = current_database()
                """
            )
            database_description_row = cursor.fetchone()
    return {
        'connector_id': connector.id,
        'connector_type': connector.connector_type,
        'scanned_at': dt.datetime.now(dt.UTC).isoformat(),
        'database': {
            'description': (database_description_row or [None])[0] or '',
        },
        'schema_descriptions': schema_descriptions,
        'tables': tables,
    }


def _pg_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    order_by: list[dict[str, str]] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _pg_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    order_columns = _order_columns(order_by)
    _allowed_output_columns(connector, table, order_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_pg_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    order_sql = _build_order_sql(_pg_quote_identifier, order_by)
    sql = (
        f'SELECT {", ".join(_pg_quote_identifier(column) for column in selected_columns)} '
        f'FROM {_pg_table_sql(table)}{where_sql}{order_sql} LIMIT %s'
    )
    with _pg_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, [*values, capped_limit])
            names = [description.name for description in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return {
        'connector_id': connector.id,
        'table': table,
        'columns': selected_columns,
        'row_count': len(rows),
        'rows': rows,
        'limit': capped_limit,
    }


def _pg_count(
    connector: InteractDataConnectorModel,
    table: str,
    filters: dict[str, Any] | None,
    group_by: list[str] | None,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _pg_columns(connector, table)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    group_columns = _allowed_output_columns(connector, table, group_by, available_columns) if group_by else []
    where_sql, values = _build_where_sql(_pg_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    group_select = ', '.join(_pg_quote_identifier(column) for column in group_columns)
    select_sql = f'{group_select}, COUNT(*) AS count' if group_columns else 'COUNT(*) AS count'
    group_sql = f' GROUP BY {group_select}' if group_columns else ''
    limit_sql = f' LIMIT {max(1, int(connector.max_rows))}' if group_columns else ''
    sql = f'SELECT {select_sql} FROM {_pg_table_sql(table)}{where_sql}{group_sql}{limit_sql}'
    with _pg_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, values)
            names = [description.name for description in cursor.description]
            rows = cursor.fetchall()
    if group_columns:
        return {
            'connector_id': connector.id,
            'table': table,
            'operation': 'count',
            'group_by': group_columns,
            'row_count': len(rows),
            'rows': [dict(zip(names, row)) for row in rows],
            'filters': filters or {},
        }
    count = int(rows[0][0] if rows else 0)
    return {
        'connector_id': connector.id,
        'table': table,
        'operation': 'count',
        'count': count,
        'filters': filters or {},
    }


def _mysql_connection_params(connector: InteractDataConnectorModel) -> dict[str, Any]:
    if connector.connection_string:
        parsed = urlparse(connector.connection_string)
        if parsed.scheme not in {'mysql', 'mysql+pymysql', 'mariadb'}:
            raise ValueError('MySQL connector requires a mysql:// or mariadb:// connection string.')
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params: dict[str, Any] = {
            'host': parsed.hostname,
            'port': parsed.port or 3306,
            'user': unquote(parsed.username or ''),
            'password': unquote(parsed.password or ''),
            'database': unquote(parsed.path.lstrip('/')),
        }
        if query.get('charset'):
            params['charset'] = query['charset']
    else:
        params = {
            'host': connector.host,
            'port': connector.port or 3306,
            'user': connector.username or '',
            'password': connector.password or '',
            'database': connector.database_name or '',
        }

    if not params.get('host') or not params.get('database'):
        raise ValueError('MySQL connector requires host and database name or a connection string.')

    params['charset'] = params.get('charset') or 'utf8mb4'
    params['connect_timeout'] = max(1, min(connector.query_timeout_seconds, 60))
    params['read_timeout'] = max(1, min(connector.query_timeout_seconds, 300))
    params['write_timeout'] = max(1, min(connector.query_timeout_seconds, 300))
    params['autocommit'] = True

    if connector.ssl_mode == 'require':
        params['ssl'] = {}

    return params


def _mysql_quote_identifier(value: str) -> str:
    return '`' + value.replace('`', '``') + '`'


def _mysql_table_parts(connector: InteractDataConnectorModel, table: str) -> tuple[str, str]:
    parts = table.split('.')
    if len(parts) == 1:
        return _mysql_connection_params(connector)['database'], parts[0]
    return parts[0], parts[1]


def _mysql_table_sql(connector: InteractDataConnectorModel, table: str) -> str:
    database, name = _mysql_table_parts(connector, table)
    return f'{_mysql_quote_identifier(database)}.{_mysql_quote_identifier(name)}'


def _mysql_connect(connector: InteractDataConnectorModel):
    import pymysql

    connection = pymysql.connect(**_mysql_connection_params(connector))
    try:
        with connection.cursor() as cursor:
            # MySQL supports MAX_EXECUTION_TIME for SELECT. MariaDB may not; ignore if unavailable.
            try:
                cursor.execute('SET SESSION max_execution_time=%s', (connector.query_timeout_seconds * 1000,))
            except Exception:
                pass
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
    except Exception:
        pass
    return connection


def _mysql_columns(connector: InteractDataConnectorModel, table: str) -> list[str]:
    database, name = _mysql_table_parts(connector, table)
    with _mysql_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (database, name),
            )
            return [row[0] for row in cursor.fetchall()]


def _mysql_schema(connector: InteractDataConnectorModel, table: str | None) -> list[dict[str, Any]]:
    tables = [_normalize_table(table)] if table else [_normalize_table(item) for item in connector.allowed_tables]
    schemas = []
    with _mysql_connect(connector) as connection:
        with connection.cursor() as cursor:
            for table_name in tables:
                _check_table_policy(connector, table_name)
                database, name = _mysql_table_parts(connector, table_name)
                cursor.execute(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                    """,
                    (database, name),
                )
                rows = cursor.fetchall()
                available = [row[0] for row in rows]
                visible = _allowed_output_columns(connector, table_name, None, available)
                schemas.append(
                    {
                        'table': table_name,
                        'columns': [{'name': row[0], 'type': row[1]} for row in rows if row[0] in visible],
                    }
                )
    return schemas


def _mysql_scan_schema(connector: InteractDataConnectorModel, max_tables: int) -> dict[str, Any]:
    max_tables = max(1, min(int(max_tables or 200), 500))
    database = _mysql_connection_params(connector)['database']
    tables: list[dict[str, Any]] = []
    with _mysql_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT TABLE_NAME, TABLE_TYPE
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME
                LIMIT %s
                """,
                (database, max_tables),
            )
            table_rows = cursor.fetchall()
            table_names = {row[0] for row in table_rows}

            cursor.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, ORDINAL_POSITION, COLUMN_KEY
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (database,),
            )
            columns_by_table: dict[str, list[dict[str, Any]]] = {}
            primary_by_table: dict[str, list[str]] = {}
            for table, column, data_type, nullable, default, ordinal, column_key in cursor.fetchall():
                if table not in table_names:
                    continue
                is_primary = column_key == 'PRI'
                columns_by_table.setdefault(table, []).append(
                    {
                        'name': column,
                        'type': data_type,
                        'nullable': nullable == 'YES',
                        'default': default,
                        'ordinal': ordinal,
                        'primary_key': is_primary,
                    }
                )
                if is_primary:
                    primary_by_table.setdefault(table, []).append(column)

            cursor.execute(
                """
                SELECT
                    CONSTRAINT_NAME,
                    TABLE_NAME,
                    COLUMN_NAME,
                    REFERENCED_TABLE_SCHEMA,
                    REFERENCED_TABLE_NAME,
                    REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (database,),
            )
            foreign_by_table: dict[str, list[dict[str, Any]]] = {}
            for constraint, table, column, ref_schema, ref_table, ref_column in cursor.fetchall():
                if table not in table_names:
                    continue
                foreign_by_table.setdefault(table, []).append(
                    {
                        'column': column,
                        'references_table': f'{ref_schema}.{ref_table}',
                        'references_column': ref_column,
                        'constraint_name': constraint,
                    }
                )

            for table, table_type in table_rows:
                tables.append(
                    {
                        'name': table,
                        'schema': database,
                        'table': table,
                        'type': table_type,
                        'columns': columns_by_table.get(table, []),
                        'primary_key': primary_by_table.get(table, []),
                        'foreign_keys': foreign_by_table.get(table, []),
                    }
                )
    return {
        'connector_id': connector.id,
        'connector_type': connector.connector_type,
        'scanned_at': dt.datetime.now(dt.UTC).isoformat(),
        'tables': tables,
    }


def _mysql_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    order_by: list[dict[str, str]] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _mysql_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    order_columns = _order_columns(order_by)
    _allowed_output_columns(connector, table, order_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_mysql_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    order_sql = _build_order_sql(_mysql_quote_identifier, order_by)
    sql = (
        f'SELECT {", ".join(_mysql_quote_identifier(column) for column in selected_columns)} '
        f'FROM {_mysql_table_sql(connector, table)}{where_sql}{order_sql} LIMIT %s'
    )
    with _mysql_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, [*values, capped_limit])
            names = [description[0] for description in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return {
        'connector_id': connector.id,
        'table': table,
        'columns': selected_columns,
        'row_count': len(rows),
        'rows': rows,
        'limit': capped_limit,
    }


def _mysql_count(
    connector: InteractDataConnectorModel,
    table: str,
    filters: dict[str, Any] | None,
    group_by: list[str] | None,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _mysql_columns(connector, table)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    group_columns = _allowed_output_columns(connector, table, group_by, available_columns) if group_by else []
    where_sql, values = _build_where_sql(_mysql_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    group_select = ', '.join(_mysql_quote_identifier(column) for column in group_columns)
    select_sql = f'{group_select}, COUNT(*) AS count' if group_columns else 'COUNT(*) AS count'
    group_sql = f' GROUP BY {group_select}' if group_columns else ''
    limit_sql = f' LIMIT {max(1, int(connector.max_rows))}' if group_columns else ''
    sql = f'SELECT {select_sql} FROM {_mysql_table_sql(connector, table)}{where_sql}{group_sql}{limit_sql}'
    with _mysql_connect(connector) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, values)
            names = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
    if group_columns:
        return {
            'connector_id': connector.id,
            'table': table,
            'operation': 'count',
            'group_by': group_columns,
            'row_count': len(rows),
            'rows': [dict(zip(names, row)) for row in rows],
            'filters': filters or {},
        }
    count = int(rows[0][0] if rows else 0)
    return {
        'connector_id': connector.id,
        'table': table,
        'operation': 'count',
        'count': count,
        'filters': filters or {},
    }


def _mssql_connection_string(connector: InteractDataConnectorModel) -> str:
    if connector.connection_string:
        if ';' in connector.connection_string and '=' in connector.connection_string:
            return connector.connection_string
        parsed = urlparse(connector.connection_string)
        if parsed.scheme not in {'mssql', 'sqlserver', 'mssql+pyodbc'}:
            raise ValueError('SQL Server connector requires an ODBC string or mssql:// connection string.')
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        driver = query.get('driver') or os.environ.get('INTERACT_MSSQL_ODBC_DRIVER') or 'ODBC Driver 18 for SQL Server'
        server = parsed.hostname or ''
        if parsed.port:
            server = f'{server},{parsed.port}'
        parts = [
            f'DRIVER={{{driver}}}',
            f'SERVER={server}',
            f'DATABASE={unquote(parsed.path.lstrip("/"))}',
        ]
        if parsed.username:
            parts.append(f'UID={unquote(parsed.username)}')
        if parsed.password:
            parts.append(f'PWD={unquote(parsed.password)}')
        parts.append(f'Encrypt={query.get("encrypt") or ("yes" if connector.ssl_mode == "require" else "no")}')
        parts.append(f'TrustServerCertificate={query.get("trustServerCertificate") or "yes"}')
        return ';'.join(parts) + ';'

    if not connector.host or not connector.database_name:
        raise ValueError('SQL Server connector requires host and database name or a connection string.')
    driver = os.environ.get('INTERACT_MSSQL_ODBC_DRIVER') or 'ODBC Driver 18 for SQL Server'
    server = connector.host
    if connector.port:
        server = f'{server},{connector.port}'
    parts = [
        f'DRIVER={{{driver}}}',
        f'SERVER={server}',
        f'DATABASE={connector.database_name}',
    ]
    if connector.username:
        parts.append(f'UID={connector.username}')
    if connector.password:
        parts.append(f'PWD={connector.password}')
    else:
        parts.append('Trusted_Connection=yes')
    parts.append(f'Encrypt={"yes" if connector.ssl_mode == "require" else "no"}')
    parts.append('TrustServerCertificate=yes')
    return ';'.join(parts) + ';'


def _mssql_connect(connector: InteractDataConnectorModel):
    import pyodbc

    connection = pyodbc.connect(
        _mssql_connection_string(connector),
        timeout=max(1, min(connector.query_timeout_seconds, 60)),
        autocommit=True,
    )
    try:
        connection.timeout = max(1, min(connector.query_timeout_seconds, 300))
    except Exception:
        pass
    return connection


def _mssql_quote_identifier(value: str) -> str:
    return '[' + value.replace(']', ']]') + ']'


def _mssql_table_parts(table: str) -> tuple[str, str]:
    parts = table.split('.')
    if len(parts) == 1:
        return 'dbo', parts[0]
    return parts[0], parts[1]


def _mssql_table_sql(table: str) -> str:
    schema, name = _mssql_table_parts(table)
    return f'{_mssql_quote_identifier(schema)}.{_mssql_quote_identifier(name)}'


def _mssql_columns(connector: InteractDataConnectorModel, table: str) -> list[str]:
    schema, name = _mssql_table_parts(table)
    with _mssql_connect(connector) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            (schema, name),
        )
        return [row[0] for row in cursor.fetchall()]


def _mssql_schema(connector: InteractDataConnectorModel, table: str | None) -> list[dict[str, Any]]:
    tables = [_normalize_table(table)] if table else [_normalize_table(item) for item in connector.allowed_tables]
    schemas = []
    with _mssql_connect(connector) as connection:
        cursor = connection.cursor()
        for table_name in tables:
            _check_table_policy(connector, table_name)
            schema, name = _mssql_table_parts(table_name)
            cursor.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                ORDER BY ORDINAL_POSITION
                """,
                (schema, name),
            )
            rows = cursor.fetchall()
            available = [row[0] for row in rows]
            visible = _allowed_output_columns(connector, table_name, None, available)
            schemas.append(
                {
                    'table': table_name,
                    'columns': [{'name': row[0], 'type': row[1]} for row in rows if row[0] in visible],
                }
            )
    return schemas


def _mssql_scan_schema(connector: InteractDataConnectorModel, max_tables: int) -> dict[str, Any]:
    max_tables = max(1, min(int(max_tables or 200), 500))
    allowed_schemas = set(connector.allowed_schemas or [])
    tables: list[dict[str, Any]] = []
    with _mssql_connect(connector) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT TOP {max_tables} TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA', 'sys')
            ORDER BY TABLE_SCHEMA, TABLE_NAME
            """
        )
        table_rows = [row for row in cursor.fetchall() if not allowed_schemas or row[0] in allowed_schemas][:max_tables]
        table_keys = {(row[0], row[1]) for row in table_rows}

        cursor.execute(
            """
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA', 'sys')
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
            """
        )
        columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for schema, table, column, data_type, nullable, default, ordinal in cursor.fetchall():
            if (schema, table) not in table_keys:
                continue
            columns_by_table.setdefault((schema, table), []).append(
                {
                    'name': column,
                    'type': data_type,
                    'nullable': nullable == 'YES',
                    'default': default,
                    'ordinal': ordinal,
                    'primary_key': False,
                }
            )

        cursor.execute(
            """
            SELECT KU.TABLE_SCHEMA, KU.TABLE_NAME, KU.COLUMN_NAME, KU.CONSTRAINT_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS TC
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE KU
                ON TC.CONSTRAINT_NAME = KU.CONSTRAINT_NAME
                AND TC.TABLE_SCHEMA = KU.TABLE_SCHEMA
                AND TC.TABLE_NAME = KU.TABLE_NAME
            WHERE TC.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ORDER BY KU.TABLE_SCHEMA, KU.TABLE_NAME, KU.ORDINAL_POSITION
            """
        )
        primary_by_table: dict[tuple[str, str], list[str]] = {}
        for schema, table, column, _constraint in cursor.fetchall():
            if (schema, table) in table_keys:
                primary_by_table.setdefault((schema, table), []).append(column)

        cursor.execute(
            """
            SELECT
                FK.CONSTRAINT_NAME,
                CU.TABLE_SCHEMA,
                CU.TABLE_NAME,
                CU.COLUMN_NAME,
                PKCU.TABLE_SCHEMA AS REFERENCED_TABLE_SCHEMA,
                PKCU.TABLE_NAME AS REFERENCED_TABLE_NAME,
                PKCU.COLUMN_NAME AS REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS FK
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE CU
                ON CU.CONSTRAINT_NAME = FK.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE PKCU
                ON PKCU.CONSTRAINT_NAME = FK.UNIQUE_CONSTRAINT_NAME
                AND PKCU.ORDINAL_POSITION = CU.ORDINAL_POSITION
            ORDER BY CU.TABLE_SCHEMA, CU.TABLE_NAME, CU.ORDINAL_POSITION
            """
        )
        foreign_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for constraint, schema, table, column, ref_schema, ref_table, ref_column in cursor.fetchall():
            if (schema, table) not in table_keys:
                continue
            foreign_by_table.setdefault((schema, table), []).append(
                {
                    'column': column,
                    'references_table': f'{ref_schema}.{ref_table}',
                    'references_column': ref_column,
                    'constraint_name': constraint,
                }
            )

        for schema, table, table_type in table_rows:
            key = (schema, table)
            pk = primary_by_table.get(key, [])
            columns = columns_by_table.get(key, [])
            for column in columns:
                column['primary_key'] = column['name'] in pk
            tables.append(
                {
                    'name': f'{schema}.{table}',
                    'schema': schema,
                    'table': table,
                    'type': table_type,
                    'columns': columns,
                    'primary_key': pk,
                    'foreign_keys': foreign_by_table.get(key, []),
                }
            )
    return {
        'connector_id': connector.id,
        'connector_type': connector.connector_type,
        'scanned_at': dt.datetime.now(dt.UTC).isoformat(),
        'tables': tables,
    }


def _mssql_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    order_by: list[dict[str, str]] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _mssql_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    order_columns = _order_columns(order_by)
    _allowed_output_columns(connector, table, order_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_mssql_quote_identifier, filters)
    order_sql = _build_order_sql(_mssql_quote_identifier, order_by)
    sql = (
        f'SELECT TOP {capped_limit} {", ".join(_mssql_quote_identifier(column) for column in selected_columns)} '
        f'FROM {_mssql_table_sql(table)}{where_sql}{order_sql}'
    )
    with _mssql_connect(connector) as connection:
        cursor = connection.cursor()
        cursor.execute(sql, values)
        names = [description[0] for description in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return {
        'connector_id': connector.id,
        'table': table,
        'columns': selected_columns,
        'row_count': len(rows),
        'rows': rows,
        'limit': capped_limit,
    }


def _mssql_count(
    connector: InteractDataConnectorModel,
    table: str,
    filters: dict[str, Any] | None,
    group_by: list[str] | None,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _mssql_columns(connector, table)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    group_columns = _allowed_output_columns(connector, table, group_by, available_columns) if group_by else []
    where_sql, values = _build_where_sql(_mssql_quote_identifier, filters)
    group_select = ', '.join(_mssql_quote_identifier(column) for column in group_columns)
    select_sql = f'{group_select}, COUNT(*) AS count' if group_columns else 'COUNT(*) AS count'
    top_sql = f'TOP {max(1, int(connector.max_rows))} ' if group_columns else ''
    group_sql = f' GROUP BY {group_select}' if group_columns else ''
    sql = f'SELECT {top_sql}{select_sql} FROM {_mssql_table_sql(table)}{where_sql}{group_sql}'
    with _mssql_connect(connector) as connection:
        cursor = connection.cursor()
        cursor.execute(sql, values)
        names = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
    if group_columns:
        return {
            'connector_id': connector.id,
            'table': table,
            'operation': 'count',
            'group_by': group_columns,
            'row_count': len(rows),
            'rows': [dict(zip(names, row)) for row in rows],
            'filters': filters or {},
        }
    count = int(rows[0][0] if rows else 0)
    return {
        'connector_id': connector.id,
        'table': table,
        'operation': 'count',
        'count': count,
        'filters': filters or {},
    }


async def _record_event(
    connector_id: str,
    ctx: QueryContext,
    table_name: str | None,
    status: str,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    try:
        await InteractDataConnectors.record_query_event(
            connector_id=connector_id,
            user_id=ctx.user_id,
            model_id=ctx.model_id,
            channel_id=ctx.channel_id,
            table_name=table_name,
            status=status,
            row_count=row_count,
            error=error,
        )
    except Exception:
        pass


async def scan_data_connector_schema(connector_id: str, max_tables: int = 200) -> dict[str, Any]:
    connector = await _load_connector_for_service_scan(connector_id)
    if connector.connector_type == 'sqlite':
        return await asyncio.to_thread(_sqlite_scan_schema, connector, max_tables)
    if connector.connector_type in {'postgres', 'postgresql'}:
        return await asyncio.to_thread(_pg_scan_schema, connector, max_tables)
    if connector.connector_type in {'mysql', 'mariadb'}:
        return await asyncio.to_thread(_mysql_scan_schema, connector, max_tables)
    if connector.connector_type == 'mssql':
        return await asyncio.to_thread(_mssql_scan_schema, connector, max_tables)
    raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')


def _table_aliases(table: str | None) -> set[str]:
    if not table:
        return set()
    clean = str(table).strip()
    if not clean:
        return set()
    return {clean, clean.split('.')[-1]}


def _schema_table_name(item: dict[str, Any]) -> str:
    name = str(item.get('name') or '').strip()
    if name:
        return name
    schema = str(item.get('schema') or '').strip()
    table = str(item.get('table') or '').strip()
    if schema and table:
        return f'{schema}.{table}'
    return table


def _filter_schema_scan_for_policy(
    connector: InteractDataConnectorModel,
    scanned: dict[str, Any],
    table: str | None,
) -> list[dict[str, Any]]:
    requested = _normalize_table(table) if table else None
    requested_aliases = _table_aliases(requested)
    schemas: list[dict[str, Any]] = []
    table_items: dict[str, dict[str, Any]] = {}

    for item in scanned.get('tables') or []:
        if not isinstance(item, dict):
            continue
        table_name = _schema_table_name(item)
        if not table_name:
            continue
        for alias in _table_aliases(table_name):
            table_items.setdefault(alias, item)

    for item in scanned.get('tables') or []:
        if not isinstance(item, dict):
            continue
        table_name = _schema_table_name(item)
        if not table_name:
            continue
        if requested_aliases and not (_table_aliases(table_name) & requested_aliases):
            continue

        try:
            _check_table_policy(connector, table_name)
        except Exception:
            continue

        columns = [column for column in item.get('columns') or [] if isinstance(column, dict)]
        available_columns = [
            str(column.get('name')) for column in columns if isinstance(column.get('name'), str) and column.get('name')
        ]
        try:
            visible_columns = set(_allowed_output_columns(connector, table_name, None, available_columns))
        except Exception:
            continue

        primary_key = [
            str(column)
            for column in item.get('primary_key') or []
            if isinstance(column, str) and column in visible_columns
        ]
        foreign_keys: list[dict[str, Any]] = []
        for fk in item.get('foreign_keys') or []:
            if not isinstance(fk, dict):
                continue
            column = str(fk.get('column') or '').strip()
            reference_table = str(fk.get('references_table') or '').strip()
            reference_column = str(fk.get('references_column') or '').strip()
            if not column or column not in visible_columns or not reference_table or not reference_column:
                continue
            try:
                _check_table_policy(connector, reference_table)
            except Exception:
                continue
            reference_item = next(
                (table_items.get(alias) for alias in _table_aliases(reference_table) if table_items.get(alias)),
                None,
            )
            if not reference_item:
                continue
            reference_columns = [
                column
                for column in reference_item.get('columns') or []
                if isinstance(column, dict) and isinstance(column.get('name'), str) and column.get('name')
            ]
            reference_available = [str(column.get('name')) for column in reference_columns]
            try:
                reference_visible = set(_allowed_output_columns(connector, reference_table, None, reference_available))
            except Exception:
                continue
            if reference_column not in reference_visible:
                continue
            foreign_keys.append(
                {
                    'column': column,
                    'references_table': reference_table,
                    'references_column': reference_column,
                    'constraint_name': fk.get('constraint_name'),
                }
            )

        schemas.append(
            {
                'table': table_name,
                'schema': item.get('schema'),
                'name': item.get('name') or table_name,
                'type': item.get('type') or 'BASE TABLE',
                'description': item.get('description') or '',
                'columns': [
                    {
                        'name': column.get('name'),
                        'type': column.get('type'),
                        'primary_key': bool(column.get('primary_key')),
                        'nullable': bool(column.get('nullable', True)),
                        'unique': bool(column.get('unique')),
                        'description': column.get('description') or '',
                        **({'default': column.get('default')} if column.get('default') is not None else {}),
                        **(
                            {'allowed_values': list(column.get('allowed_values') or [])}
                            if column.get('allowed_values')
                            else {}
                        ),
                    }
                    for column in columns
                    if column.get('name') in visible_columns
                ],
                'primary_key': primary_key,
                'unique_keys': [
                    {
                        'name': key.get('name'),
                        'columns': [column for column in key.get('columns') or [] if column in visible_columns],
                    }
                    for key in item.get('unique_keys') or []
                    if key.get('columns') and all(column in visible_columns for column in key.get('columns') or [])
                ],
                'foreign_keys': foreign_keys,
            }
        )

    if requested and not schemas:
        raise PermissionError(f'Table is not allowed or was not found in schema scan: {requested}')
    return schemas


async def _connector_visible_schema(
    connector: InteractDataConnectorModel,
    table: str | None,
) -> list[dict[str, Any]]:
    max_tables = max(50, min(500, len(connector.allowed_tables or []) + 50))
    if connector.connector_type == 'sqlite':
        scanned = await asyncio.to_thread(_sqlite_scan_schema, connector, max_tables)
    elif connector.connector_type in {'postgres', 'postgresql'}:
        scanned = await asyncio.to_thread(_pg_scan_schema, connector, max_tables)
    elif connector.connector_type in {'mysql', 'mariadb'}:
        scanned = await asyncio.to_thread(_mysql_scan_schema, connector, max_tables)
    elif connector.connector_type == 'mssql':
        scanned = await asyncio.to_thread(_mssql_scan_schema, connector, max_tables)
    else:
        raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
    return _filter_schema_scan_for_policy(connector, scanned, table)


async def interact_database_schema(
    connector_id: str = 'webui_local',
    table: str | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Inspect the schema of an authorized Interact Vision data connector.
    Only tables and columns allowed by the connector policy are returned.
    Relationship metadata is included when the database exposes primary keys and foreign keys.

    :param connector_id: Optional data connector id from the company portal. When omitted,
        the only connector assigned to the current model is selected automatically.
    :param table: Optional table name to inspect.
    :return: JSON with visible tables, columns, primary keys, and foreign keys.
    """
    ctx = _context(__user__, __metadata__)
    try:
        await _resolve_context_groups(ctx)
        connector = await _load_connector(connector_id, ctx)
        if not _connector_context_allowed(connector, ctx):
            raise PermissionError('This user/model/channel is not allowed to use the data connector.')
        schemas = await _connector_visible_schema(connector, table)
        await _record_event(connector.id, ctx, table, 'schema', row_count=0)
        return _json_result({'ok': True, 'connector_id': connector.id, 'name': connector.name, 'schemas': schemas})
    except Exception as error:
        await _record_event(connector_id, ctx, table, 'error', error=str(error))
        return _json_result(_database_error_result(error))


async def interact_database_query(
    connector_id: str = 'webui_local',
    table: str = '',
    operation: str = 'select',
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    order_by: list[dict[str, str]] | None = None,
    group_by: list[str] | None = None,
    limit: int = 20,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Query rows from an authorized Interact Vision data connector using a safe read-only builder.
    This tool does not accept raw SQL and does not have a sql parameter.
    Use interact_database_schema first when unsure, then call this tool with table, operation,
    columns, filters, order_by, group_by, and limit.
    For business metrics, rankings, or analytics, call interact_semantic_catalog first and use
    interact_semantic_query when a dataset is returned. Never use this raw table tool to bypass
    an empty or unauthorized semantic catalog result.
    Supported operations are "select" and "count". Use operation="count" for "how many",
    totals, and grouped counts.
    Example: table="crm.users", columns=["name","department","role","email"],
    filters={"active": true}, order_by=[{"column":"id","direction":"asc"}], limit=10.
    Count example: table="v_customer_summary", operation="count".
    Grouped count example: table="crm.users", operation="count", group_by=["department"].

    :param connector_id: Optional data connector id from the company portal. When omitted,
        the only connector assigned to the current model is selected automatically.
    :param table: Allowed table name to read.
    :param operation: "select" to return rows, or "count" to return COUNT(*) with optional group_by.
    :param columns: Optional list of columns to return. Connector policy is always enforced.
    :param filters: Optional safe filters, for example email $contains, id $in, or direct equality values.
    :param order_by: Optional safe sort list, e.g. [{"column": "follow_up_at", "direction": "desc"}].
    :param group_by: Optional grouping columns for operation="count".
    :param limit: Maximum rows to return, capped by connector policy.
    :return: JSON with rows and query metadata.
    """
    ctx = _context(__user__, __metadata__)
    row_count = 0
    resolved_connector_id = connector_id
    try:
        await _resolve_context_groups(ctx)
        operation = (operation or 'select').strip().lower()
        if operation not in {'select', 'count'}:
            raise ValueError('Unsupported operation. Use "select" or "count".')
        connector = await _load_connector(connector_id, ctx)
        resolved_connector_id = connector.id
        if not _connector_context_allowed(connector, ctx):
            raise PermissionError('This user/model/channel is not allowed to use the data connector.')
        if connector.allow_write:
            raise PermissionError('Write-enabled connectors are not executable by this read-only tool.')
        if operation == 'count':
            if order_by:
                raise ValueError('order_by is not supported for count operations.')
            if columns:
                raise ValueError('columns is not supported for count operations. Use group_by for grouped counts.')
            if connector.connector_type == 'sqlite':
                result = await asyncio.to_thread(_sqlite_count, connector, table, filters, group_by)
            elif connector.connector_type in {'postgres', 'postgresql'}:
                result = await asyncio.to_thread(_pg_count, connector, table, filters, group_by)
            elif connector.connector_type in {'mysql', 'mariadb'}:
                result = await asyncio.to_thread(_mysql_count, connector, table, filters, group_by)
            elif connector.connector_type == 'mssql':
                result = await asyncio.to_thread(_mssql_count, connector, table, filters, group_by)
            else:
                raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
        elif connector.connector_type == 'sqlite':
            if group_by:
                raise ValueError('group_by is only supported for count operations.')
            result = await asyncio.to_thread(_sqlite_query, connector, table, columns, filters, order_by, limit)
        elif connector.connector_type in {'postgres', 'postgresql'}:
            if group_by:
                raise ValueError('group_by is only supported for count operations.')
            result = await asyncio.to_thread(_pg_query, connector, table, columns, filters, order_by, limit)
        elif connector.connector_type in {'mysql', 'mariadb'}:
            if group_by:
                raise ValueError('group_by is only supported for count operations.')
            result = await asyncio.to_thread(_mysql_query, connector, table, columns, filters, order_by, limit)
        elif connector.connector_type == 'mssql':
            if group_by:
                raise ValueError('group_by is only supported for count operations.')
            result = await asyncio.to_thread(_mssql_query, connector, table, columns, filters, order_by, limit)
        else:
            raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
        row_count = int(result.get('row_count') or result.get('count') or 0)
        await _record_event(connector.id, ctx, table, 'success', row_count=row_count)
        return _json_result({'ok': True, **result})
    except Exception as error:
        await _record_event(resolved_connector_id, ctx, table, 'error', row_count=row_count, error=str(error))
        return _json_result(_database_error_result(error))


async def _database_count_compat(
    connector_id: str = 'webui_local',
    table: str = '',
    filters: dict[str, Any] | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Count rows from an authorized Interact Vision data connector using a safe read-only COUNT(*) query.
    Use this tool for questions asking how many records exist, such as "目前有幾間公司" or "count customers".
    This tool does not accept raw SQL and does not return row contents.

    :param connector_id: Optional data connector id from the company portal. When omitted,
        the only connector assigned to the current model is selected automatically.
    :param table: Allowed table or view name to count.
    :param filters: Optional equality/range filters, e.g. {"active": true} or {"created_at": {"$gte": "2026-01-01"}}.
    :return: JSON with the total count.
    """
    ctx = _context(__user__, __metadata__)
    try:
        connector = await _load_connector(connector_id, ctx)
        if not _connector_context_allowed(connector, ctx):
            raise PermissionError('This user/model/channel is not allowed to use the data connector.')
        if connector.allow_write:
            raise PermissionError('Write-enabled connectors are not executable by this read-only tool.')
        if connector.connector_type == 'sqlite':
            result = await asyncio.to_thread(_sqlite_count, connector, table, filters, None)
        elif connector.connector_type in {'postgres', 'postgresql'}:
            result = await asyncio.to_thread(_pg_count, connector, table, filters, None)
        elif connector.connector_type in {'mysql', 'mariadb'}:
            result = await asyncio.to_thread(_mysql_count, connector, table, filters, None)
        elif connector.connector_type == 'mssql':
            result = await asyncio.to_thread(_mssql_count, connector, table, filters, None)
        else:
            raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
        await _record_event(connector.id, ctx, table, 'success', row_count=int(result.get('count') or 0))
        return _json_result({'ok': True, **result})
    except Exception as error:
        await _record_event(connector_id, ctx, table, 'error', row_count=0, error=str(error))
        return _json_result(_database_error_result(error))
