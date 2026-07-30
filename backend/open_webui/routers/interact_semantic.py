from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from open_webui.models.groups import Groups
from open_webui.models.interact_data_connectors import InteractDataConnectors
from open_webui.models.interact_semantic import InteractSemantic
from open_webui.models.models import Models
from open_webui.routers.interact_channels import (
    _require_company_connector,
    _require_data_connector_company_admin,
    _require_service_token,
)
from open_webui.semantic_query.contracts import FilterGroup, QueryRuntimeContext
from open_webui.semantic_query.dataset_import import resolve_portable_dataset
from open_webui.semantic_query.entitlements import enforce_dataset_limit
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.schema import scan_connector_snapshot, schema_scan_error
from open_webui.semantic_query.schema import snapshot_diff as _snapshot_diff
from open_webui.semantic_query.schema_handoff import build_schema_handoff
from open_webui.semantic_query.service import (
    _enforce_connector_catalog_policy,
    accessible_dataset_manifests,
    execute_query,
    validate_dataset_definition,
    validate_query,
)
from open_webui.utils.access_control import check_model_access
from open_webui.utils.auth import get_verified_user
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from pydantic import BaseModel, Field

router = APIRouter()


class ScanRequest(BaseModel):
    max_tables: int = Field(default=200, ge=1, le=500)


class BulkCatalogObjectRequest(BaseModel):
    snapshot_id: str = Field(..., min_length=1, max_length=200)
    object_ids: list[str] = Field(..., min_length=1, max_length=500)
    enabled: bool


class BulkCatalogAuthorizationRequest(BaseModel):
    snapshot_id: str = Field(..., min_length=1, max_length=200)
    object_ids: list[str] = Field(default_factory=list, max_length=500)
    authorized: bool
    apply: bool = False
    acknowledge_impact: bool = False


class DatasetImportRequest(BaseModel):
    document: dict[str, Any]


class CatalogPermissionChangeRequest(BaseModel):
    snapshot_id: str = Field(..., min_length=1, max_length=200)
    target_type: Literal['object', 'field']
    object_id: str = Field(..., min_length=1, max_length=200)
    field_id: str | None = Field(default=None, min_length=1, max_length=200)
    permission: Literal['enabled', 'readable', 'filterable', 'groupable', 'aggregatable']
    desired: bool


class DatasetRequest(BaseModel):
    id: str | None = None
    connector_id: str
    slug: str = Field(..., min_length=1, max_length=120, pattern=r'^[a-z0-9][a-z0-9_-]*$')
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    business_domain: str | None = Field(default=None, max_length=200)
    definition: dict[str, Any] = Field(default_factory=dict)
    access_mode: Literal[
        'company_admins',
        'selected_members',
        'all_company_members',
        'selected_channels',
    ] = 'company_admins'
    allowed_member_ids: list[str] = Field(default_factory=list, max_length=500)
    allowed_group_ids: list[str] = Field(default_factory=list, max_length=500)
    allowed_model_ids: list[str] = Field(default_factory=list, max_length=200)
    allowed_channel_ids: list[str] = Field(default_factory=list, max_length=200)
    allowed_workflow_ids: list[str] = Field(default_factory=list, max_length=200)


class RelationshipRequest(BaseModel):
    id: str | None = None
    connector_id: str | None = None
    left_object_id: str
    right_object_id: str
    relationship_type: Literal['one_to_one', 'one_to_many', 'many_to_one', 'many_to_many']
    join_type: Literal['left', 'inner'] = 'left'
    join_pairs: list[dict[str, str]] = Field(min_length=1, max_length=8)
    source: Literal['foreign_key', 'admin_defined'] = 'admin_defined'
    status: Literal['suggested', 'confirmed', 'rejected'] = 'confirmed'
    fanout_risk: Literal['none', 'low', 'high'] = 'low'
    description: str | None = Field(default=None, max_length=2000)


class RowPolicyRequest(BaseModel):
    id: str | None = None
    name: str = Field(..., min_length=1, max_length=200)
    status: Literal['draft', 'active', 'disabled'] = 'draft'
    principal_type: Literal['all', 'member', 'group', 'channel', 'model', 'role']
    principal_ids: list[str] = Field(default_factory=list, max_length=500)
    expression: FilterGroup
    deny_if_unresolved: bool = True


