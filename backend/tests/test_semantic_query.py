import sqlite3

import pytest
from open_webui.models.interact_data_connectors import InteractDataConnectorModel
from open_webui.models.interact_semantic import canonical_schema_fingerprint
from open_webui.routers.interact_semantic import RelationshipRequest, RowPolicyRequest, _snapshot_diff
from open_webui.routers.interact_semantic import router as semantic_router
from open_webui.semantic_query.compiler import SemanticCompiler
from open_webui.semantic_query.contracts import QueryPlan, QueryRuntimeContext
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.service import (
    SemanticRuntime,
    _connector_allowed,
    _dataset_allowed,
    _enforce_connector_catalog_policy,
    _execute_sync,
    _manifest_score,
    _manifest_semantic_field,
    _redact_plan,
    _validate_allowed_filter_values,
    execute_query,
    resolve_row_policies,
    validate_dataset_definition,
    validate_query,
)
from open_webui.tools.interact_database import (
    QueryContext,
    _connector_context_allowed,
    _load_connector,
    _resolve_default_connector,
)
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def allow_semantic_query_entitlement(monkeypatch):
    async def reserve(_company_user_id):
        return {'limit': 1000, 'used': 1}

    monkeypatch.setattr('open_webui.semantic_query.service.reserve_query_entitlement', reserve)


def catalog_fixture(relationship_type='many_to_one'):
    orders = {
        'id': 'orders',
        'physical_name': 'crm.orders',
        'display_name': 'Orders',
        'enabled': True,
        'fields': [
            {
                'id': 'orders.id',
                'physical_name': 'id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': True,
                'semantic_type': 'identifier',
                'masking_rule': 'none',
            },
            {
                'id': 'orders.salesperson_id',
                'physical_name': 'salesperson_id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
                'semantic_type': 'identifier',
                'masking_rule': 'none',
            },
            {
                'id': 'orders.amount',
                'physical_name': 'amount',
                'readable': True,
                'filterable': True,
                'groupable': False,
                'aggregatable': True,
                'semantic_type': 'money',
                'masking_rule': 'none',
            },
            {
                'id': 'orders.closed_at',
                'physical_name': 'closed_at',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
                'semantic_type': 'datetime',
                'masking_rule': 'none',
            },
        ],
    }
    employees = {
        'id': 'employees',
        'physical_name': 'crm.employees',
        'display_name': 'Employees',
        'enabled': True,
        'fields': [
            {
                'id': 'employees.id',
                'physical_name': 'id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': True,
                'semantic_type': 'identifier',
                'masking_rule': 'none',
            },
            {
                'id': 'employees.name',
                'physical_name': 'name',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
                'semantic_type': 'string',
                'masking_rule': 'none',
            },
        ],
    }
    return {
        'snapshotId': 'snapshot-1',
        'objects': [orders, employees],
        'relationships': [
            {
                'id': 'orders_employee',
                'left_object_id': 'orders',
                'right_object_id': 'employees',
                'relationship_type': relationship_type,
                'join_type': 'left',
                'join_pairs': [{'leftFieldId': 'orders.salesperson_id', 'rightFieldId': 'employees.id'}],
                'status': 'confirmed',
                'fanout_risk': 'low',
            }
        ],
    }


def definition_fixture():
    return {
        'snapshotId': 'snapshot-1',
        'rootObjectId': 'orders',
        'relationshipIds': ['orders_employee'],
        'dimensions': [
            {'id': 'salesperson.name', 'name': 'Salesperson', 'fieldId': 'employees.name'},
            {'id': 'sales.closed_at', 'name': 'Closed at', 'fieldId': 'orders.closed_at'},
        ],
        'measures': [
            {
                'id': 'sales.revenue',
                'name': 'Revenue',
                'fieldId': 'orders.amount',
                'aggregation': 'sum',
            },
            {
                'id': 'sales.order_count',
                'name': 'Orders',
                'fieldId': 'orders.id',
                'aggregation': 'count_distinct',
            },
        ],
        'metrics': [
            {
                'id': 'sales.average_order_value',
                'name': 'AOV',
                'expression': {
                    'operator': 'divide',
                    'leftMeasureId': 'sales.revenue',
                    'rightMeasureId': 'sales.order_count',
                },
            }
        ],
    }


def plan_fixture():
    return QueryPlan.model_validate(
        {
            'version': '1',
            'datasetId': 'sales',
            'dimensions': ['salesperson.name'],
            'measures': ['sales.revenue'],
            'filters': {
                'operator': 'and',
                'conditions': [{'fieldId': 'salesperson.name', 'operator': 'starts_with', 'value': 'A'}],
            },
            'orderBy': [{'fieldId': 'sales.revenue', 'direction': 'desc'}],
            'limit': 5,
        }
    )


