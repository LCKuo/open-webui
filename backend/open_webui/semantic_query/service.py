from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from open_webui.models.groups import Groups
from open_webui.models.interact_data_connectors import (
    InteractDataConnectorModel,
    InteractDataConnectors,
)
from open_webui.models.interact_semantic import InteractSemantic, canonical_fingerprint
from open_webui.semantic_query.compiler import SemanticCompiler
from open_webui.semantic_query.contracts import QueryPlan, QueryRuntimeContext
from open_webui.semantic_query.entitlements import reserve_query_entitlement
from open_webui.semantic_query.errors import ERROR_MESSAGES, SemanticQueryError
from open_webui.semantic_query.telemetry import record_query_event
from open_webui.tools.interact_database import (
    _assert_not_internal_connector,
    _database_error_code,
    _mssql_connect,
    _mysql_connect,
    _pg_connect,
    _sqlite_connect,
)

MAX_RESULT_BYTES = 1_000_000
MAX_JOIN_COUNT = 5
ALLOWED_AGGREGATIONS = {'sum', 'count', 'count_distinct', 'avg', 'min', 'max'}
NUMERIC_SEMANTIC_TYPES = {'number', 'money', 'currency', 'integer', 'decimal', 'float', 'percentage'}
NUMERIC_PHYSICAL_TYPES = {
    'bigint',
    'bigserial',
    'dec',
    'decimal',
    'double',
    'double precision',
    'fixed',
    'float',
    'float4',
    'float8',
    'int',
    'int2',
    'int4',
    'int8',
    'integer',
    'mediumint',
    'money',
    'numeric',
    'number',
    'real',
    'serial',
    'smallint',
    'smallmoney',
    'smallserial',
    'tinyint',
}
ALLOWED_ACCESS_MODES = {
    'company_admins',
    'selected_members',
    'all_company_members',
    'selected_channels',
}


@dataclass
class SemanticRuntime:
    dataset: dict[str, Any]
    version: dict[str, Any]
    connector: InteractDataConnectorModel
    catalog: dict[str, Any]
    row_filters: list[dict[str, Any]]
    policy_decision: dict[str, Any]


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _validation_error_detail(error: ValidationError) -> str:
    return '; '.join(
        f'{".".join(str(part) for part in item.get("loc") or [])}: {item.get("type") or "invalid"}'
        for item in error.errors(include_input=False, include_url=False)
    )


def _metadata_value(metadata: dict[str, Any], *keys: str) -> Any:
    sources = [metadata]
    for container in ('interact_company', 'interact_channel', 'workflow'):
        nested = metadata.get(container)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for key in keys:
            if source.get(key) is not None:
                return source[key]
    return None


async def runtime_context(
    user: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> QueryRuntimeContext:
    user = user or {}
    metadata = metadata or {}
    user_id = _text(user.get('id'))
    supplied_groups = _metadata_value(metadata, 'group_ids', 'groupIds')
    if isinstance(supplied_groups, list):
        group_ids = [str(item) for item in supplied_groups if str(item).strip()]
    elif user_id:
        group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id)]
    else:
        group_ids = []
    company_user_id = (
        _text(user.get('companyUserId'))
        or _text(user.get('company_user_id'))
        or _text(_metadata_value(metadata, 'companyUserId', 'company_user_id'))
    )
    if not company_user_id:
        raise SemanticQueryError('DB-COMPANY-CONTEXT-MISSING', 'Company context is required.')
    return QueryRuntimeContext(
        user_id=user_id,
        user_role=_text(user.get('role')),
        company_user_id=company_user_id,
        company_member_id=(
            _text(user.get('companyMemberId'))
            or _text(user.get('company_member_id'))
            or _text(_metadata_value(metadata, 'companyMemberId', 'company_member_id'))
        ),
        company_member_role=(
            _text(user.get('companyMemberRole'))
            or _text(user.get('company_member_role'))
            or _text(_metadata_value(metadata, 'companyMemberRole', 'company_member_role'))
        ),
        group_ids=group_ids,
        model_id=_text(_metadata_value(metadata, 'model_id', 'modelId')),
        channel_id=_text(_metadata_value(metadata, 'channel_id', 'channelId')),
        channel_source=_text(_metadata_value(metadata, 'channel_source', 'channelSource', 'source')),
        workflow_id=_text(_metadata_value(metadata, 'workflow_id', 'workflowId')),
        external_user_id=_text(_metadata_value(metadata, 'external_user_id', 'externalUserId')),
        request_id=_text(_metadata_value(metadata, 'request_id', 'requestId')),
    )


