from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.interact_semantic import InteractSemantic, _business_day_start, _synced_description
from open_webui.routers import interact_semantic as semantic_router
from open_webui.routers.interact_channels import _safe_sso_return_url, _safe_sso_target
from open_webui.routers.interact_semantic import (
    BulkCatalogAuthorizationRequest,
    BulkCatalogObjectRequest,
    CatalogPermissionChangeRequest,
    DatasetImportRequest,
)
from open_webui.semantic_query import entitlements
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.schema import _scoped_schema_descriptions


def test_business_day_uses_taipei_midnight_by_default(monkeypatch):
    monkeypatch.delenv('INTERACT_SEMANTIC_DAY_OFFSET_SECONDS', raising=False)
    now = 1_720_000_000
    day_start = _business_day_start(now)
    assert (day_start + 8 * 3600) % 86400 == 0
    assert 0 <= now - day_start < 86400


def test_schema_comments_refresh_without_overwriting_catalog_override():
    assert _synced_description('Old database comment', 'Old database comment', 'New database comment') == (
        'New database comment'
    )
    assert _synced_description('Administrator definition', 'Old database comment', 'New database comment') == (
        'Administrator definition'
    )
    assert _synced_description(None, None, 'Initial database comment') == 'Initial database comment'


def test_schema_descriptions_are_limited_to_connector_visible_tables():
    scanned = {
        'schema_descriptions': {
            'sales': 'Sales records.',
            'payroll': 'Restricted payroll records.',
        }
    }
    tables = [{'name': 'sales.orders', 'schema': 'sales'}]

    assert _scoped_schema_descriptions(scanned, tables) == {'sales': 'Sales records.'}


def test_sso_navigation_allowlists_targets_and_return_hosts():
    assert _safe_sso_target('/workspace/data-connectors/connector-1')
    assert _safe_sso_target('/workflows/workflow-1/edit')
    assert _safe_sso_target('//evil.example') is None
    assert _safe_sso_target('/admin/settings') is None
    assert _safe_sso_return_url('https://interact-vision.com.tw/company-portal/data-connectors')
    assert _safe_sso_return_url('https://evil.example/company-portal') is None


@pytest.mark.asyncio
async def test_dataset_limit_is_enforced(monkeypatch):
    async def fake_entitlements(_company_user_id):
        return {'operational': True, 'limits': {'datasets': 2}}

    async def fake_reserve(_company_user_id, _limit):
        return False, 2

    monkeypatch.setattr(entitlements, 'semantic_entitlements', fake_entitlements)
    monkeypatch.setattr(InteractSemantic, 'reserve_dataset_slot', fake_reserve)
    with pytest.raises(SemanticQueryError) as raised:
        await entitlements.enforce_dataset_limit('company-1')
    assert raised.value.code == 'SEMANTIC-DATASET-LIMIT'


@pytest.mark.asyncio
async def test_daily_query_limit_is_enforced(monkeypatch):
    async def fake_entitlements(_company_user_id):
        return {'operational': True, 'limits': {'dailyQueries': 10}}

    async def fake_reserve(_company_user_id, _limit):
        return False, 10

    monkeypatch.setattr(entitlements, 'semantic_entitlements', fake_entitlements)
    monkeypatch.setattr(InteractSemantic, 'reserve_daily_query', fake_reserve)
    with pytest.raises(SemanticQueryError) as raised:
        await entitlements.reserve_query_entitlement('company-1')
    assert raised.value.code == 'QUERY-DAILY-LIMIT'


@pytest.mark.asyncio
async def test_bulk_catalog_update_is_scoped_to_connector_company_and_snapshot(monkeypatch):
    calls = {}

    async def fake_require_admin(user):
        assert user.id == 'admin-1'
        return 'company-1'

    async def fake_require_connector(connector_id, company_user_id):
        calls['connector_scope'] = (connector_id, company_user_id)

    async def fake_update(connector_id, company_user_id, snapshot_id, object_ids, enabled, actor):
        calls['update'] = (connector_id, company_user_id, snapshot_id, object_ids, enabled, actor)
        return {'status': 'updated', 'snapshot_id': snapshot_id, 'updated_count': len(object_ids)}

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'set_catalog_objects_enabled', fake_update)

    result = await semantic_router.patch_catalog_objects(
        'connector-1',
        BulkCatalogObjectRequest(snapshot_id='snapshot-2', object_ids=['table-1', 'table-2'], enabled=True),
        SimpleNamespace(id='admin-1'),
    )

    assert calls['connector_scope'] == ('connector-1', 'company-1')
    assert calls['update'] == (
        'connector-1',
        'company-1',
        'snapshot-2',
        ['table-1', 'table-2'],
        True,
        'admin-1',
    )
    assert result['updated_count'] == 2


