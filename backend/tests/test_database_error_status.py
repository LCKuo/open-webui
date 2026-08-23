import json

import pytest
from open_webui.routers.interact_channels import (
    _channel_runtime_failure,
    _incomplete_semantic_tool_response,
    _rate_limit_text,
    _tool_failure_from_items,
)
from open_webui.tools.interact_database import _build_where_sql, _database_error_result


def test_database_filter_builder_supports_parameterized_in_and_not_in_lists():
    where_sql, values = _build_where_sql(
        lambda value: f'"{value}"',
        {
            'id': {'$in': [8, 14, 20]},
            'status': {'$nin': ['disabled', 'deleted']},
        },
    )

    assert where_sql == ' WHERE "id" IN (?, ?, ?) AND "status" NOT IN (?, ?)'
    assert values == [8, 14, 20, 'disabled', 'deleted']


@pytest.mark.parametrize('operand', [[], '1,2,3', list(range(101))])
def test_database_filter_builder_rejects_unsafe_in_lists(operand):
    with pytest.raises(ValueError):
        _build_where_sql(lambda value: f'"{value}"', {'id': {'$in': operand}})


def test_database_errors_have_stable_public_codes():
    cases = [
        (PermissionError('No data connector is synced for this company.'), 'DB-CONNECTOR-NOT-CONFIGURED'),
        (PermissionError('model zxh001 is not assigned'), 'DB-MODEL-NOT-ALLOWED'),
        (PermissionError('channel abc is not assigned'), 'DB-CHANNEL-NOT-ALLOWED'),
        (PermissionError('Table is not allowed: crm.secret'), 'DB-TABLE-NOT-ALLOWED'),
        (PermissionError('Column is not allowed: password'), 'DB-COLUMN-NOT-ALLOWED'),
        (RuntimeError('connection refused'), 'DB-CONNECTION-FAILED'),
        (TimeoutError('query timed out'), 'DB-TIMEOUT'),
    ]

    for error, expected in cases:
        result = _database_error_result(error)
        assert result['error_code'] == expected
        assert result['message']
        assert str(error) not in result.values()


def test_channel_uses_database_error_code_without_exposing_internal_detail():
    output = [
        {
            'type': 'function_call',
            'call_id': 'call-1',
            'name': 'interact_database_query',
        },
        {
            'type': 'function_call_output',
            'call_id': 'call-1',
            'output': [
                {
                    'type': 'input_text',
                    'text': json.dumps(
                        {
                            'ok': False,
                            'error_code': 'DB-CHANNEL-NOT-ALLOWED',
                            'message': '目前通訊渠道未獲授權使用這個資料庫連接器。',
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
    ]

    assert _tool_failure_from_items(output) == (
        'DB-CHANNEL-NOT-ALLOWED',
        '目前通訊渠道未獲授權使用這個資料庫連接器。（錯誤代碼：DB-CHANNEL-NOT-ALLOWED）',
    )


def test_channel_reads_nested_semantic_error_code():
    output = [
        {'type': 'function_call', 'call_id': 'call-1', 'name': 'interact_semantic_query'},
        {
            'type': 'function_call_output',
            'call_id': 'call-1',
            'output': [
                {
                    'type': 'input_text',
                    'text': json.dumps(
                        {
                            'ok': False,
                            'error': {
                                'code': 'QUERY-PLAN-INVALID',
                                'message': '查詢計畫格式不正確。',
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
    ]

    assert _tool_failure_from_items(output) == (
        'QUERY-PLAN-INVALID',
        '查詢計畫格式不正確。（錯誤代碼：QUERY-PLAN-INVALID）',
    )


def test_channel_ignores_tool_failure_resolved_by_later_retry():
    output = [
        {'type': 'function_call', 'call_id': 'call-1', 'name': 'interact_semantic_query'},
        {
            'type': 'function_call_output',
            'call_id': 'call-1',
            'output': [
                {
                    'type': 'input_text',
                    'text': json.dumps(
                        {
                            'ok': False,
                            'error': {
                                'code': 'QUERY-PLAN-INVALID',
                                'message': '查詢計畫格式不正確。',
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
        {'type': 'function_call', 'call_id': 'call-2', 'name': 'interact_semantic_query'},
        {
            'type': 'function_call_output',
            'call_id': 'call-2',
            'output': [
                {
                    'type': 'input_text',
                    'text': json.dumps({'ok': True, 'rows': [{'customer.name': '木綠森設計有限公司'}]}),
                }
            ],
        },
    ]

    assert _tool_failure_from_items(output) is None


def test_channel_runtime_failures_are_distinct():
    assert _channel_runtime_failure('Configured Open WebUI model was not found.')[0] == 'AI-MODEL-NOT-FOUND'
    assert _channel_runtime_failure('Interact Web Ai user not found.')[0] == 'AI-ACCOUNT-NOT-FOUND'
    assert _channel_runtime_failure('Interact Web Ai user is pending approval.')[0] == 'AI-ACCOUNT-PENDING'
    assert _channel_runtime_failure('unexpected internal detail')[0] == 'AI-RUNTIME-FAILED'


def test_channel_rejects_catalog_only_transition_as_final_answer():
    output = [
        {
            'type': 'function_call',
            'call_id': 'catalog-1',
            'name': 'interact_semantic_catalog',
        },
        {
            'type': 'function_call_output',
            'call_id': 'catalog-1',
            'output': [{'type': 'input_text', 'text': '{"ok":true,"datasets":[{}]}'}],
        },
    ]

    assert _incomplete_semantic_tool_response(output, '我先查詢已發布資料集的欄位定義。') is True


def test_channel_accepts_answer_after_semantic_query():
    output = [
        {'type': 'function_call', 'call_id': 'catalog-1', 'name': 'interact_semantic_catalog'},
        {'type': 'function_call', 'call_id': 'query-1', 'name': 'interact_semantic_query'},
    ]

    assert _incomplete_semantic_tool_response(output, '查詢結果共有三家公司。') is False


def test_channel_rate_limit_messages_include_stable_codes():
    channel = object()
    assert 'CHANNEL-BOT-DAILY-TOKEN-LIMIT' in _rate_limit_text(channel, 'daily-token')
    assert 'CHANNEL-USER-DAILY-LIMIT' in _rate_limit_text(channel, 'daily-user')
    assert 'CHANNEL-USER-RATE-LIMIT' in _rate_limit_text(channel, 'user-rpm')
    assert 'CHANNEL-BOT-RATE-LIMIT' in _rate_limit_text(channel, 'rpm')
