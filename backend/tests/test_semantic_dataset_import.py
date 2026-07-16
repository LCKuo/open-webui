from open_webui.semantic_query.compiler import SemanticCompiler
from open_webui.semantic_query.contracts import QueryPlan
from open_webui.semantic_query.dataset_import import resolve_portable_dataset


def portable_catalog():
    shipments = {
        'id': 'object-shipments',
        'physical_name': 'warehouse.shipments',
        'enabled': True,
        'fields': [
            {
                'id': 'shipment-id',
                'physical_name': 'tracking_id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': True,
            },
            {
                'id': 'shipment-agent-id',
                'physical_name': 'agent_id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
            },
            {
                'id': 'shipment-fee',
                'physical_name': 'delivery_fee',
                'physical_type': 'numeric(12,2)',
                'semantic_type': 'money',
                'readable': True,
                'filterable': False,
                'groupable': False,
                'aggregatable': True,
            },
            {
                'id': 'shipment-status',
                'physical_name': 'status',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
            },
            {
                'id': 'shipment-delivered-at',
                'physical_name': 'delivered_at',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
            },
        ],
    }
    agents = {
        'id': 'object-agents',
        'physical_name': 'identity.agents',
        'enabled': True,
        'fields': [
            {
                'id': 'agent-id',
                'physical_name': 'id',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
            },
            {
                'id': 'agent-name',
                'physical_name': 'display_name',
                'readable': True,
                'filterable': True,
                'groupable': True,
                'aggregatable': False,
            },
        ],
    }
    return {
        'snapshotId': 'snapshot-logistics-7',
        'objects': [shipments, agents],
        'relationships': [
            {
                'id': 'shipments-to-agents',
                'left_object_id': shipments['id'],
                'right_object_id': agents['id'],
                'join_pairs': [{'leftFieldId': 'shipment-agent-id', 'rightFieldId': 'agent-id'}],
                'status': 'confirmed',
                'fanout_risk': 'low',
            }
        ],
    }


def portable_document():
    return {
        'format': 'interact-semantic-dataset',
        'version': 1,
        'dataset': {
            'name': 'Delivery performance',
            'slug': 'delivery-performance',
            'description': 'Carrier delivery performance and fees.',
            'businessDomain': 'Logistics',
            'access': {'mode': 'all_company_members', 'modelIds': ['ops-model']},
            'model': {
                'rootObject': 'warehouse.shipments',
                'whenToUse': 'Delivery rankings, fees, and completion rates.',
                'notFor': ['inventory levels'],
                'examples': ['Which agent delivered the most shipments this month?'],
                'synonyms': ['carrier', 'parcel'],
                'defaultTimeDimension': 'shipment.delivered_at',
                'dimensions': [
                    {
                        'id': 'agent.name',
                        'name': 'Agent',
                        'field': {'object': 'identity.agents', 'field': 'display_name'},
                    },
                    {
                        'id': 'shipment.delivered_at',
                        'name': 'Delivered at',
                        'field': {'object': 'warehouse.shipments', 'field': 'delivered_at'},
                    },
                ],
                'measures': [
                    {
                        'id': 'shipment.fees',
                        'name': 'Delivery fees',
                        'field': {'object': 'warehouse.shipments', 'field': 'delivery_fee'},
                        'aggregation': 'sum',
                        'filters': [
                            {
                                'field': {'object': 'warehouse.shipments', 'field': 'status'},
                                'operator': 'eq',
                                'value': 'delivered',
                            }
                        ],
                    },
                    {
                        'id': 'shipment.count',
                        'name': 'Shipments',
                        'field': {'object': 'warehouse.shipments', 'field': 'tracking_id'},
                        'aggregation': 'count_distinct',
                    },
                ],
                'metrics': [
                    {
                        'id': 'shipment.average_fee',
                        'name': 'Average fee',
                        'semanticType': 'money',
                        'expression': {
                            'operator': 'divide',
                            'leftMeasureId': 'shipment.fees',
                            'rightMeasureId': 'shipment.count',
                        },
                    }
                ],
                'relationships': [
                    {
                        'leftObject': 'identity.agents',
                        'rightObject': 'warehouse.shipments',
                        'joinPairs': [{'leftField': 'id', 'rightField': 'agent_id'}],
                    }
                ],
            },
        },
    }


