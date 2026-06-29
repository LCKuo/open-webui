from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse

from fastapi import Request

from open_webui.env import DATABASE_URL, DATA_DIR
from open_webui.models.interact_data_connectors import (
    InteractDataConnectorModel,
    InteractDataConnectors,
)

IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
SAFE_FILTER_OPS = {'$eq', '$ne', '$gt', '$gte', '$lt', '$lte', '$contains'}


@dataclass
class QueryContext:
    user_id: str | None
    user_role: str | None
    model_id: str | None
    channel_id: str | None
    company_member_id: str | None


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


def _context(__user__: dict | None, __metadata__: dict | None) -> QueryContext:
    user = __user__ or {}
    return QueryContext(
        user_id=user.get('id'),
        user_role=user.get('role'),
        model_id=_metadata_value(__metadata__, 'model_id', 'modelId', 'model'),
        channel_id=_metadata_value(__metadata__, 'channelId', 'channel_id'),
        company_member_id=(
            user.get('companyMemberId')
            or user.get('company_member_id')
            or _metadata_value(__metadata__, 'companyMemberId', 'company_member_id')
        ),
    )


def _is_admin(ctx: QueryContext) -> bool:
    return ctx.user_role == 'admin'


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
            'Synced SQLite connectors are disabled by default. Use a network database, or explicitly enable local SQLite connectors for a private WebUI node.'
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


async def _load_connector(connector_id: str, ctx: QueryContext) -> InteractDataConnectorModel:
    if connector_id == 'webui_local':
        if not _is_admin(ctx):
            raise PermissionError('Only WebUI administrators can use the built-in local WebUI connector.')
        return _local_webui_connector()

    connector = await InteractDataConnectors.get_enabled_by_id(connector_id)
    if not connector:
        raise PermissionError('Data connector not found or disabled.')
    _assert_not_internal_connector(connector)
    return connector


def _connector_context_allowed(connector: InteractDataConnectorModel, ctx: QueryContext) -> bool:
    if connector.id != 'webui_local':
        if not connector.allowed_model_ids:
            return False
        if not ctx.model_id or ctx.model_id not in connector.allowed_model_ids:
            return False
    elif connector.allowed_model_ids and (not ctx.model_id or ctx.model_id not in connector.allowed_model_ids):
        return False
    if connector.allowed_channel_ids and (not ctx.channel_id or ctx.channel_id not in connector.allowed_channel_ids):
        return False
    if connector.access_mode == 'company_admins':
        return _is_admin(ctx)
    if connector.access_mode == 'selected_members':
        return bool(ctx.company_member_id and ctx.company_member_id in connector.allowed_member_ids)
    return True


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

        if op == '$contains':
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
    connection = sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True, timeout=connector.query_timeout_seconds)
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
                    'columns': [
                        {'name': row['name'], 'type': row['type']}
                        for row in rows
                        if row['name'] in visible
                    ],
                }
            )
    return schemas


def _sqlite_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _sqlite_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_quote_sqlite_identifier, filters)
    sql = (
        f'SELECT {", ".join(_quote_sqlite_identifier(column) for column in selected_columns)} '
        f'FROM {_quote_sqlite_table(table)}{where_sql} LIMIT ?'
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
    query['options'] = f'{query.get("options", "")} -c statement_timeout={connector.query_timeout_seconds * 1000}'.strip()
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
                        'columns': [
                            {'name': row[0], 'type': row[1]}
                            for row in rows
                            if row[0] in visible
                        ],
                    }
                )
    return schemas


def _pg_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _pg_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_pg_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    sql = (
        f'SELECT {", ".join(_pg_quote_identifier(column) for column in selected_columns)} '
        f'FROM {_pg_table_sql(table)}{where_sql} LIMIT %s'
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
                        'columns': [
                            {'name': row[0], 'type': row[1]}
                            for row in rows
                            if row[0] in visible
                        ],
                    }
                )
    return schemas