@pytest.mark.parametrize(
    ('connector_type', 'table_fragment', 'limit_fragment', 'placeholder'),
    [
        ('sqlite', '"crm"."orders"', 'LIMIT 5', '?'),
        ('postgresql', '"crm"."orders"', 'LIMIT 5', '%s'),
        ('mysql', '`crm`.`orders`', 'LIMIT 5', '%s'),
        ('mssql', '[crm].[orders]', 'TOP 5', '?'),
    ],
)
def test_compiler_generates_parameterized_read_only_sql(connector_type, table_fragment, limit_fragment, placeholder):
    compiled = SemanticCompiler(connector_type, catalog_fixture(), definition_fixture()).compile(plan_fixture())

    assert table_fragment in compiled.sql
    assert limit_fragment in compiled.sql
    assert placeholder in compiled.sql
    assert 'Alice' not in compiled.sql
    assert compiled.parameters == ['A%']
    assert ';' not in compiled.sql
    assert compiled.joins == ['orders_employee']


def test_compiler_allows_non_readable_keys_for_confirmed_relationship_joins():
    catalog = catalog_fixture()
    catalog['objects'][0]['fields'][1]['readable'] = False
    catalog['objects'][1]['fields'][0]['readable'] = False

    compiled = SemanticCompiler('postgresql', catalog, definition_fixture()).compile(plan_fixture())

    assert 't0."salesperson_id" = t1."id"' in compiled.sql
    assert compiled.joins == ['orders_employee']


def test_compiler_does_not_make_non_readable_relationship_keys_selectable():
    catalog = catalog_fixture()
    catalog['objects'][0]['fields'][1]['readable'] = False
    definition = definition_fixture()
    definition['dimensions'].append(
        {
            'id': 'sales.salesperson_id',
            'name': 'Salesperson ID',
            'fieldId': 'orders.salesperson_id',
        }
    )
    plan = plan_fixture()
    plan.dimensions.append('sales.salesperson_id')

    with pytest.raises(SemanticQueryError) as captured:
        SemanticCompiler('postgresql', catalog, definition).compile(plan)

    assert captured.value.code == 'SEMANTIC-FIELD-NOT-ALLOWED'


def test_query_plan_rejects_raw_sql_and_unknown_properties():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(
            {
                'datasetId': 'sales',
                'dimensions': ['salesperson.name'],
                'sql': 'DROP TABLE users',
            }
        )


def test_query_plan_normalizes_common_llm_filter_shapes():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'contacts',
            'dimensions': ['customer.name'],
            'filters': [
                {
                    'fieldId': 'customer.name',
                    'operator': 'equals',
                    'value': '木綠森設計有限公司',
                }
            ],
        }
    )

    assert plan.filters.operator == 'and'
    assert plan.filters.conditions[0].operator == 'eq'
    assert plan.filters.conditions[0].value == '木綠森設計有限公司'


def test_query_plan_normalizes_filter_logic_alias():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'contacts',
            'dimensions': ['customer.name'],
            'filters': {
                'logic': 'or',
                'conditions': [
                    {
                        'fieldId': 'customer.name',
                        'operator': 'startsWith',
                        'value': '木綠森',
                    }
                ],
            },
        }
    )

    assert plan.filters.operator == 'or'
    assert plan.filters.conditions[0].operator == 'starts_with'


def test_query_plan_normalizes_json_encoded_tool_containers():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': '["candidate.name", "candidate.score"]',
            'measures': '[]',
            'metrics': '[]',
            'filters': {
                'operator': 'and',
                'conditions': '[{"fieldId":"candidate.status","operator":"equals","value":"review"}]',
            },
            'orderBy': '[{"fieldId":"candidate.score","direction":"desc"}]',
            'limit': 3,
        }
    )

    assert plan.dimensions == ['candidate.name', 'candidate.score']
    assert plan.measures == []
    assert plan.filters.conditions[0].operator == 'eq'
    assert plan.orderBy[0].fieldId == 'candidate.score'


def test_query_plan_normalizes_provider_item_wrappers():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': {'item': ['candidate.name', 'candidate.score']},
            'measures': {'item': []},
            'metrics': {'item': []},
            'filters': {
                'operator': 'and',
                'conditions': {
                    'item': [
                        {
                            'item': {
                                'fieldId': 'candidate.status',
                                'operator': 'equals',
                                'value': 'review',
                            }
                        }
                    ]
                },
            },
            'orderBy': [
                {
                    'item': {
                        'fieldId': 'candidate.score',
                        'direction': 'desc',
                    }
                }
            ],
            'limit': 3,
        }
    )

    assert plan.dimensions == ['candidate.name', 'candidate.score']
    assert plan.measures == []
    assert plan.filters.conditions[0].operator == 'eq'
    assert plan.orderBy[0].fieldId == 'candidate.score'