def _company_admin(context: QueryRuntimeContext) -> bool:
    if context.external_user_id:
        return False
    if context.user_role == 'admin':
        return True
    if not context.company_member_id:
        return True
    return (context.company_member_role or '').lower() in {'owner', 'admin'}


def _scope_allowed(allowed_ids: list[str], value: str | None) -> bool:
    return not allowed_ids or bool(value and value in allowed_ids)


def _principal_allowed(
    access_mode: str,
    allowed_member_ids: list[str],
    allowed_group_ids: list[str],
    context: QueryRuntimeContext,
) -> bool:
    if context.external_user_id:
        return access_mode == 'selected_channels'
    if access_mode == 'selected_channels':
        return context.channel_source == 'query_lab' and _company_admin(context)
    if access_mode == 'company_admins':
        return _company_admin(context)
    if access_mode == 'all_company_members':
        return True
    if access_mode == 'selected_members':
        return bool(
            (context.company_member_id and context.company_member_id in allowed_member_ids)
            or set(context.group_ids).intersection(allowed_group_ids)
        )
    return False


def _connector_allowed(connector: InteractDataConnectorModel, context: QueryRuntimeContext) -> None:
    if not connector.enabled:
        raise SemanticQueryError('DB-CONNECTOR-DISABLED')
    if connector.company_user_id != context.company_user_id:
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED', 'Connector belongs to another company.')
    if connector.allow_write:
        raise SemanticQueryError('QUERY-EXECUTION-FAILED', 'Write-enabled connectors cannot run semantic queries.')
    if not connector.allowed_model_ids or not _scope_allowed(connector.allowed_model_ids, context.model_id):
        raise SemanticQueryError('DB-MODEL-NOT-ALLOWED')
    if context.external_user_id:
        if not context.channel_id or context.channel_id not in connector.allowed_channel_ids:
            raise SemanticQueryError('DB-CHANNEL-NOT-ALLOWED')
    else:
        if context.channel_id and not _scope_allowed(connector.allowed_channel_ids, context.channel_id):
            raise SemanticQueryError('DB-CHANNEL-NOT-ALLOWED')
        if not _principal_allowed(
            connector.access_mode,
            connector.allowed_member_ids,
            connector.allowed_group_ids,
            context,
        ):
            raise SemanticQueryError('DB-USER-NOT-ALLOWED')
    _assert_not_internal_connector(connector)


def _dataset_allowed(dataset: dict[str, Any], context: QueryRuntimeContext) -> None:
    if dataset['company_user_id'] != context.company_user_id:
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED', 'Dataset belongs to another company.')
    if dataset.get('status') == 'blocked':
        raise SemanticQueryError('SEMANTIC-MODEL-DRIFT-BLOCKED')
    if dataset.get('status') not in {'published', 'degraded'} or not dataset.get('current_version_id'):
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-PUBLISHED')
    access_mode = dataset.get('access_mode') or 'company_admins'
    allowed_channels = dataset.get('allowed_channel_ids') or []
    if context.external_user_id:
        if not context.channel_id or context.channel_id not in allowed_channels:
            raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED', 'The channel is not explicitly allowed.')
    elif not _principal_allowed(
        access_mode,
        dataset.get('allowed_member_ids') or [],
        dataset.get('allowed_group_ids') or [],
        context,
    ):
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED')
    if context.channel_id and allowed_channels and context.channel_id not in allowed_channels:
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED', 'The channel is not explicitly allowed.')
    for key, value in (
        ('allowed_model_ids', context.model_id),
        ('allowed_workflow_ids', context.workflow_id),
    ):
        allowed = dataset.get(key) or []
        if allowed and (not value or value not in allowed):
            raise SemanticQueryError('SEMANTIC-DATASET-NOT-ALLOWED', f'{key} rejected the request context.')