def _mysql_query(
    connector: InteractDataConnectorModel,
    table: str,
    columns: list[str] | None,
    filters: dict[str, Any] | None,
    limit: int,
) -> dict[str, Any]:
    table = _normalize_table(table)
    _check_table_policy(connector, table)
    available_columns = _mysql_columns(connector, table)
    selected_columns = _allowed_output_columns(connector, table, columns, available_columns)
    filter_columns = _filter_columns(filters)
    _allowed_output_columns(connector, table, filter_columns, available_columns)
    capped_limit = max(1, min(int(limit or 20), connector.max_rows))
    where_sql, values = _build_where_sql(_mysql_quote_identifier, filters)
    where_sql = where_sql.replace('?', '%s')
    sql = (
        f'SELECT {", ".join(_mysql_quote_identifier(column) for column in selected_columns)} '
        f'FROM {_mysql_table_sql(connector, table)}{where_sql} LIMIT %s'
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

    :param connector_id: Data connector id from the company portal. Use webui_local only for WebUI admin diagnostics.
    :param table: Optional table name to inspect.
    :return: JSON with visible tables and columns.
    """
    ctx = _context(__user__, __metadata__)
    try:
        connector = await _load_connector(connector_id, ctx)
        if not _connector_context_allowed(connector, ctx):
            raise PermissionError('This user/model/channel is not allowed to use the data connector.')
        if connector.connector_type == 'sqlite':
            schemas = await asyncio.to_thread(_sqlite_schema, connector, table)
        elif connector.connector_type in {'postgres', 'postgresql'}:
            schemas = await asyncio.to_thread(_pg_schema, connector, table)
        elif connector.connector_type in {'mysql', 'mariadb'}:
            schemas = await asyncio.to_thread(_mysql_schema, connector, table)
        else:
            raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
        await _record_event(connector.id, ctx, table, 'schema', row_count=0)
        return _json_result({'ok': True, 'connector_id': connector.id, 'name': connector.name, 'schemas': schemas})
    except Exception as error:
        await _record_event(connector_id, ctx, table, 'error', error=str(error))
        return _json_result({'ok': False, 'error': str(error)})


async def interact_database_query(
    connector_id: str = 'webui_local',
    table: str = '',
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Query rows from an authorized Interact Vision data connector using a safe read-only builder.
    This tool does not accept raw SQL. Use interact_database_schema first when unsure.

    :param connector_id: Data connector id from the company portal. Use webui_local only for WebUI admin diagnostics.
    :param table: Allowed table name to read.
    :param columns: Optional list of columns to return. Connector policy is always enforced.
    :param filters: Optional equality/range filters, e.g. {"email": {"$contains": "@example.com"}, "role": "user"}.
    :param limit: Maximum rows to return, capped by connector policy.
    :return: JSON with rows and query metadata.
    """
    ctx = _context(__user__, __metadata__)
    row_count = 0
    try:
        connector = await _load_connector(connector_id, ctx)
        if not _connector_context_allowed(connector, ctx):
            raise PermissionError('This user/model/channel is not allowed to use the data connector.')
        if connector.allow_write:
            raise PermissionError('Write-enabled connectors are not executable by this read-only tool.')
        if connector.connector_type == 'sqlite':
            result = await asyncio.to_thread(_sqlite_query, connector, table, columns, filters, limit)
        elif connector.connector_type in {'postgres', 'postgresql'}:
            result = await asyncio.to_thread(_pg_query, connector, table, columns, filters, limit)
        elif connector.connector_type in {'mysql', 'mariadb'}:
            result = await asyncio.to_thread(_mysql_query, connector, table, columns, filters, limit)
        else:
            raise ValueError(f'Connector type is not executable by this WebUI node: {connector.connector_type}')
        row_count = int(result.get('row_count') or 0)
        await _record_event(connector.id, ctx, table, 'success', row_count=row_count)
        return _json_result({'ok': True, **result})
    except Exception as error:
        await _record_event(connector_id, ctx, table, 'error', row_count=row_count, error=str(error))
        return _json_result({'ok': False, 'error': str(error)})