def test_query_plan_normalizes_provider_version_and_order_direction():
    plan = QueryPlan.model_validate(
        {
            'version': '1.0',
            'datasetId': 'dataset-1',
            'dimensions': ['candidate.name'],
            'orderBy': [
                {'fieldId': 'candidate.score', 'direction': 'DESC'},
            ],
        }
    )

    assert plan.version == '1'
    assert plan.orderBy[0].direction == 'desc'


def test_query_plan_normalizes_provider_wrapped_filter_values():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': ['candidate.name'],
            'filters': {
                'conditions': [
                    {
                        'fieldId': 'candidate.status',
                        'operator': 'in',
                        'value': {'item': ['review', 'approved']},
                    }
                ]
            },
        }
    )

    assert plan.filters.conditions[0].value == ['review', 'approved']


def test_query_plan_normalizes_provider_single_item_condition():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': {'item': ['candidate.name']},
            'filters': {
                'operator': 'and',
                'conditions': {
                    'item': {
                        'fieldId': 'candidate.status',
                        'operator': 'eq',
                        'value': 'pending_review',
                    }
                },
            },
            'orderBy': {
                'item': {
                    'fieldId': 'candidate.score',
                    'direction': 'desc',
                }
            },
            'limit': '3',
        }
    )

    assert plan.filters.conditions[0].value == 'pending_review'
    assert plan.orderBy[0].fieldId == 'candidate.score'
    assert plan.limit == 3


def test_query_plan_normalizes_provider_single_item_selection():
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': {'item': 'candidate.status'},
            'measures': {'item': 'candidate.count'},
        }
    )

    assert plan.dimensions == ['candidate.status']
    assert plan.measures == ['candidate.count']


def test_query_plan_still_rejects_non_json_list_strings():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(
            {
                'datasetId': 'prospects',
                'dimensions': 'candidate.name,candidate.score',
            }
        )


def test_manifest_exposes_allowed_filter_values():
    assert _manifest_semantic_field(
        {
            'id': 'candidate.status',
            'name': '狀態',
            'description': 'new 與 reviewing 都代表等待人工覆核。',
            'allowedValues': ['new', 'reviewing', 'qualified'],
        }
    ) == {
        'id': 'candidate.status',
        'name': '狀態',
        'description': 'new 與 reviewing 都代表等待人工覆核。',
        'allowedValues': ['new', 'reviewing', 'qualified'],
    }


def test_allowed_filter_values_are_canonicalized_and_unknown_values_rejected():
    definition = {
        'dimensions': [
            {
                'id': 'candidate.status',
                'allowedValues': ['new', 'reviewing', 'qualified'],
            }
        ]
    }
    plan = QueryPlan.model_validate(
        {
            'datasetId': 'prospects',
            'dimensions': ['candidate.name'],
            'filters': {
                'conditions': [
                    {
                        'fieldId': 'candidate.status',
                        'operator': 'in',
                        'value': ['NEW', 'reviewing'],
                    }
                ]
            },
        }
    )

    _validate_allowed_filter_values(plan, definition)
    assert plan.filters.conditions[0].value == ['new', 'reviewing']

    plan.filters.conditions[0].value = ['pending_review']
    with pytest.raises(SemanticQueryError) as captured:
        _validate_allowed_filter_values(plan, definition)
    assert captured.value.code == 'QUERY-FILTER-VALUE-NOT-ALLOWED'


def test_redact_plan_masks_values_from_llm_filter_list_shape():
    plan = {
        'datasetId': 'contacts',
        'dimensions': ['customer.name'],
        'filters': [
            {
                'fieldId': 'contact.email',
                'operator': 'equals',
                'value': 'private@example.com',
            }
        ],
    }

    redacted = _redact_plan(plan)

    assert redacted['filters'][0]['value'] == {'redacted': True, 'valueType': 'str'}
    assert plan['filters'][0]['value'] == 'private@example.com'


def test_compiler_rejects_catalog_field_ids_from_agent_plan_filters():
    plan = plan_fixture()
    plan.filters.conditions[0].fieldId = 'employees.name'
    with pytest.raises(SemanticQueryError) as captured:
        SemanticCompiler('postgresql', catalog_fixture(), definition_fixture()).compile(plan)
    assert captured.value.code == 'SEMANTIC-FIELD-NOT-ALLOWED'


def test_compiler_can_be_reused_without_leaking_old_parameters():
    compiler = SemanticCompiler('postgresql', catalog_fixture(), definition_fixture())
    assert compiler.compile(plan_fixture()).parameters == ['A%']
    assert compiler.compile(plan_fixture()).parameters == ['A%']