@pytest.mark.asyncio
async def test_bulk_catalog_update_rejects_stale_snapshot(monkeypatch):
    async def fake_require_admin(_user):
        return 'company-1'

    async def fake_require_connector(_connector_id, _company_user_id):
        return None

    async def fake_update(*_args):
        return {'status': 'stale_snapshot', 'snapshot_id': 'snapshot-3', 'updated_count': 0}

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'set_catalog_objects_enabled', fake_update)

    with pytest.raises(HTTPException) as raised:
        await semantic_router.patch_catalog_objects(
            'connector-1',
            BulkCatalogObjectRequest(snapshot_id='snapshot-2', object_ids=['table-1'], enabled=True),
            SimpleNamespace(id='admin-1'),
        )

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_bulk_authorization_preview_is_company_connector_snapshot_scoped(monkeypatch):
    calls = {}

    async def fake_require_admin(_user):
        return 'company-1'

    async def fake_require_connector(connector_id, company_user_id):
        calls['scope'] = (connector_id, company_user_id)

    async def fake_bulk(*args):
        calls['bulk'] = args
        return {
            'status': 'preview',
            'snapshot_id': 'snapshot-2',
            'object_count': 2,
            'field_count': 8,
            'affected_datasets': [],
        }

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'bulk_catalog_authorization', fake_bulk)

    result = await semantic_router.bulk_catalog_authorization(
        'connector-1',
        BulkCatalogAuthorizationRequest(
            snapshot_id='snapshot-2',
            object_ids=['table-1', 'table-2'],
            authorized=True,
        ),
        SimpleNamespace(id='admin-1'),
    )

    assert calls['scope'] == ('connector-1', 'company-1')
    assert calls['bulk'] == (
        'connector-1',
        'company-1',
        'snapshot-2',
        ['table-1', 'table-2'],
        True,
        'admin-1',
        False,
    )
    assert result['field_count'] == 8


@pytest.mark.asyncio
async def test_bulk_authorization_requires_explicit_revoke_acknowledgement(monkeypatch):
    async def fake_require_admin(_user):
        return 'company-1'

    async def fake_require_connector(_connector_id, _company_user_id):
        return None

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)

    with pytest.raises(HTTPException) as raised:
        await semantic_router.bulk_catalog_authorization(
            'connector-1',
            BulkCatalogAuthorizationRequest(
                snapshot_id='snapshot-2',
                authorized=False,
                apply=True,
                acknowledge_impact=False,
            ),
            SimpleNamespace(id='admin-1'),
        )

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_field_revoke_blocks_only_dependent_published_datasets(monkeypatch):
    async def fake_require_admin(_user):
        return 'company-1'

    async def fake_update(*_args):
        return {
            'id': 'field-1',
            'connector_id': 'connector-1',
            'object_physical_name': 'crm.orders',
            'physical_name': 'amount',
        }

    async def fake_mark(*args, **kwargs):
        assert args == ('connector-1', 'company-1', 'blocked')
        assert kwargs == {'impacted_fields': {'crm.orders.amount'}}
        return 2

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(InteractSemantic, 'update_catalog_field', fake_update)
    monkeypatch.setattr(InteractSemantic, 'mark_connector_drift', fake_mark)

    result = await semantic_router.patch_catalog_field(
        'field-1',
        {'readable': False},
        SimpleNamespace(id='admin-1'),
    )

    assert result['affectedDatasets'] == 2


@pytest.mark.asyncio
async def test_dataset_dependencies_include_root_object_and_measure_filter_fields():
    version = SimpleNamespace(
        snapshot_id='snapshot-1',
        definition={
            'rootObjectId': 'orders',
            'relationshipIds': [],
            'dimensions': [],
            'measures': [
                {
                    'fieldId': 'orders.amount',
                    'filters': [{'fieldId': 'orders.status', 'operator': 'eq', 'value': 'won'}],
                }
            ],
        },
    )
    objects = [SimpleNamespace(id='orders', physical_name='crm.orders')]
    fields = [
        SimpleNamespace(id='orders.amount', catalog_object_id='orders', physical_name='amount'),
        SimpleNamespace(id='orders.status', catalog_object_id='orders', physical_name='status'),
    ]

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class FakeDb:
        def __init__(self):
            self.results = iter([objects, fields])

        async def get(self, _model, _identifier):
            return version

        async def execute(self, _statement):
            return FakeResult(next(self.results))

    physical_objects, physical_fields = await InteractSemantic._dataset_physical_dependencies(
        FakeDb(),
        SimpleNamespace(current_version_id='version-1'),
        'connector-1',
        'company-1',
    )

    assert physical_objects == {'crm.orders'}
    assert physical_fields == {'crm.orders.amount', 'crm.orders.status'}