def test_portable_import_resolves_non_crm_schema_and_preserves_semantics():
    result = resolve_portable_dataset(portable_document(), portable_catalog(), 'connector-logistics')

    assert result['ok'] is True
    assert result['errors'] == []
    assert result['summary'] == {'dimensions': 2, 'measures': 2, 'metrics': 1, 'relationships': 1}
    dataset = result['dataset']
    definition = dataset['definition']
    assert dataset['connector_id'] == 'connector-logistics'
    assert dataset['allowed_model_ids'] == ['ops-model']
    assert definition['rootObjectId'] == 'object-shipments'
    assert definition['relationshipIds'] == ['shipments-to-agents']
    assert definition['defaultTimeDimensionId'] == 'shipment.delivered_at'
    assert definition['measures'][0]['aggregation'] == 'sum'
    assert definition['measures'][0]['filters'][0]['fieldId'] == 'shipment-status'
    assert definition['measures'][1]['aggregation'] == 'count_distinct'
    assert definition['metrics'][0]['expression']['rightMeasureId'] == 'shipment.count'


def test_portable_import_compiles_relationships_with_non_readable_join_keys():
    catalog = portable_catalog()
    catalog['objects'][0]['fields'][1]['readable'] = False
    catalog['objects'][1]['fields'][0]['readable'] = False

    result = resolve_portable_dataset(portable_document(), catalog, 'connector-logistics')

    assert result['ok'] is True
    compiled = SemanticCompiler('postgresql', catalog, result['dataset']['definition']).compile(
        QueryPlan.model_validate(
            {
                'datasetId': 'delivery-performance',
                'dimensions': ['agent.name'],
                'measures': ['shipment.fees'],
                'orderBy': [{'fieldId': 'shipment.fees', 'direction': 'desc'}],
                'limit': 20,
            }
        )
    )
    assert 't0."agent_id" = t1."id"' in compiled.sql
    assert compiled.joins == ['shipments-to-agents']


def test_portable_import_reports_missing_objects_without_saving_partial_definition():
    document = portable_document()
    document['dataset']['model']['rootObject'] = 'finance.invoices'

    result = resolve_portable_dataset(document, portable_catalog(), 'connector-logistics')

    assert result['ok'] is False
    assert any(item['code'] == 'IMPORT-OBJECT-NOT-FOUND' for item in result['errors'])
    assert result['dataset']['definition']['rootObjectId'] == ''


def test_portable_import_rejects_unknown_properties_and_unsupported_versions():
    document = portable_document()
    document['version'] = 2
    document['dataset']['model']['sql'] = 'SELECT * FROM secrets'

    result = resolve_portable_dataset(document, portable_catalog(), 'connector-logistics')

    assert result['ok'] is False
    assert result['dataset'] is None
    paths = {item['path'] for item in result['errors']}
    assert 'version' in paths
    assert 'dataset.model.sql' in paths


def test_portable_import_derives_missing_permissions_and_flags_ai_revoke_conflict():
    catalog = portable_catalog()
    catalog['objects'][1]['enabled'] = False
    agent_name = catalog['objects'][1]['fields'][1]
    agent_name['readable'] = False
    agent_name['groupable'] = False
    catalog['objects'][0]['fields'][2]['aggregatable'] = False
    document = portable_document()
    document['permissionRecommendations'] = [
        {
            'target': {'object': 'identity.agents', 'field': 'display_name'},
            'permission': 'groupable',
            'action': 'grant',
            'reason': '員工排名需要依顯示名稱分組，否則無法產生排名。',
            'requiredFor': ['agent.name dimension'],
        },
        {
            'target': {'object': 'warehouse.shipments', 'field': 'status'},
            'permission': 'filterable',
            'action': 'revoke',
            'reason': 'AI 認為狀態欄位不應再作為一般篩選條件。',
            'requiredFor': ['least privilege review'],
        },
    ]

    result = resolve_portable_dataset(document, catalog, 'connector-logistics')

    assert result['ok'] is False
    assert result['errors'] == []
    assert result['permissionReviewRequired'] is True
    changes = result['permissionChanges']
    assert any(
        item['object'] == 'identity.agents'
        and item['permission'] == 'enabled'
        and item['required']
        and item['source'] == 'system'
        for item in changes
    )
    assert any(
        item['field'] == 'display_name' and item['permission'] == 'groupable' and item['source'] == 'system+ai'
        for item in changes
    )
    assert any(
        item['field'] == 'delivery_fee' and item['permission'] == 'aggregatable' and item['source'] == 'system'
        for item in changes
    )
    assert any(
        item['field'] == 'status'
        and item['permission'] == 'filterable'
        and item['action'] == 'revoke'
        and item['status'] == 'conflict'
        for item in changes
    )

    for obj in catalog['objects']:
        obj['enabled'] = True
        for field in obj['fields']:
            field.update(readable=True, filterable=True, groupable=True, aggregatable=True)
    revalidated = resolve_portable_dataset(document, catalog, 'connector-logistics')
    assert revalidated['ok'] is True
    assert revalidated['authorizationSummary']['missingRequired'] == 0
    assert any(item['status'] == 'conflict' for item in revalidated['permissionChanges'])