def test_compiler_rejects_fanout_from_measure_grain():
    with pytest.raises(SemanticQueryError) as captured:
        SemanticCompiler('postgresql', catalog_fixture('one_to_many'), definition_fixture()).compile(plan_fixture())
    assert captured.value.code == 'QUERY-FANOUT-RISK'


def test_row_policy_is_always_anded_outside_user_or_filters():
    plan = plan_fixture()
    plan.filters.operator = 'or'
    plan.filters.conditions.append(plan.filters.conditions[0].model_copy(update={'value': 'B'}))
    compiled = SemanticCompiler('postgresql', catalog_fixture(), definition_fixture()).compile(
        plan,
        [{'fieldId': 'orders.salesperson_id', 'operator': 'eq', 'value': 'member-a'}],
    )

    assert ') AND (' in compiled.sql
    assert compiled.parameters == ['A%', 'B%', 'member-a']


def test_compiler_rejects_wrong_filter_value_type():
    plan = plan_fixture()
    plan.filters.conditions = [
        plan.filters.conditions[0].model_copy(update={'fieldId': 'sales.revenue', 'operator': 'gte', 'value': 'a lot'})
    ]
    with pytest.raises(SemanticQueryError) as captured:
        SemanticCompiler('postgresql', catalog_fixture(), definition_fixture()).compile(plan)
    assert captured.value.code == 'QUERY-TYPE-MISMATCH'


def test_dataset_validation_requires_confirmed_semantic_fields():
    dataset = {
        'definition': definition_fixture(),
        'access_mode': 'all_company_members',
    }
    assert validate_dataset_definition(dataset, catalog_fixture())['ok'] is True

    broken = catalog_fixture()
    broken['objects'][0]['fields'][2]['aggregatable'] = False
    result = validate_dataset_definition(dataset, broken)
    assert result['ok'] is False
    assert any(item['path'].endswith('fieldId') for item in result['errors'])

    disconnected = definition_fixture()
    disconnected['relationshipIds'] = []
    result = validate_dataset_definition(
        {'definition': disconnected, 'access_mode': 'all_company_members'},
        catalog_fixture(),
    )
    assert result['ok'] is False
    assert any(item['code'] == 'SEMANTIC-RELATIONSHIP-MISSING' for item in result['errors'])


def test_dataset_validation_rejects_arithmetic_aggregation_for_text_fields():
    catalog = catalog_fixture()
    name_field = catalog['objects'][1]['fields'][1]
    name_field['aggregatable'] = True
    definition = definition_fixture()
    definition['measures'] = [
        {
            'id': 'salesperson.name_total',
            'name': 'Invalid name total',
            'fieldId': 'employees.name',
            'aggregation': 'sum',
        }
    ]
    definition['metrics'] = []

    result = validate_dataset_definition(
        {'definition': definition, 'access_mode': 'all_company_members'},
        catalog,
    )

    assert result['ok'] is False
    assert {'code': 'QUERY-TYPE-MISMATCH', 'path': 'definition.measures.0.aggregation'} in result['errors']


def test_relationship_and_row_policy_contracts_reject_unsafe_shapes():
    with pytest.raises(ValidationError):
        RelationshipRequest.model_validate(
            {
                'left_object_id': 'orders',
                'right_object_id': 'employees',
                'relationship_type': 'unknown_cardinality',
                'join_pairs': [{'leftFieldId': 'orders.id', 'rightFieldId': 'employees.id'}],
            }
        )
    with pytest.raises(ValidationError):
        RowPolicyRequest.model_validate(
            {
                'name': 'oversized-policy',
                'principal_type': 'all',
                'expression': {
                    'operator': 'and',
                    'conditions': [
                        {'fieldId': 'orders.id', 'operator': 'eq', 'value': str(index)} for index in range(31)
                    ],
                },
            }
        )


def runtime_context(company='company-a', groups=None):
    return QueryRuntimeContext(
        user_id='user-a',
        user_role='user',
        company_user_id=company,
        company_member_id='member-a',
        company_member_role='member',
        group_ids=groups or [],
        model_id='model-a',
        channel_id='channel-a',
        workflow_id='workflow-a',
    )


def connector_fixture():
    return InteractDataConnectorModel(
        id='connector-a',
        company_user_id='company-a',
        name='CRM',
        connector_type='postgresql',
        execution_mode='webui_local',
        storage_mode='encrypted_credentials',
        enabled=True,
        allowed_schemas=['crm'],
        allowed_tables=['crm.orders'],
        blocked_tables=[],
        allowed_columns={'crm.orders': ['id', 'amount']},
        blocked_columns={},
        max_rows=100,
        query_timeout_seconds=10,
        allow_write=False,
        access_mode='selected_members',
        allowed_member_ids=[],
        allowed_group_ids=['sales'],
        allowed_model_ids=['model-a'],
        allowed_channel_ids=['channel-a'],
        updated_at=0,
    )