@pytest.mark.asyncio
async def test_dataset_import_is_company_connector_scoped_and_read_only(monkeypatch):
    calls = {}

    async def fake_require_admin(user):
        assert user.id == 'admin-1'
        return 'company-1'

    async def fake_require_connector(connector_id, company_user_id):
        calls['scope'] = (connector_id, company_user_id)

    async def fake_catalog(connector_id, company_user_id):
        calls['catalog'] = (connector_id, company_user_id)
        return {'snapshotId': 'snapshot-1', 'objects': [], 'relationships': []}

    def fake_resolve(document, catalog, connector_id):
        calls['resolve'] = (document, catalog['snapshotId'], connector_id)
        return {'ok': False, 'errors': [{'code': 'EXPECTED'}], 'warnings': [], 'dataset': None}

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'catalog', fake_catalog)
    monkeypatch.setattr(semantic_router, 'resolve_portable_dataset', fake_resolve)

    result = await semantic_router.import_dataset_document(
        'connector-1',
        DatasetImportRequest(document={'format': 'interact-semantic-dataset'}),
        SimpleNamespace(id='admin-1'),
    )

    assert calls['scope'] == ('connector-1', 'company-1')
    assert calls['catalog'] == ('connector-1', 'company-1')
    assert calls['resolve'] == (
        {'format': 'interact-semantic-dataset'},
        'snapshot-1',
        'connector-1',
    )
    assert result['ok'] is False


@pytest.mark.asyncio
async def test_ai_schema_handoff_export_is_company_connector_scoped(monkeypatch):
    calls = {}

    async def fake_require_admin(user):
        assert user.id == 'admin-1'
        return 'company-1'

    async def fake_require_connector(connector_id, company_user_id):
        calls['scope'] = (connector_id, company_user_id)
        return SimpleNamespace(name='Scheduling', connector_type='postgresql')

    async def fake_catalog(connector_id, company_user_id):
        calls['catalog'] = (connector_id, company_user_id)
        return {'snapshotId': 'snapshot-9', 'objects': [], 'relationships': []}

    async def fake_snapshot(snapshot_id, company_user_id):
        calls['snapshot'] = (snapshot_id, company_user_id)
        return {'version': 9, 'fingerprint': 'fingerprint-9'}

    def fake_build(catalog, connector, snapshot):
        calls['build'] = (catalog['snapshotId'], connector, snapshot['version'])
        return {'format': 'interact-semantic-schema-handoff', 'version': 1}

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'catalog', fake_catalog)
    monkeypatch.setattr(InteractSemantic, 'get_snapshot', fake_snapshot)
    monkeypatch.setattr(semantic_router, 'build_schema_handoff', fake_build)

    result = await semantic_router.export_ai_schema_handoff(
        'connector-1',
        SimpleNamespace(id='admin-1'),
    )

    assert calls['scope'] == ('connector-1', 'company-1')
    assert calls['catalog'] == ('connector-1', 'company-1')
    assert calls['snapshot'] == ('snapshot-9', 'company-1')
    assert calls['build'] == (
        'snapshot-9',
        {'name': 'Scheduling', 'connectorType': 'postgresql'},
        9,
    )
    assert result['document']['format'] == 'interact-semantic-schema-handoff'


@pytest.mark.asyncio
async def test_reviewed_field_permission_change_requires_latest_scoped_snapshot(monkeypatch):
    calls = {}

    async def fake_require_admin(_user):
        return 'company-1'

    async def fake_require_connector(connector_id, company_user_id):
        calls['scope'] = (connector_id, company_user_id)

    async def fake_catalog(_connector_id, _company_user_id):
        return {
            'snapshotId': 'snapshot-current',
            'objects': [
                {
                    'id': 'object-1',
                    'fields': [{'id': 'field-1', 'groupable': False}],
                }
            ],
        }

    async def fake_update(field_id, company_user_id, patch, actor):
        calls['update'] = (field_id, company_user_id, patch, actor)
        return {'id': field_id, **patch}

    monkeypatch.setattr(semantic_router, '_require_data_connector_company_admin', fake_require_admin)
    monkeypatch.setattr(semantic_router, '_require_company_connector', fake_require_connector)
    monkeypatch.setattr(InteractSemantic, 'catalog', fake_catalog)
    monkeypatch.setattr(InteractSemantic, 'update_catalog_field', fake_update)

    payload = CatalogPermissionChangeRequest(
        snapshot_id='snapshot-current',
        target_type='field',
        object_id='object-1',
        field_id='field-1',
        permission='groupable',
        desired=True,
    )
    result = await semantic_router.apply_catalog_permission_change(
        'connector-1',
        payload,
        SimpleNamespace(id='admin-1'),
    )

    assert calls['scope'] == ('connector-1', 'company-1')
    assert calls['update'] == ('field-1', 'company-1', {'groupable': True}, 'admin-1')
    assert result['value']['groupable'] is True

    with pytest.raises(HTTPException) as raised:
        await semantic_router.apply_catalog_permission_change(
            'connector-1',
            payload.model_copy(update={'snapshot_id': 'snapshot-stale'}),
            SimpleNamespace(id='admin-1'),
        )
    assert raised.value.status_code == 409