class CatalogSearchRequest(BaseModel):
    query: str = Field(default='', max_length=500)
    modelId: str | None = Field(default=None, max_length=200)


def _http_error(error: SemanticQueryError) -> HTTPException:
    denied = error.code.endswith('NOT-ALLOWED') or error.code in {
        'DB-COMPANY-CONTEXT-MISSING',
        'DB-CONNECTOR-DISABLED',
    }
    unavailable = error.code in {'DB-CONNECTION-FAILED', 'DB-TIMEOUT'}
    quota_exceeded = error.code in {'QUERY-DAILY-LIMIT', 'SEMANTIC-DATASET-LIMIT'}
    return HTTPException(
        status_code=(
            status.HTTP_429_TOO_MANY_REQUESTS
            if quota_exceeded
            else status.HTTP_403_FORBIDDEN
            if denied or error.code == 'SEMANTIC-PLAN-INACTIVE'
            else status.HTTP_503_SERVICE_UNAVAILABLE
            if unavailable or error.code == 'SEMANTIC-ENTITLEMENT-UNAVAILABLE'
            else status.HTTP_400_BAD_REQUEST
        ),
        detail=error.public(),
    )


async def _runtime_context_for_user(user: Any) -> QueryRuntimeContext:
    if not is_billing_enabled():
        raise HTTPException(status_code=503, detail='Interact company identity is not configured.')
    identity = await InteractBillingClient().resolve_identity(user)
    company_user_id = str((identity.company_user or {}).get('id') or '').strip()
    if not company_user_id:
        raise HTTPException(status_code=403, detail='Unable to resolve company account.')
    member = identity.company_member or {}
    groups = await Groups.get_groups_by_member_id(user.id)
    return QueryRuntimeContext(
        user_id=user.id,
        user_role=user.role,
        company_user_id=company_user_id,
        company_member_id=str(member.get('id') or '').strip() or None,
        company_member_role=str(member.get('role') or '').strip() or None,
        group_ids=[group.id for group in groups],
    )


async def _complete_query_lab_context(
    plan: dict[str, Any],
    context: QueryRuntimeContext,
) -> QueryRuntimeContext:
    dataset_id = str(plan.get('datasetId') or '').strip()
    dataset = await InteractSemantic.get_dataset(dataset_id, context.company_user_id)
    if not dataset:
        return context
    connector = await InteractDataConnectors.get_by_id(dataset['connector_id'])
    if not connector:
        return context
    model_candidates = connector.allowed_model_ids
    if dataset.get('allowed_model_ids'):
        model_candidates = [item for item in model_candidates if item in dataset['allowed_model_ids']]
    context.model_id = model_candidates[0] if model_candidates else None
    if dataset.get('allowed_channel_ids'):
        channel_candidates = dataset['allowed_channel_ids']
        if connector.allowed_channel_ids:
            channel_candidates = [item for item in channel_candidates if item in connector.allowed_channel_ids]
        context.channel_id = channel_candidates[0] if channel_candidates else None
        context.channel_source = 'query_lab'
    if dataset.get('allowed_workflow_ids'):
        context.workflow_id = dataset['allowed_workflow_ids'][0]
    return context


async def _apply_verified_model_context(
    user: Any,
    context: QueryRuntimeContext,
    model_id: str | None,
) -> QueryRuntimeContext:
    clean_model_id = str(model_id or '').strip()
    if not clean_model_id:
        return context
    model = await Models.get_model_by_id(clean_model_id)
    if not model:
        raise HTTPException(status_code=403, detail='Model not found.')
    await check_model_access(user, model)
    context.model_id = clean_model_id
    return context