def test_connector_acl_honors_group_and_rejects_cross_company():
    connector = connector_fixture()
    _connector_allowed(connector, runtime_context(groups=['sales']))

    with pytest.raises(SemanticQueryError) as captured:
        _connector_allowed(connector, runtime_context(company='company-b', groups=['sales']))
    assert captured.value.code == 'SEMANTIC-DATASET-NOT-ALLOWED'


def test_published_catalog_cannot_bypass_a_tightened_connector_column_policy():
    connector = connector_fixture()
    connector.allowed_tables = ['crm.orders', 'crm.employees']
    connector.allowed_columns = {
        'crm.orders': ['id', 'salesperson_id'],
        'crm.employees': ['id', 'name'],
    }
    with pytest.raises(SemanticQueryError) as captured:
        _enforce_connector_catalog_policy(connector, catalog_fixture(), definition_fixture())
    assert captured.value.code == 'SEMANTIC-FIELD-NOT-ALLOWED'


def test_legacy_connector_acl_honors_group_membership():
    connector = connector_fixture()
    context = QueryContext(
        user_id='user-a',
        user_role='user',
        model_id='model-a',
        channel_id='channel-a',
        channel_source='channel',
        company_user_id='company-a',
        company_member_id='member-a',
        company_member_role='member',
        group_ids=['sales'],
    )
    assert _connector_context_allowed(connector, context) is True


def test_legacy_connector_selected_channel_requires_company_and_channel():
    connector = connector_fixture()
    connector.access_mode = 'selected_channels'
    connector.allowed_channel_ids = ['channel-a']
    context = QueryContext(
        user_id='external-user',
        user_role='user',
        model_id='model-a',
        channel_id='channel-a',
        channel_source='line',
        company_user_id='company-a',
        company_member_id=None,
        company_member_role=None,
        group_ids=[],
    )
    assert _connector_context_allowed(connector, context) is True
    context.channel_id = 'channel-b'
    assert _connector_context_allowed(connector, context) is False
    context.channel_id = 'channel-a'
    context.company_user_id = 'company-b'
    assert _connector_context_allowed(connector, context) is False


@pytest.mark.asyncio
async def test_external_channel_cannot_fall_back_to_webui_local_admin_connector():
    context = QueryContext(
        user_id='webui-admin',
        user_role='admin',
        model_id='model-a',
        channel_id='line-channel',
        channel_source='channel',
        company_user_id=None,
        company_member_id=None,
        company_member_role=None,
        group_ids=[],
    )

    with pytest.raises(PermissionError, match='No company identity'):
        await _resolve_default_connector(context)
    with pytest.raises(PermissionError, match='interactive WebUI administrators'):
        await _load_connector('webui_local', context)


def test_dataset_acl_requires_every_context_scope():
    dataset = {
        'company_user_id': 'company-a',
        'status': 'published',
        'current_version_id': 'version-1',
        'access_mode': 'all_company_members',
        'allowed_member_ids': [],
        'allowed_group_ids': [],
        'allowed_model_ids': ['model-a'],
        'allowed_channel_ids': ['channel-a'],
        'allowed_workflow_ids': ['workflow-a'],
    }
    _dataset_allowed(dataset, runtime_context())
    denied = runtime_context()
    denied.workflow_id = 'workflow-b'
    with pytest.raises(SemanticQueryError) as captured:
        _dataset_allowed(dataset, denied)
    assert captured.value.code == 'SEMANTIC-DATASET-NOT-ALLOWED'

    dataset['status'] = 'blocked'
    with pytest.raises(SemanticQueryError) as captured:
        _dataset_allowed(dataset, runtime_context())
    assert captured.value.code == 'SEMANTIC-MODEL-DRIFT-BLOCKED'


def test_external_channel_requires_explicit_connector_and_dataset_channel_access():
    connector = connector_fixture()
    external = runtime_context(groups=[])
    external.company_member_id = None
    external.company_member_role = None
    external.external_user_id = 'line-user-a'
    external.channel_source = 'channel'
    _connector_allowed(connector, external)

    connector.allowed_channel_ids = []
    with pytest.raises(SemanticQueryError) as captured:
        _connector_allowed(connector, external)
    assert captured.value.code == 'DB-CHANNEL-NOT-ALLOWED'

    dataset = {
        'company_user_id': 'company-a',
        'status': 'published',
        'current_version_id': 'version-1',
        'access_mode': 'selected_channels',
        'allowed_member_ids': [],
        'allowed_group_ids': [],
        'allowed_model_ids': ['model-a'],
        'allowed_channel_ids': ['channel-a'],
        'allowed_workflow_ids': ['workflow-a'],
    }
    _dataset_allowed(dataset, external)

    dataset['access_mode'] = 'all_company_members'
    _dataset_allowed(dataset, external)

    external.channel_id = 'channel-b'
    with pytest.raises(SemanticQueryError) as captured:
        _dataset_allowed(dataset, external)
    assert captured.value.code == 'SEMANTIC-DATASET-NOT-ALLOWED'


