from types import SimpleNamespace

from open_webui.tools.interact_database import _filter_schema_scan_for_policy


def test_schema_policy_filter_preserves_safe_semantic_metadata():
    connector = SimpleNamespace(
        allowed_tables=['sales.orders', 'directory.people'],
        blocked_tables=[],
        allowed_columns={},
        blocked_columns={},
    )
    scanned = {
        'tables': [
            {
                'name': 'sales.orders',
                'schema': 'sales',
                'type': 'BASE TABLE',
                'description': 'Signed orders. Grain: one row per order.',
                'columns': [
                    {
                        'name': 'status',
                        'type': 'USER-DEFINED',
                        'nullable': False,
                        'default': "'active'::sales.order_status",
                        'primary_key': False,
                        'unique': False,
                        'description': 'Order lifecycle status.',
                        'allowed_values': ['active', 'completed'],
                    }
                ],
                'primary_key': [],
                'unique_keys': [],
                'foreign_keys': [],
            },
            {
                'name': 'directory.people',
                'schema': 'directory',
                'type': 'BASE TABLE',
                'description': 'People directory.',
                'columns': [
                    {
                        'name': 'id',
                        'type': 'uuid',
                        'nullable': False,
                        'primary_key': True,
                        'unique': False,
                        'description': 'Stable person identifier.',
                    }
                ],
                'primary_key': ['id'],
                'unique_keys': [],
                'foreign_keys': [],
            },
        ]
    }

    result = _filter_schema_scan_for_policy(connector, scanned, None)

    status = result[0]['columns'][0]
    assert result[0]['description'] == 'Signed orders. Grain: one row per order.'
    assert status['description'] == 'Order lifecycle status.'
    assert status['allowed_values'] == ['active', 'completed']
    assert status['default'] == "'active'::sales.order_status"
    assert status['nullable'] is False