def _validate_relationship_fields(catalog: dict[str, Any], payload: RelationshipRequest) -> None:
    fields_by_object = {
        item['id']: {field['id'] for field in item.get('fields') or []} for item in catalog.get('objects') or []
    }
    if payload.left_object_id not in fields_by_object or payload.right_object_id not in fields_by_object:
        raise HTTPException(status_code=400, detail='Relationship objects must belong to this connector snapshot.')
    if any(
        pair.get('leftFieldId') not in fields_by_object[payload.left_object_id]
        or pair.get('rightFieldId') not in fields_by_object[payload.right_object_id]
        for pair in payload.join_pairs
    ):
        raise HTTPException(status_code=400, detail='Join fields must belong to their declared relationship objects.')


@router.get('/admin/semantic-governance')
async def semantic_governance(
    company_user_id: str = Query(..., min_length=1, max_length=200),
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    connectors = await InteractDataConnectors.list_by_company_user_id(company_user_id)
    summary = await InteractSemantic.governance_summary(company_user_id)
    datasets = summary['datasets']
    dataset_counts: dict[str, dict[str, int]] = {}
    for dataset in datasets:
        counts = dataset_counts.setdefault(dataset['connector_id'], {'total': 0, 'published': 0, 'blocked': 0})
        counts['total'] += 1
        if dataset['status'] in {'published', 'degraded'}:
            counts['published'] += 1
        if dataset['status'] == 'blocked':
            counts['blocked'] += 1

    connector_rows = []
    for connector in connectors:
        snapshot = summary['latestSnapshots'].get(connector.id)
        counts = dataset_counts.get(connector.id, {'total': 0, 'published': 0, 'blocked': 0})
        has_credentials = bool(connector.password or connector.connection_string)
        connection_configured = bool(connector.connection_string or connector.host)
        if not connector.enabled:
            health = 'disabled'
        elif not connection_configured:
            health = 'credentials_required'
        elif snapshot and snapshot.get('status') == 'failed':
            health = 'degraded'
        elif counts['blocked']:
            health = 'attention'
        elif not snapshot:
            health = 'scan_required'
        else:
            health = 'healthy'
        connector_rows.append(
            {
                'id': connector.id,
                'name': connector.name,
                'connectorType': connector.connector_type,
                'enabled': connector.enabled,
                'health': health,
                'hasCredentials': has_credentials,
                'connectionConfigured': connection_configured,
                'schema': {
                    'status': snapshot.get('status') if snapshot else 'not_scanned',
                    'version': snapshot.get('version') if snapshot else None,
                    'scannedAt': snapshot.get('completed_at') if snapshot else None,
                    'errorCode': snapshot.get('error_code') if snapshot else None,
                },
                'datasets': counts,
                'acl': {
                    'accessMode': connector.access_mode,
                    'memberCount': len(connector.allowed_member_ids),
                    'groupCount': len(connector.allowed_group_ids),
                    'modelIds': connector.allowed_model_ids,
                    'channelIds': connector.allowed_channel_ids,
                },
                'usage30d': summary['connectorUsage30d'].get(
                    connector.id,
                    {
                        'total': 0,
                        'success': 0,
                        'denied': 0,
                        'timeout': 0,
                        'failed': 0,
                        'successRate': None,
                        'deniedRate': None,
                        'averageDurationMs': 0,
                        'lastQueryAt': None,
                    },
                ),
            }
        )

    return {
        'ok': True,
        'companyUserId': company_user_id,
        'generatedAt': summary['generatedAt'],
        'connectors': connector_rows,
        'datasets': [
            {
                'id': item['id'],
                'connectorId': item['connector_id'],
                'name': item['name'],
                'description': item['description'],
                'businessDomain': item['business_domain'],
                'status': item['status'],
                'accessMode': item['access_mode'],
                'currentVersionId': item['current_version_id'],
                'allowedModelIds': item['allowed_model_ids'],
                'allowedChannelIds': item['allowed_channel_ids'],
                'allowedWorkflowIds': item['allowed_workflow_ids'],
                'updatedAt': item['updated_at'],
            }
            for item in datasets
        ],
        'usageToday': summary['usageToday'],
        'usage30d': summary['usage30d'],
    }


@router.post('/data-connectors/{connector_id}/schema-scans')
async def create_schema_scan(
    connector_id: str,
    payload: ScanRequest | None = None,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    connector = await _require_company_connector(connector_id, company_user_id)
    if not await InteractSemantic.claim_schema_scan(
        connector_id,
        company_user_id,
        int(time.time()),
        force=True,
    ):
        raise HTTPException(status_code=409, detail='This connector already has a schema scan in progress.')
    try:
        result = await scan_connector_snapshot(connector, user.id, (payload or ScanRequest()).max_tables)
        await InteractSemantic.finish_schema_scan(connector_id, success=True)
        return result
    except Exception as error:
        log.exception('Semantic schema scan failed for connector %s', connector_id)
        safe_error = schema_scan_error(error)
        await InteractSemantic.finish_schema_scan(
            connector_id,
            success=False,
            error=f'{safe_error.code}: {safe_error.public()["message"]}',
        )
        raise _http_error(safe_error) from error


@router.get('/data-connectors/{connector_id}/schema-snapshots')
async def list_schema_snapshots(connector_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    snapshots = await InteractSemantic.list_snapshots(connector_id, company_user_id)
    for snapshot in snapshots:
        snapshot.pop('schema_json', None)
    return {'ok': True, 'snapshots': snapshots}


@router.get('/data-connectors/{connector_id}/schema-snapshots/{snapshot_id}')
async def get_schema_snapshot(connector_id: str, snapshot_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    snapshot = await InteractSemantic.get_snapshot(snapshot_id, company_user_id)
    if not snapshot or snapshot['connector_id'] != connector_id:
        raise HTTPException(status_code=404, detail='Schema snapshot not found.')
    return {'ok': True, 'snapshot': snapshot}


@router.get('/data-connectors/{connector_id}/schema-diff')
async def schema_diff(
    connector_id: str,
    from_snapshot_id: str = Query(alias='from'),
    to_snapshot_id: str = Query(alias='to'),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    before = await InteractSemantic.get_snapshot(from_snapshot_id, company_user_id)
    after = await InteractSemantic.get_snapshot(to_snapshot_id, company_user_id)
    if not before or not after or before['connector_id'] != connector_id or after['connector_id'] != connector_id:
        raise HTTPException(status_code=404, detail='Schema snapshot not found.')
    return {'ok': True, 'diff': _snapshot_diff(before['schema_json'], after['schema_json'])}


@router.get('/data-connectors/{connector_id}/catalog')
async def get_catalog(
    connector_id: str,
    snapshot_id: str | None = None,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    return {'ok': True, 'catalog': await InteractSemantic.catalog(connector_id, company_user_id, snapshot_id)}


@router.get('/data-connectors/{connector_id}/semantic-datasets/ai-schema-handoff')
async def export_ai_schema_handoff(connector_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    connector = await _require_company_connector(connector_id, company_user_id)
    catalog = await InteractSemantic.catalog(connector_id, company_user_id)
    snapshot_id = catalog.get('snapshotId')
    if not snapshot_id:
        raise HTTPException(status_code=409, detail='請先完成 Schema 掃描，再匯出 AI 交接包。')
    snapshot = await InteractSemantic.get_snapshot(snapshot_id, company_user_id)
    document = build_schema_handoff(
        catalog,
        {'name': connector.name, 'connectorType': connector.connector_type},
        snapshot,
    )
    return {'ok': True, 'document': document}


@router.patch('/catalog/objects/{object_id}')
async def patch_catalog_object(
    object_id: str,
    patch: dict[str, Any] = Body(...),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    result = await InteractSemantic.update_catalog_object(object_id, company_user_id, patch, user.id)
    if not result:
        raise HTTPException(status_code=404, detail='Catalog object not found.')
    affected = 0
    if patch.get('enabled') is False:
        affected = await InteractSemantic.mark_connector_drift(
            str(result['connector_id']),
            company_user_id,
            'blocked',
            impacted_objects={str(result['physical_name'])},
        )
    return {'ok': True, 'object': result, 'affectedDatasets': affected}


@router.patch('/data-connectors/{connector_id}/catalog/objects')
async def patch_catalog_objects(
    connector_id: str,
    payload: BulkCatalogObjectRequest,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    impacted_objects: set[str] = set()
    if not payload.enabled:
        catalog = await InteractSemantic.catalog(connector_id, company_user_id)
        selected_ids = set(payload.object_ids)
        impacted_objects = {
            str(item.get('physical_name') or '')
            for item in catalog.get('objects') or []
            if str(item.get('id')) in selected_ids
        }
    result = await InteractSemantic.set_catalog_objects_enabled(
        connector_id,
        company_user_id,
        payload.snapshot_id,
        payload.object_ids,
        payload.enabled,
        user.id,
    )
    if result['status'] == 'stale_snapshot':
        raise HTTPException(
            status_code=409,
            detail='Schema 已更新，請重新載入最新資料表後再操作。',
        )
    if result['status'] == 'invalid_objects':
        raise HTTPException(
            status_code=400,
            detail='部分資料表不屬於目前 Connector 或 Schema snapshot。',
        )
    affected = 0
    if not payload.enabled and impacted_objects:
        affected = await InteractSemantic.mark_connector_drift(
            connector_id,
            company_user_id,
            'blocked',
            impacted_objects=impacted_objects,
        )
    return {'ok': True, **result, 'affectedDatasets': affected}


@router.post('/data-connectors/{connector_id}/catalog/authorization')
async def bulk_catalog_authorization(
    connector_id: str,
    payload: BulkCatalogAuthorizationRequest,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    if payload.apply and not payload.authorized and not payload.acknowledge_impact:
        raise HTTPException(status_code=400, detail='解除授權前必須確認對已發布資料集的影響。')
    result = await InteractSemantic.bulk_catalog_authorization(
        connector_id,
        company_user_id,
        payload.snapshot_id,
        payload.object_ids,
        payload.authorized,
        user.id,
        payload.apply,
    )
    if result['status'] == 'stale_snapshot':
        raise HTTPException(status_code=409, detail='Schema 已更新，請重新載入資料目錄後再操作。')
    if result['status'] == 'invalid_objects':
        raise HTTPException(status_code=400, detail='指定資料表不屬於目前 Connector 或 Schema。')
    return {'ok': True, **result}


@router.patch('/catalog/fields/{field_id}')
async def patch_catalog_field(
    field_id: str,
    patch: dict[str, Any] = Body(...),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    result = await InteractSemantic.update_catalog_field(field_id, company_user_id, patch, user.id)
    if not result:
        raise HTTPException(status_code=404, detail='Catalog field not found.')
    affected = 0
    permission_keys = {'readable', 'filterable', 'groupable', 'aggregatable'}
    if any(patch.get(key) is False for key in permission_keys):
        affected = await InteractSemantic.mark_connector_drift(
            str(result['connector_id']),
            company_user_id,
            'blocked',
            impacted_fields={f'{result["object_physical_name"]}.{result["physical_name"]}'},
        )
    return {'ok': True, 'field': result, 'affectedDatasets': affected}


@router.post('/data-connectors/{connector_id}/catalog/permission-changes')
async def apply_catalog_permission_change(  # noqa: C901
    connector_id: str,
    payload: CatalogPermissionChangeRequest,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    catalog = await InteractSemantic.catalog(connector_id, company_user_id)
    if payload.snapshot_id != catalog.get('snapshotId'):
        raise HTTPException(status_code=409, detail='Schema 已更新，請重新檢查匯入 JSON 後再審核權限。')
    objects = {str(item.get('id')): item for item in catalog.get('objects') or []}
    obj = objects.get(payload.object_id)
    if not obj:
        raise HTTPException(status_code=404, detail='目前 Schema 找不到指定資料表。')
    if payload.target_type == 'object':
        if payload.permission != 'enabled' or payload.field_id:
            raise HTTPException(status_code=400, detail='資料表只能變更 enabled 權限。')
        result = await InteractSemantic.update_catalog_object(
            payload.object_id,
            company_user_id,
            {'enabled': payload.desired},
            user.id,
        )
        if not result:
            raise HTTPException(status_code=409, detail='資料表權限狀態已變更，請重新檢查。')
        affected = 0
        if not payload.desired:
            affected = await InteractSemantic.mark_connector_drift(
                connector_id,
                company_user_id,
                'blocked',
                impacted_objects={str(obj.get('physical_name') or '')},
            )
        return {'ok': True, 'target': 'object', 'value': result, 'affectedDatasets': affected}
    if payload.permission == 'enabled' or not payload.field_id:
        raise HTTPException(status_code=400, detail='欄位權限變更需要 field_id，且不能使用 enabled。')
    field = next(
        (item for item in obj.get('fields') or [] if str(item.get('id')) == payload.field_id),
        None,
    )
    if not field:
        raise HTTPException(status_code=404, detail='目前 Schema 找不到指定欄位。')
    result = await InteractSemantic.update_catalog_field(
        payload.field_id,
        company_user_id,
        {payload.permission: payload.desired},
        user.id,
    )
    if not result:
        raise HTTPException(status_code=409, detail='欄位權限狀態已變更，請重新檢查。')
    affected = 0
    if not payload.desired:
        affected = await InteractSemantic.mark_connector_drift(
            connector_id,
            company_user_id,
            'blocked',
            impacted_fields={f'{obj.get("physical_name")}.{field.get("physical_name")}'},
        )
    return {'ok': True, 'target': 'field', 'value': result, 'affectedDatasets': affected}


@router.put('/data-connectors/{connector_id}/relationships')
async def save_relationship(
    connector_id: str,
    payload: RelationshipRequest,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    catalog = await InteractSemantic.catalog(connector_id, company_user_id)
    data = payload.model_dump(exclude_none=True)
    _validate_relationship_fields(catalog, payload)
    result = await InteractSemantic.upsert_relationship(company_user_id, connector_id, data, user.id)
    return {'ok': True, 'relationship': result}


@router.post('/catalog/relationships')
async def create_relationship(payload: RelationshipRequest, user=Depends(get_verified_user)):
    if not payload.connector_id:
        raise HTTPException(status_code=400, detail='connector_id is required.')
    return await save_relationship(payload.connector_id, payload, user)


@router.patch('/catalog/relationships/{relationship_id}')
async def patch_relationship(
    relationship_id: str,
    patch: dict[str, Any] = Body(...),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    existing = await InteractSemantic.get_relationship(relationship_id, company_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail='Catalog relationship not found.')
    allowed = {
        'relationship_type',
        'join_type',
        'join_pairs',
        'status',
        'fanout_risk',
        'description',
    }
    merged = RelationshipRequest.model_validate(
        {
            **existing,
            **{key: value for key, value in patch.items() if key in allowed},
            'connector_id': existing['connector_id'],
        }
    )
    catalog = await InteractSemantic.catalog(existing['connector_id'], company_user_id)
    _validate_relationship_fields(catalog, merged)
    result = await InteractSemantic.upsert_relationship(
        company_user_id,
        existing['connector_id'],
        {**merged.model_dump(exclude_none=True), 'id': relationship_id},
        user.id,
    )
    return {'ok': True, 'relationship': result}


@router.get('/semantic-datasets')
async def list_datasets(user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    return {'ok': True, 'datasets': await InteractSemantic.list_datasets(company_user_id)}


@router.post('/data-connectors/{connector_id}/semantic-datasets/import')
async def import_dataset_document(
    connector_id: str,
    payload: DatasetImportRequest,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(connector_id, company_user_id)
    if len(json.dumps(payload.document, ensure_ascii=False).encode('utf-8')) > 500_000:
        raise HTTPException(status_code=413, detail='Dataset import document is too large.')
    catalog = await InteractSemantic.catalog(connector_id, company_user_id)
    if not catalog.get('snapshotId'):
        raise HTTPException(status_code=409, detail='請先完成 Schema 掃描，再匯入語意資料集。')
    result = resolve_portable_dataset(payload.document, catalog, connector_id)
    return {'ok': result['ok'], 'import': result}


@router.get('/semantic-datasets/{dataset_id}')
async def get_dataset(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    dataset = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    if not dataset:
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    return {'ok': True, 'dataset': dataset}


@router.post('/semantic-datasets')
async def create_dataset(payload: DatasetRequest, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    await _require_company_connector(payload.connector_id, company_user_id)
    try:
        await enforce_dataset_limit(company_user_id)
    except SemanticQueryError as error:
        raise _http_error(error) from error
    try:
        result = await InteractSemantic.save_dataset(company_user_id, payload.model_dump(exclude_none=True), user.id)
        if not result:
            await InteractSemantic.release_dataset_slot(company_user_id)
    except Exception:
        await InteractSemantic.release_dataset_slot(company_user_id)
        raise
    return {'ok': True, 'dataset': result}


@router.put('/semantic-datasets/{dataset_id}')
async def update_dataset(dataset_id: str, payload: DatasetRequest, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    existing = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    if not existing or existing['connector_id'] != payload.connector_id:
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    result = await InteractSemantic.save_dataset(
        company_user_id,
        {**payload.model_dump(exclude_none=True), 'id': dataset_id},
        user.id,
    )
    return {'ok': True, 'dataset': result}


@router.patch('/semantic-datasets/{dataset_id}')
async def patch_dataset(
    dataset_id: str,
    patch: dict[str, Any] = Body(...),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    existing = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    if not existing:
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    allowed = {
        'slug',
        'name',
        'description',
        'business_domain',
        'definition',
        'access_mode',
        'allowed_member_ids',
        'allowed_group_ids',
        'allowed_model_ids',
        'allowed_channel_ids',
        'allowed_workflow_ids',
    }
    result = await InteractSemantic.save_dataset(
        company_user_id,
        {**{key: value for key, value in patch.items() if key in allowed}, 'id': dataset_id},
        user.id,
    )
    return {'ok': True, 'dataset': result}


@router.delete('/semantic-datasets/{dataset_id}')
async def delete_dataset(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.delete_dataset(dataset_id, company_user_id):
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    return {'ok': True, 'deleted': True}


@router.post('/semantic-datasets/{dataset_id}/validate')
async def validate_dataset(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    dataset = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    if not dataset:
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    catalog = await InteractSemantic.catalog(
        dataset['connector_id'], company_user_id, dataset['draft_definition'].get('snapshotId')
    )
    return {'ok': True, 'validation': validate_dataset_definition(dataset, catalog)}


@router.post('/semantic-datasets/{dataset_id}/publish')
async def publish_dataset(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    dataset = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    if not dataset:
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    catalog = await InteractSemantic.catalog(
        dataset['connector_id'], company_user_id, dataset['draft_definition'].get('snapshotId')
    )
    validation = validate_dataset_definition(dataset, catalog)
    if not validation['ok']:
        raise HTTPException(status_code=422, detail=validation)
    version = await InteractSemantic.publish_dataset(dataset_id, company_user_id, user.id, validation)
    return {'ok': True, 'version': version}


@router.get('/semantic-datasets/{dataset_id}/versions')
async def dataset_versions(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.get_dataset(dataset_id, company_user_id):
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    return {'ok': True, 'versions': await InteractSemantic.list_dataset_versions(dataset_id)}


@router.post('/semantic-datasets/{dataset_id}/versions/{version_id}/activate')
async def activate_dataset_version(
    dataset_id: str,
    version_id: str,
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    dataset = await InteractSemantic.get_dataset(dataset_id, company_user_id)
    version = await InteractSemantic.get_dataset_version(version_id)
    if not dataset or not version or version['dataset_id'] != dataset_id:
        raise HTTPException(status_code=404, detail='Semantic dataset version not found.')
    connector = await _require_company_connector(dataset['connector_id'], company_user_id)
    catalog = await InteractSemantic.catalog(dataset['connector_id'], company_user_id, version['snapshot_id'])
    validation = validate_dataset_definition(
        {**dataset, 'definition': version['definition']},
        catalog,
    )
    if not validation['ok']:
        raise HTTPException(status_code=422, detail=validation)
    try:
        _enforce_connector_catalog_policy(connector, catalog, version['definition'])
    except SemanticQueryError as error:
        raise _http_error(error) from error
    activated = await InteractSemantic.activate_dataset_version(
        dataset_id,
        version_id,
        company_user_id,
    )
    return {'ok': True, 'dataset': activated, 'version': version}


@router.post('/semantic-datasets/{dataset_id}/test-query')
async def test_dataset_query(
    dataset_id: str,
    payload: dict[str, Any] = Body(...),
    validate_only: bool = Query(default=False),
    user=Depends(get_verified_user),
):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.get_dataset(dataset_id, company_user_id):
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    plan = {**payload, 'datasetId': dataset_id}
    context = await _complete_query_lab_context(plan, await _runtime_context_for_user(user))
    try:
        return (
            await validate_query(plan, context, include_sql_preview=True)
            if validate_only
            else await execute_query(plan, context)
        )
    except SemanticQueryError as error:
        raise _http_error(error) from error


@router.get('/semantic-datasets/{dataset_id}/row-policies')
async def list_row_policies(dataset_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.get_dataset(dataset_id, company_user_id):
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    return {'ok': True, 'policies': await InteractSemantic.list_policies(dataset_id, company_user_id)}


@router.put('/semantic-datasets/{dataset_id}/row-policies')
async def save_row_policy(dataset_id: str, payload: RowPolicyRequest, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.get_dataset(dataset_id, company_user_id):
        raise HTTPException(status_code=404, detail='Semantic dataset not found.')
    result = await InteractSemantic.save_policy(
        company_user_id,
        dataset_id,
        payload.model_dump(exclude_none=True),
        user.id,
    )
    return {'ok': True, 'policy': result}


@router.delete('/semantic-datasets/{dataset_id}/row-policies/{policy_id}')
async def delete_row_policy(dataset_id: str, policy_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    if not await InteractSemantic.delete_policy(policy_id, company_user_id, dataset_id):
        raise HTTPException(status_code=404, detail='Row policy not found.')
    return {'ok': True, 'deleted': True}


@router.get('/semantic-datasets/accessible/manifests')
async def accessible_manifests(query: str | None = None, user=Depends(get_verified_user)):
    context = await _runtime_context_for_user(user)
    return {'ok': True, 'datasets': await accessible_dataset_manifests(context, query)}


@router.post('/semantic-query/validate')
async def validate_runtime_query(
    payload: dict[str, Any] = Body(...),
    model_id: str | None = Header(default=None, alias='X-OpenWebUI-Model-Id'),
    user=Depends(get_verified_user),
):
    context = await _runtime_context_for_user(user)
    context = await _apply_verified_model_context(user, context, model_id)
    try:
        return await validate_query(payload, context)
    except SemanticQueryError as error:
        raise _http_error(error) from error


@router.post('/semantic-query/plan/validate')
async def validate_runtime_query_contract(
    payload: dict[str, Any] = Body(...),
    model_id: str | None = Header(default=None, alias='X-OpenWebUI-Model-Id'),
    user=Depends(get_verified_user),
):
    return await validate_runtime_query(payload, model_id, user)


@router.post('/semantic-query/catalog-search')
async def catalog_search(payload: CatalogSearchRequest, user=Depends(get_verified_user)):
    context = await _runtime_context_for_user(user)
    context = await _apply_verified_model_context(user, context, payload.modelId)
    return {
        'ok': True,
        'datasets': await accessible_dataset_manifests(context, payload.query),
    }


@router.post('/semantic-query/execute')
async def execute_runtime_query(
    payload: dict[str, Any] = Body(...),
    model_id: str | None = Header(default=None, alias='X-OpenWebUI-Model-Id'),
    user=Depends(get_verified_user),
):
    context = await _runtime_context_for_user(user)
    context = await _apply_verified_model_context(user, context, model_id)
    try:
        return await execute_query(payload, context)
    except SemanticQueryError as error:
        raise _http_error(error) from error


@router.get('/semantic-query/events')
async def semantic_query_events(limit: int = 100, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    return {'ok': True, 'events': await InteractSemantic.list_events(company_user_id, limit)}


@router.get('/semantic-query/events/{request_id}')
async def semantic_query_event(request_id: str, user=Depends(get_verified_user)):
    company_user_id = await _require_data_connector_company_admin(user)
    event = await InteractSemantic.get_event_by_request_id(request_id, company_user_id)
    if not event:
        raise HTTPException(status_code=404, detail='Semantic query event not found.')
    return {'ok': True, 'event': event}