def _catalog_maps(catalog: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    objects = {item['id']: item for item in catalog.get('objects') or []}
    fields = {
        field['id']: {
            **field,
            'objectId': item['id'],
            'objectEnabled': item.get('enabled', False),
        }
        for item in catalog.get('objects') or []
        for field in item.get('fields') or []
    }
    return objects, fields


def _field_supports_arithmetic(field: dict[str, Any]) -> bool:
    semantic_type = str(field.get('semantic_type') or '').strip().lower()
    if semantic_type in NUMERIC_SEMANTIC_TYPES:
        return True
    physical_type = str(field.get('physical_type') or '').strip().lower()
    base_type = physical_type.split('(', 1)[0].strip()
    return base_type in NUMERIC_PHYSICAL_TYPES or base_type.startswith(('decimal ', 'numeric ', 'number ', 'float '))


def _policy_items(policy: dict[str, list[str]], physical_name: str) -> list[str]:
    return policy.get(physical_name) or policy.get(physical_name.split('.')[-1]) or []


def _enforce_connector_catalog_policy(
    connector: InteractDataConnectorModel,
    catalog: dict[str, Any],
    definition: dict[str, Any],
) -> None:
    objects, fields = _catalog_maps(catalog)
    field_ids = {
        str(item.get('fieldId') or '')
        for section in ('dimensions', 'measures')
        for item in definition.get(section) or []
    }
    relationship_ids = set(definition.get('relationshipIds') or [])
    for relationship in catalog.get('relationships') or []:
        if relationship['id'] not in relationship_ids:
            continue
        for pair in relationship.get('join_pairs') or []:
            field_ids.update((str(pair.get('leftFieldId') or ''), str(pair.get('rightFieldId') or '')))
    for field_id in field_ids:
        field = fields.get(field_id)
        if not field:
            raise SemanticQueryError('SEMANTIC-MODEL-DRIFT-BLOCKED', 'Published field is missing.')
        obj = objects[field['objectId']]
        table = obj['physical_name']
        aliases = {table, table.split('.')[-1]}
        if not connector.allowed_tables or not aliases.intersection(connector.allowed_tables):
            raise SemanticQueryError(
                'SEMANTIC-FIELD-NOT-ALLOWED',
                f'Table is no longer allowed: {table}',
            )
        if aliases.intersection(connector.blocked_tables):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Table is blocked: {table}')
        allowed_columns = _policy_items(connector.allowed_columns, table)
        blocked_columns = _policy_items(connector.blocked_columns, table)
        if allowed_columns and field['physical_name'] not in allowed_columns:
            raise SemanticQueryError(
                'SEMANTIC-FIELD-NOT-ALLOWED',
                'Field was removed from connector policy.',
            )
        if field['physical_name'] in blocked_columns:
            raise SemanticQueryError(
                'SEMANTIC-FIELD-NOT-ALLOWED',
                'Field is blocked by connector policy.',
            )


def _relationship_path_count(
    root_id: str,
    target_id: str,
    relationships: list[dict[str, Any]],
) -> int:
    if root_id == target_id:
        return 1
    graph: dict[str, set[str]] = {}
    for relationship in relationships:
        left = str(relationship.get('left_object_id') or '')
        right = str(relationship.get('right_object_id') or '')
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    queue = [(root_id, 0)]
    distances = {root_id: 0}
    counts = {root_id: 1}
    while queue:
        node, distance = queue.pop(0)
        for neighbor in graph.get(node, set()):
            next_distance = distance + 1
            if neighbor not in distances:
                distances[neighbor] = next_distance
                counts[neighbor] = counts[node]
                queue.append((neighbor, next_distance))
            elif distances[neighbor] == next_distance:
                counts[neighbor] = min(2, counts[neighbor] + counts[node])
    return counts.get(target_id, 0)


def validate_dataset_definition(  # noqa: C901
    dataset: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    definition = dataset.get('definition') or dataset.get('draft_definition') or {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    objects, fields = _catalog_maps(catalog)

    snapshot_id = str(definition.get('snapshotId') or '')
    if not snapshot_id or snapshot_id != str(catalog.get('snapshotId') or ''):
        errors.append({'code': 'SCHEMA-SNAPSHOT-NOT-READY', 'path': 'definition.snapshotId'})
    root_id = str(definition.get('rootObjectId') or '')
    if root_id not in objects or not objects[root_id].get('enabled'):
        errors.append({'code': 'SEMANTIC-FIELD-NOT-ALLOWED', 'path': 'definition.rootObjectId'})

    semantic_ids: set[str] = set()
    semantic_objects: dict[str, str] = {}
    for section, requirement in (
        ('dimensions', 'groupable'),
        ('measures', 'aggregatable'),
    ):
        for index, item in enumerate(definition.get(section) or []):
            semantic_id = str(item.get('id') or '')
            field = fields.get(str(item.get('fieldId') or ''))
            if not semantic_id or semantic_id in semantic_ids:
                errors.append({'code': 'QUERY-PLAN-INVALID', 'path': f'definition.{section}.{index}.id'})
            semantic_ids.add(semantic_id)
            if not field or not field.get('objectEnabled') or not field.get('readable') or not field.get(requirement):
                errors.append({'code': 'SEMANTIC-FIELD-NOT-ALLOWED', 'path': f'definition.{section}.{index}.fieldId'})
            elif semantic_id:
                semantic_objects[semantic_id] = field['objectId']
            if section == 'measures':
                aggregation = str(item.get('aggregation') or '').lower()
                aggregation_path = f'definition.measures.{index}.aggregation'
                if aggregation not in ALLOWED_AGGREGATIONS:
                    errors.append({'code': 'QUERY-PLAN-INVALID', 'path': aggregation_path})
                elif field and aggregation in {'sum', 'avg'} and not _field_supports_arithmetic(field):
                    errors.append({'code': 'QUERY-TYPE-MISMATCH', 'path': aggregation_path})

    measure_ids = {str(item.get('id')) for item in definition.get('measures') or []}
    for index, metric in enumerate(definition.get('metrics') or []):
        metric_id = str(metric.get('id') or '')
        if not metric_id or metric_id in semantic_ids:
            errors.append({'code': 'QUERY-PLAN-INVALID', 'path': f'definition.metrics.{index}.id'})
        semantic_ids.add(metric_id)
        expression = metric.get('expression') or {}
        refs = [expression.get(key) for key in ('measureId', 'leftMeasureId', 'rightMeasureId') if expression.get(key)]
        if not refs or any(str(ref) not in measure_ids for ref in refs):
            errors.append({'code': 'QUERY-PLAN-INVALID', 'path': f'definition.metrics.{index}.expression'})

    relation_map = {item['id']: item for item in catalog.get('relationships') or []}
    selected_relationships: list[dict[str, Any]] = []
    for index, relationship_id in enumerate(definition.get('relationshipIds') or []):
        relationship = relation_map.get(str(relationship_id))
        if not relationship or relationship.get('status') != 'confirmed':
            errors.append({'code': 'SEMANTIC-RELATIONSHIP-MISSING', 'path': f'definition.relationshipIds.{index}'})
        else:
            selected_relationships.append(relationship)
            if relationship.get('fanout_risk') == 'high':
                warnings.append({'code': 'QUERY-FANOUT-RISK', 'path': f'definition.relationshipIds.{index}'})

    for semantic_id, object_id in semantic_objects.items():
        path_count = _relationship_path_count(root_id, object_id, selected_relationships)
        if path_count == 0:
            errors.append({'code': 'SEMANTIC-RELATIONSHIP-MISSING', 'path': f'definition.fields.{semantic_id}'})
        elif path_count > 1:
            errors.append({'code': 'SEMANTIC-RELATIONSHIP-AMBIGUOUS', 'path': f'definition.fields.{semantic_id}'})

    if dataset.get('access_mode') not in ALLOWED_ACCESS_MODES:
        errors.append({'code': 'SEMANTIC-DATASET-NOT-ALLOWED', 'path': 'access_mode'})
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}


def _policy_applies(policy: dict[str, Any], context: QueryRuntimeContext) -> bool:
    principal_type = policy.get('principal_type')
    ids = policy.get('principal_ids') or []
    if principal_type == 'all':
        return True
    if principal_type == 'member':
        return bool(context.company_member_id and context.company_member_id in ids)
    if principal_type == 'group':
        return bool(set(context.group_ids).intersection(ids))
    if principal_type == 'channel':
        return bool(context.channel_id and context.channel_id in ids)
    if principal_type == 'model':
        return bool(context.model_id and context.model_id in ids)
    if principal_type == 'role':
        return bool(context.company_member_role and context.company_member_role in ids)
    return False


def _context_value(value: Any, context: QueryRuntimeContext) -> tuple[Any, bool]:
    if not isinstance(value, str) or not value.startswith('$context.'):
        return value, True
    attribute = {
        '$context.userId': 'user_id',
        '$context.user_id': 'user_id',
        '$context.companyMemberId': 'company_member_id',
        '$context.company_member_id': 'company_member_id',
        '$context.companyMemberRole': 'company_member_role',
        '$context.company_member_role': 'company_member_role',
        '$context.channelId': 'channel_id',
        '$context.channel_id': 'channel_id',
        '$context.modelId': 'model_id',
        '$context.model_id': 'model_id',
        '$context.workflowId': 'workflow_id',
        '$context.workflow_id': 'workflow_id',
        '$context.externalUserId': 'external_user_id',
        '$context.external_user_id': 'external_user_id',
        '$context.groupIds': 'group_ids',
        '$context.group_ids': 'group_ids',
    }.get(value)
    if not attribute:
        return None, False
    resolved = getattr(context, attribute)
    return resolved, resolved is not None and resolved != []


def resolve_row_policies(
    policies: list[dict[str, Any]],
    context: QueryRuntimeContext,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    applied: list[str] = []
    active_policies = [policy for policy in policies if policy.get('status') in {'active', 'published'}]
    matching_policies = [policy for policy in active_policies if _policy_applies(policy, context)]
    if active_policies and not matching_policies:
        raise SemanticQueryError(
            'SEMANTIC-DATASET-NOT-ALLOWED',
            'No active row policy matches the trusted request principal.',
        )
    for policy in matching_policies:
        expression = policy.get('expression') or {}
        conditions = expression.get('conditions') if isinstance(expression, dict) else None
        if not isinstance(conditions, list) or (expression.get('operator') or 'and') != 'and':
            raise SemanticQueryError('QUERY-PLAN-INVALID', 'Row policies support an AND condition group only.')
        for condition in conditions:
            if not isinstance(condition, dict):
                raise SemanticQueryError('QUERY-PLAN-INVALID', 'Invalid row policy condition.')
            resolved, ok = _context_value(condition.get('value'), context)
            if not ok:
                if policy.get('deny_if_unresolved', True):
                    raise SemanticQueryError('ROW-POLICY-CONTEXT-MISSING')
                continue
            filters.append({**condition, 'value': resolved})
        applied.append(policy['id'])
    return filters, {'allowed': True, 'rowPolicyIds': applied, 'filterCount': len(filters)}


async def load_runtime(plan: QueryPlan, context: QueryRuntimeContext) -> SemanticRuntime:
    dataset = await InteractSemantic.get_dataset(plan.datasetId)
    if not dataset:
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-FOUND')
    _dataset_allowed(dataset, context)
    version = await InteractSemantic.get_dataset_version(dataset['current_version_id'])
    if not version:
        raise SemanticQueryError('SEMANTIC-DATASET-NOT-PUBLISHED')
    if plan.datasetVersion is not None and plan.datasetVersion != version['version']:
        raise SemanticQueryError('SEMANTIC-MODEL-DRIFT-BLOCKED', 'Requested dataset version is not current.')
    connector = await InteractDataConnectors.get_by_id(dataset['connector_id'])
    if not connector:
        raise SemanticQueryError('DB-CONNECTOR-DISABLED')
    _connector_allowed(connector, context)
    catalog = await InteractSemantic.catalog(connector.id, context.company_user_id, version['snapshot_id'])
    validation = validate_dataset_definition({**dataset, 'definition': version['definition']}, catalog)
    if not validation['ok']:
        raise SemanticQueryError('SEMANTIC-MODEL-DRIFT-BLOCKED', json.dumps(validation['errors']))
    _enforce_connector_catalog_policy(connector, catalog, version['definition'])
    policies = await InteractSemantic.list_policies(dataset['id'], context.company_user_id)
    row_filters, policy_decision = resolve_row_policies(policies, context)
    return SemanticRuntime(dataset, version, connector, catalog, row_filters, policy_decision)


def _mask_value(value: Any, rule: str) -> Any:
    if value is None or rule == 'none':
        return value
    text = str(value)
    if rule == 'redact':
        return '[REDACTED]'
    if rule == 'last4':
        return ('*' * max(0, len(text) - 4)) + text[-4:]
    if rule == 'email':
        local, separator, domain = text.partition('@')
        return f'{local[:1]}***{separator}{domain}' if separator else '[REDACTED]'
    if rule == 'hash':
        return canonical_fingerprint(text)[:16]
    return '[REDACTED]'


def _mask_rows(
    rows: list[dict[str, Any]], selected: list[dict[str, Any]], runtime: SemanticRuntime
) -> list[dict[str, Any]]:
    _, catalog_fields = _catalog_maps(runtime.catalog)
    definition = runtime.version['definition']
    semantic_fields = {
        item['id']: catalog_fields.get(item.get('fieldId'))
        for section in ('dimensions', 'measures')
        for item in definition.get(section) or []
    }
    rules = {item['id']: (semantic_fields.get(item['id']) or {}).get('masking_rule', 'none') for item in selected}
    return [{key: _mask_value(value, rules.get(key, 'none')) for key, value in row.items()} for row in rows]


def _execute_sync(connector: InteractDataConnectorModel, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
    connector_type = connector.connector_type
    connection = None
    cursor = None
    try:
        if connector_type == 'sqlite':
            connection = _sqlite_connect(connector)
            deadline = time.monotonic() + connector.query_timeout_seconds
            connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            cursor = connection.execute(sql, parameters)
            return [dict(row) for row in cursor.fetchall()]
        if connector_type in {'postgres', 'postgresql'}:
            connection = _pg_connect(connector)
            connection.autocommit = False
            cursor = connection.cursor()
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f'{connector.query_timeout_seconds * 1000}ms',),
            )
        elif connector_type in {'mysql', 'mariadb'}:
            connection = _mysql_connect(connector)
            cursor = connection.cursor()
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
            cursor.execute('SET SESSION max_execution_time=%s', (connector.query_timeout_seconds * 1000,))
        elif connector_type == 'mssql':
            connection = _mssql_connect(connector)
            cursor = connection.cursor()
            cursor.timeout = connector.query_timeout_seconds
        else:
            raise SemanticQueryError('QUERY-EXECUTION-FAILED', f'Unsupported connector type: {connector_type}')
        cursor.execute(sql, parameters)
        names = [str(item[0]) for item in cursor.description or []]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
            connection.close()


async def validate_query(
    plan_data: dict[str, Any],
    context: QueryRuntimeContext,
    *,
    include_sql_preview: bool = False,
) -> dict[str, Any]:
    try:
        plan = QueryPlan.model_validate(plan_data)
    except ValidationError as error:
        raise SemanticQueryError('QUERY-PLAN-INVALID', _validation_error_detail(error)) from error
    runtime = await load_runtime(plan, context)
    compiler = SemanticCompiler(runtime.connector.connector_type, runtime.catalog, runtime.version['definition'])
    compiled = compiler.compile(plan, runtime.row_filters)
    if len(compiled.joins) > MAX_JOIN_COUNT:
        raise SemanticQueryError('QUERY-COST-LIMIT', 'Query contains too many joins.')
    if plan.limit > runtime.connector.max_rows:
        raise SemanticQueryError('QUERY-COST-LIMIT', 'Requested row limit exceeds connector policy.')
    result = {
        'ok': True,
        'plan': plan.model_dump(mode='json'),
        'dataset': {
            'id': runtime.dataset['id'],
            'name': runtime.dataset['name'],
            'version': runtime.version['version'],
        },
        'compiled': compiled.model_dump(exclude={'sql', 'parameters'}),
        'policyDecision': runtime.policy_decision,
        'estimatedCost': {
            'joinCount': len(compiled.joins),
            'resultRowLimit': plan.limit,
            'timeoutSeconds': runtime.connector.query_timeout_seconds,
        },
    }
    if include_sql_preview:
        result['sqlPreview'] = compiled.sql
    return result


def _redact_plan(plan_data: dict[str, Any]) -> dict[str, Any]:
    try:
        redacted = json.loads(json.dumps(plan_data, default=str))
    except (TypeError, ValueError):
        return {'unavailable': True}
    if isinstance(redacted, dict):
        allowed = {
            'version',
            'datasetId',
            'datasetVersion',
            'measures',
            'metrics',
            'dimensions',
            'filters',
            'timeRange',
            'timeGrain',
            'orderBy',
            'limit',
            'includeTotals',
            'intentSummary',
        }
        redacted = {key: value for key, value in redacted.items() if key in allowed}
    filters = redacted.get('filters') if isinstance(redacted, dict) else None
    if isinstance(filters, dict):
        for condition in filters.get('conditions') or []:
            if isinstance(condition, dict) and 'value' in condition:
                value = condition['value']
                condition['value'] = {
                    'redacted': True,
                    'valueType': 'list' if isinstance(value, list) else type(value).__name__,
                }
    return redacted


async def execute_query(  # noqa: C901
    plan_data: dict[str, Any], context: QueryRuntimeContext
) -> dict[str, Any]:
    request_id = context.request_id or str(uuid4())
    started = time.monotonic()
    event: dict[str, Any] = {
        'request_id': request_id,
        'company_user_id': context.company_user_id,
        'user_id': context.user_id,
        'company_member_id': context.company_member_id,
        'model_id': context.model_id,
        'channel_id': context.channel_id,
        'workflow_id': context.workflow_id,
        'status': 'error',
        'plan_redacted': _redact_plan(plan_data),
        'plan_fingerprint': canonical_fingerprint(plan_data),
    }
    try:
        await reserve_query_entitlement(context.company_user_id)
        plan = QueryPlan.model_validate(plan_data)
        runtime = await load_runtime(plan, context)
        event.update(
            connector_id=runtime.connector.id,
            connector_type=runtime.connector.connector_type,
            dataset_id=runtime.dataset['id'],
            dataset_version_id=runtime.version['id'],
            intent_summary=plan.intentSummary,
            policy_decision=runtime.policy_decision,
        )
        if plan.limit > runtime.connector.max_rows:
            raise SemanticQueryError('QUERY-COST-LIMIT', 'Requested row limit exceeds connector policy.')
        compiler = SemanticCompiler(runtime.connector.connector_type, runtime.catalog, runtime.version['definition'])
        compiled = compiler.compile(plan, runtime.row_filters)
        if len(compiled.joins) > MAX_JOIN_COUNT:
            raise SemanticQueryError('QUERY-COST-LIMIT', 'Query contains too many joins.')
        rows = await asyncio.wait_for(
            asyncio.to_thread(_execute_sync, runtime.connector, compiled.sql, compiled.parameters),
            timeout=max(1, runtime.connector.query_timeout_seconds + 2),
        )
        rows = _mask_rows(rows, compiled.selectedFields, runtime)
        totals = None
        if plan.includeTotals and (plan.measures or plan.metrics):
            totals_plan = plan.model_copy(deep=True)
            totals_plan.dimensions = []
            totals_plan.orderBy = []
            totals_plan.timeGrain = None
            totals_plan.limit = 1
            totals_compiler = SemanticCompiler(
                runtime.connector.connector_type,
                runtime.catalog,
                runtime.version['definition'],
            )
            totals_query = totals_compiler.compile(totals_plan, runtime.row_filters)
            total_rows = await asyncio.wait_for(
                asyncio.to_thread(
                    _execute_sync,
                    runtime.connector,
                    totals_query.sql,
                    totals_query.parameters,
                ),
                timeout=max(1, runtime.connector.query_timeout_seconds + 2),
            )
            total_rows = _mask_rows(total_rows, totals_query.selectedFields, runtime)
            totals = total_rows[0] if total_rows else {}
        encoded = json.dumps(
            {'rows': rows, 'totals': totals},
            ensure_ascii=False,
            default=str,
        ).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            raise SemanticQueryError('QUERY-RESULT-LIMIT')
        duration_ms = int((time.monotonic() - started) * 1000)
        event.update(
            status='success',
            compiled_query_hash=compiled.queryHash,
            row_count=len(rows),
            duration_ms=duration_ms,
        )
        return {
            'ok': True,
            'requestId': request_id,
            'dataset': {
                'id': runtime.dataset['id'],
                'name': runtime.dataset['name'],
                'version': runtime.version['version'],
            },
            'fields': compiled.selectedFields,
            'rows': rows,
            'rowCount': len(rows),
            'totals': totals,
            'truncated': len(rows) >= plan.limit,
            'durationMs': duration_ms,
            'warnings': compiled.warnings,
        }
    except ValidationError as error:
        semantic_error = SemanticQueryError(
            'QUERY-PLAN-INVALID',
            _validation_error_detail(error),
            request_id=request_id,
        )
        event.update(error_code=semantic_error.code, error_detail=semantic_error.detail)
        raise semantic_error
    except TimeoutError as error:
        semantic_error = SemanticQueryError(
            'DB-TIMEOUT',
            'Query timed out.',
            retryable=True,
            request_id=request_id,
        )
        event.update(error_code=semantic_error.code, error_detail=semantic_error.detail)
        raise semantic_error from error
    except SemanticQueryError as error:
        error.request_id = request_id
        event.update(error_code=error.code, error_detail=error.detail)
        raise
    except Exception as error:
        database_code = _database_error_code(error)
        code = database_code if database_code in ERROR_MESSAGES else 'QUERY-EXECUTION-FAILED'
        semantic_error = SemanticQueryError(
            code,
            f'Database driver error: {type(error).__name__}',
            retryable=code in {'DB-CONNECTION-FAILED', 'DB-TIMEOUT'},
            request_id=request_id,
        )
        event.update(error_code=semantic_error.code, error_detail=semantic_error.detail)
        raise semantic_error from error
    finally:
        event.setdefault('connector_id', 'unknown')
        event['duration_ms'] = event.get('duration_ms') or int((time.monotonic() - started) * 1000)
        try:
            await InteractSemantic.record_event(event)
        except Exception:
            pass
        try:
            record_query_event(event)
        except Exception:
            pass


def _search_units(value: str) -> set[str]:
    normalized = value.lower().strip()
    tokens = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', normalized))
    cjk = ''.join(re.findall(r'[\u4e00-\u9fff]', normalized))
    tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {item for item in tokens if item}


def _manifest_score(query: str, manifest: dict[str, Any]) -> float:
    if not query.strip():
        return 0.0
    searchable = json.dumps(manifest, ensure_ascii=False).lower()
    normalized_query = query.lower().strip()
    score = 12.0 if normalized_query in searchable else 0.0
    query_units = _search_units(normalized_query)
    if not query_units:
        return score
    searchable_units = _search_units(searchable)
    overlap = query_units.intersection(searchable_units)
    score += len(overlap) * 2.0
    score += len(overlap) / len(query_units)
    name = str(manifest.get('name') or '').lower()
    if any(unit in name for unit in query_units):
        score += 3.0
    return round(score, 4)


async def accessible_dataset_manifests(context: QueryRuntimeContext, query: str | None = None) -> list[dict[str, Any]]:
    datasets = await InteractSemantic.list_datasets(context.company_user_id, published_only=True)
    needle = (query or '').strip().lower()
    manifests: list[dict[str, Any]] = []
    for dataset in datasets:
        try:
            _dataset_allowed(dataset, context)
            version = await InteractSemantic.get_dataset_version(dataset['current_version_id'])
            connector = await InteractDataConnectors.get_by_id(dataset['connector_id'])
            if not version or not connector:
                continue
            _connector_allowed(connector, context)
        except SemanticQueryError:
            continue
        definition = version['definition']
        manifest = {
            'datasetId': dataset['id'],
            'name': dataset['name'],
            'description': dataset['description'],
            'businessDomain': dataset.get('business_domain'),
            'version': version['version'],
            'status': dataset.get('status'),
            'grain': definition.get('grain') or definition.get('rootObjectLabel'),
            'whenToUse': definition.get('whenToUse') or definition.get('usageGuidance'),
            'notFor': definition.get('notFor') or [],
            'examples': definition.get('examples') or [],
            'synonyms': definition.get('synonyms') or [],
            'dimensions': [
                {'id': item.get('id'), 'name': item.get('name'), 'description': item.get('description')}
                for item in definition.get('dimensions') or []
            ],
            'measures': [
                {'id': item.get('id'), 'name': item.get('name'), 'description': item.get('description')}
                for item in definition.get('measures') or []
            ],
            'metrics': [
                {'id': item.get('id'), 'name': item.get('name'), 'description': item.get('description')}
                for item in definition.get('metrics') or []
            ],
        }
        manifest['selectorScore'] = _manifest_score(needle, manifest)
        manifests.append(manifest)
    manifests.sort(key=lambda item: (-float(item['selectorScore']), str(item['name']).lower()))
    return manifests[:20]
