import json

from open_webui.semantic_query.schema_handoff import build_schema_handoff


def test_schema_handoff_includes_authorized_and_unauthorized_schema_without_secrets():
    catalog = {
        'snapshotId': 'snapshot-8',
        'objects': [
            {
                'id': 'object-shifts',
                'physical_name': 'operations.shifts',
                'display_name': 'Shifts',
                'object_type': 'table',
                'enabled': False,
                'fields': [
                    {
                        'id': 'field-worker-id',
                        'physical_name': 'worker_id',
                        'physical_type': 'uuid',
                        'semantic_type': 'identifier',
                        'primary_key': False,
                        'readable': True,
                        'filterable': False,
                        'groupable': True,
                        'aggregatable': False,
                        'sensitivity': 'internal',
                        'masking_rule': 'none',
                    }
                ],
            },
            {
                'id': 'object-workers',
                'physical_name': 'directory.workers',
                'display_name': 'Workers',
                'object_type': 'table',
                'enabled': True,
                'fields': [
                    {
                        'id': 'field-worker-pk',
                        'physical_name': 'id',
                        'physical_type': 'uuid',
                        'semantic_type': 'identifier',
                        'primary_key': True,
                        'readable': True,
                        'filterable': True,
                        'groupable': True,
                        'aggregatable': False,
                        'sensitivity': 'internal',
                        'masking_rule': 'none',
                    }
                ],
            },
        ],
        'relationships': [
            {
                'id': 'relationship-internal-id',
                'left_object_id': 'object-shifts',
                'right_object_id': 'object-workers',
                'relationship_type': 'many_to_one',
                'join_type': 'left',
                'join_pairs': [{'leftFieldId': 'field-worker-id', 'rightFieldId': 'field-worker-pk'}],
                'status': 'confirmed',
                'fanout_risk': 'low',
            }
        ],
    }

    document = build_schema_handoff(
        catalog,
        {'name': 'Scheduling warehouse', 'connectorType': 'postgresql'},
        {'version': 8, 'fingerprint': 'safe-fingerprint'},
    )

    assert document['format'] == 'interact-semantic-schema-handoff'
    assert document['schema']['objects'][1]['authorization']['enabled'] is False
    assert document['schema']['objects'][1]['fields'][0]['authorization']['filterable'] is False
    assert document['schema']['relationships'][0]['joinPairs'] == [
        {'leftField': 'worker_id', 'rightField': 'id'}
    ]
    serialized = json.dumps(document)
    assert 'object-shifts' not in serialized
    assert 'relationship-internal-id' not in serialized
    assert 'password' not in serialized.lower()
    assert 'connectionstring' in serialized.lower()  # Listed only as a forbidden output key.
    assert document['securityNotice']['containsCredentials'] is False
    assert 'permissionRecommendations' in document['outputContract']['topLevelKeys']
    assert document['outputContract']['jsonTemplate']['dataset']['model']['metrics'][0]['expression'] == {
        'operator': 'divide',
        'leftMeasureId': 'measure.left_id',
        'rightMeasureId': 'measure.right_id',
    }