def test_dataset_channel_allowlist_adds_external_access_without_blocking_internal_principals():
    dataset = {
        'company_user_id': 'company-a',
        'status': 'published',
        'current_version_id': 'version-1',
        'access_mode': 'company_admins',
        'allowed_member_ids': [],
        'allowed_group_ids': [],
        'allowed_model_ids': ['model-a'],
        'allowed_channel_ids': ['channel-a'],
        'allowed_workflow_ids': [],
    }
    internal_admin = runtime_context()
    internal_admin.user_role = 'admin'
    internal_admin.channel_id = None
    internal_admin.workflow_id = None
    _dataset_allowed(dataset, internal_admin)

    external = runtime_context(groups=[])
    external.company_member_id = None
    external.company_member_role = None
    external.external_user_id = 'line-user-a'
    external.channel_source = 'line'
    external.workflow_id = None
    _dataset_allowed(dataset, external)


def test_row_policy_resolves_trusted_context_and_denies_missing_values():
    policy = {
        'id': 'policy-a',
        'status': 'published',
        'principal_type': 'all',
        'principal_ids': [],
        'deny_if_unresolved': True,
        'expression': {
            'operator': 'and',
            'conditions': [
                {
                    'fieldId': 'orders.salesperson_id',
                    'operator': 'eq',
                    'value': '$context.companyMemberId',
                }
            ],
        },
    }
    filters, decision = resolve_row_policies([policy], runtime_context())
    assert filters[0]['value'] == 'member-a'
    assert decision['rowPolicyIds'] == ['policy-a']

    missing = runtime_context()
    missing.company_member_id = None
    with pytest.raises(SemanticQueryError) as captured:
        resolve_row_policies([policy], missing)
    assert captured.value.code == 'ROW-POLICY-CONTEXT-MISSING'


def test_active_scoped_row_policies_deny_unmatched_principals():
    policy = {
        'id': 'north-team',
        'status': 'active',
        'principal_type': 'group',
        'principal_ids': ['north'],
        'deny_if_unresolved': True,
        'expression': {
            'operator': 'and',
            'conditions': [
                {
                    'fieldId': 'orders.salesperson_id',
                    'operator': 'eq',
                    'value': '$context.company_member_id',
                }
            ],
        },
    }
    with pytest.raises(SemanticQueryError) as captured:
        resolve_row_policies([policy], runtime_context(groups=['south']))
    assert captured.value.code == 'SEMANTIC-DATASET-NOT-ALLOWED'

    filters, _ = resolve_row_policies([policy], runtime_context(groups=['north']))
    assert filters[0]['value'] == 'member-a'


def test_audit_plan_redacts_filter_values():
    original = {
        'datasetId': 'sales',
        'filters': {
            'operator': 'and',
            'conditions': [{'fieldId': 'customer.email', 'operator': 'eq', 'value': 'secret@example.com'}],
        },
    }
    redacted = _redact_plan(original)
    assert 'secret@example.com' not in str(redacted)
    assert original['filters']['conditions'][0]['value'] == 'secret@example.com'


def test_semantic_router_exposes_the_documented_contracts():
    contracts = {(method, route.path) for route in semantic_router.routes for method in route.methods}
    expected = {
        ('POST', '/data-connectors/{connector_id}/schema-scans'),
        ('GET', '/data-connectors/{connector_id}/schema-snapshots'),
        ('GET', '/data-connectors/{connector_id}/schema-snapshots/{snapshot_id}'),
        ('GET', '/data-connectors/{connector_id}/schema-diff'),
        ('GET', '/data-connectors/{connector_id}/catalog'),
        ('POST', '/catalog/relationships'),
        ('GET', '/semantic-datasets/{dataset_id}'),
        ('PATCH', '/semantic-datasets/{dataset_id}'),
        ('POST', '/semantic-datasets/{dataset_id}/test-query'),
        ('POST', '/semantic-query/catalog-search'),
        ('POST', '/semantic-query/plan/validate'),
        ('POST', '/semantic-query/execute'),
        ('GET', '/semantic-query/events/{request_id}'),
    }
    assert expected.issubset(contracts)


def test_schema_diff_distinguishes_additive_and_breaking_changes():
    before = {'tables': [{'table': 'orders', 'columns': [{'name': 'id', 'type': 'int'}]}]}
    additive = {
        'tables': [
            {
                'table': 'orders',
                'columns': [{'name': 'id', 'type': 'int'}, {'name': 'note', 'type': 'text'}],
            }
        ]
    }
    breaking = {'tables': [{'table': 'orders', 'columns': [{'name': 'id', 'type': 'text'}]}]}

    assert _snapshot_diff(before, additive)['severity'] == 'additive'
    assert _snapshot_diff(before, breaking)['severity'] == 'breaking'


def test_schema_fingerprint_is_stable_when_driver_order_changes():
    first = {
        'scanned_at': '2026-07-14T00:00:00Z',
        'tables': [
            {'table': 'z', 'columns': [{'name': 'b'}, {'name': 'a'}]},
            {'table': 'a', 'columns': [{'name': 'id'}]},
        ],
    }
    reordered = {
        'scanned_at': '2026-07-15T00:00:00Z',
        'tables': [
            {'table': 'a', 'columns': [{'name': 'id'}]},
            {'table': 'z', 'columns': [{'name': 'a'}, {'name': 'b'}]},
        ],
    }
    assert canonical_schema_fingerprint(first) == canonical_schema_fingerprint(reordered)


def test_manifest_ranking_uses_chinese_synonyms_and_examples():
    sales = {
        'name': '銷售績效',
        'description': '企業成交資料',
        'synonyms': ['業績', '營收'],
        'examples': ['本月業績前五名是誰？'],
    }
    inventory = {
        'name': '庫存',
        'description': '倉庫現有數量',
        'synonyms': ['存貨'],
        'examples': ['哪些商品快沒貨？'],
    }
    assert _manifest_score('本月營收排行', sales) > _manifest_score('本月營收排行', inventory)


@pytest.mark.asyncio
async def test_query_validation_does_not_reserve_daily_entitlement(monkeypatch):
    calls = []

    async def reserve(company_user_id):
        calls.append(company_user_id)

    monkeypatch.setattr('open_webui.semantic_query.service.reserve_query_entitlement', reserve)
    with pytest.raises(SemanticQueryError) as captured:
        await validate_query({}, runtime_context())

    assert captured.value.code == 'QUERY-PLAN-INVALID'
    assert calls == []


def test_postgres_execution_sets_a_parameterized_local_timeout(monkeypatch):
    commands = []

    class Cursor:
        description = [('employee.name',)]

        def execute(self, sql, parameters=None):
            commands.append((sql, parameters))

        def fetchall(self):
            return [('Amy',)]

        def close(self):
            pass

    class Connection:
        autocommit = True

        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr('open_webui.semantic_query.service._pg_connect', lambda _connector: Connection())
    connector = connector_fixture()

    rows = _execute_sync(connector, 'SELECT %s AS "employee.name"', ['Amy'])

    assert rows == [{'employee.name': 'Amy'}]
    assert commands == [
        ('SET TRANSACTION READ ONLY', None),
        ("SELECT set_config('statement_timeout', %s, true)", ('10000ms',)),
        ('SELECT %s AS "employee.name"', ['Amy']),
    ]


@pytest.mark.asyncio
async def test_execute_query_runs_read_only_sqlite_masks_results_and_returns_totals(tmp_path, monkeypatch):
    database = tmp_path / 'sales.db'
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE employees (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            salesperson_id TEXT NOT NULL,
            amount REAL NOT NULL,
            closed_at TEXT,
            FOREIGN KEY (salesperson_id) REFERENCES employees(id)
        );
        INSERT INTO employees VALUES ('member-a', 'Amy'), ('member-b', 'Bob');
        INSERT INTO orders VALUES
            ('o1', 'member-a', 100, '2026-07-01'),
            ('o2', 'member-a', 250, '2026-07-02'),
            ('o3', 'member-b', 900, '2026-07-03');
        """
    )
    connection.close()

    catalog = catalog_fixture()
    for obj in catalog['objects']:
        obj['physical_name'] = obj['physical_name'].split('.')[-1]
    catalog['objects'][1]['fields'][1]['masking_rule'] = 'redact'
    definition = definition_fixture()
    connector = connector_fixture()
    connector.connector_type = 'sqlite'
    connector.connection_string = f'sqlite:///{database}'
    connector.allowed_tables = ['orders', 'employees']
    connector.allowed_columns = {
        'orders': ['id', 'salesperson_id', 'amount', 'closed_at'],
        'employees': ['id', 'name'],
    }
    runtime = SemanticRuntime(
        dataset={'id': 'sales', 'name': 'Sales'},
        version={'id': 'version-1', 'version': 1, 'definition': definition},
        connector=connector,
        catalog=catalog,
        row_filters=[{'fieldId': 'orders.salesperson_id', 'operator': 'eq', 'value': 'member-a'}],
        policy_decision={'allowed': True, 'rowPolicyIds': ['policy-a']},
    )
    events = []

    async def fake_runtime(plan, context):
        return runtime

    async def fake_event(event):
        events.append(event)

    monkeypatch.setattr('open_webui.semantic_query.service.load_runtime', fake_runtime)
    monkeypatch.setattr('open_webui.semantic_query.service.InteractSemantic.record_event', fake_event)
    result = await execute_query(
        {
            'version': '1',
            'datasetId': 'sales',
            'dimensions': ['salesperson.name'],
            'measures': ['sales.revenue'],
            'orderBy': [{'fieldId': 'sales.revenue', 'direction': 'desc'}],
            'limit': 5,
            'includeTotals': True,
        },
        runtime_context(groups=['sales']),
    )

    assert result['rows'] == [{'salesperson.name': '[REDACTED]', 'sales.revenue': 350.0}]
    assert result['totals'] == {'sales.revenue': 350.0}
    assert events[0]['status'] == 'success'
    assert events[0]['row_count'] == 1

    workflow_context = runtime_context(groups=['sales'])
    workflow_context.workflow_id = 'workflow-email'
    unmasked = await execute_query(
        {
            'version': '1',
            'datasetId': 'sales',
            'dimensions': ['salesperson.name'],
            'limit': 5,
        },
        workflow_context,
        unmasked_field_ids={'salesperson.name'},
    )

    assert unmasked['rows'] == [{'salesperson.name': 'Amy'}]
    assert events[1]['policy_decision']['sensitiveFieldAccess'] == {
        'purpose': 'workflow_email_delivery',
        'fieldIds': ['salesperson.name'],
    }


@pytest.mark.asyncio
async def test_execute_query_rejects_unmasked_fields_outside_workflow():
    context = runtime_context(groups=['sales'])
    context.workflow_id = None
    with pytest.raises(SemanticQueryError) as captured:
        await execute_query(
            {'datasetId': 'sales', 'dimensions': ['salesperson.name']},
            context,
            unmasked_field_ids={'salesperson.name'},
        )

    assert captured.value.code == 'SEMANTIC-FIELD-NOT-ALLOWED'


@pytest.mark.asyncio
async def test_execute_error_exposes_the_audited_request_id(monkeypatch):
    events = []
    reservations = []

    async def fake_event(event):
        events.append(event)

    async def reserve(company_user_id):
        reservations.append(company_user_id)
        return {'limit': 1000, 'used': 1}

    monkeypatch.setattr('open_webui.semantic_query.service.InteractSemantic.record_event', fake_event)
    monkeypatch.setattr('open_webui.semantic_query.service.reserve_query_entitlement', reserve)
    context = runtime_context(groups=['sales'])
    context.request_id = 'channel-job-123'
    with pytest.raises(SemanticQueryError) as captured:
        await execute_query({'datasetId': 'sales', 'sql': 'password=do-not-log'}, context)

    assert captured.value.public()['requestId'] == 'channel-job-123'
    assert events[0]['request_id'] == 'channel-job-123'
    assert events[0]['error_code'] == 'QUERY-PLAN-INVALID'
    assert 'do-not-log' not in events[0]['error_detail']
    assert 'do-not-log' not in str(events[0]['plan_redacted'])
    assert reservations == ['company-a']


@pytest.mark.asyncio
async def test_execute_preserves_stable_database_connection_error(monkeypatch):
    runtime = SemanticRuntime(
        dataset={'id': 'sales', 'name': 'Sales'},
        version={'id': 'version-1', 'version': 1, 'definition': definition_fixture()},
        connector=connector_fixture(),
        catalog=catalog_fixture(),
        row_filters=[],
        policy_decision={'allowed': True, 'rowPolicyIds': []},
    )
    events = []

    async def fake_runtime(plan, context):
        return runtime

    async def fake_event(event):
        events.append(event)

    def fail_execution(connector, sql, parameters):
        raise ConnectionError('connection refused')

    monkeypatch.setattr('open_webui.semantic_query.service.load_runtime', fake_runtime)
    monkeypatch.setattr('open_webui.semantic_query.service._execute_sync', fail_execution)
    monkeypatch.setattr('open_webui.semantic_query.service.InteractSemantic.record_event', fake_event)
    with pytest.raises(SemanticQueryError) as captured:
        await execute_query(plan_fixture().model_dump(mode='json'), runtime_context(groups=['sales']))

    assert captured.value.code == 'DB-CONNECTION-FAILED'
    assert captured.value.retryable is True
    assert events[0]['error_code'] == 'DB-CONNECTION-FAILED'
