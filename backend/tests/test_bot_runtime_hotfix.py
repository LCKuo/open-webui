import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import upstream_retry
from open_webui.utils.misc import convert_output_to_messages
from open_webui.routers.interact_channels import _channel_runtime_failure, _channel_result_tokens, _usage_tokens
from open_webui.tools import interact_semantic


class Response:
    def __init__(self, status, body='', retry_after=None):
        self.status = status
        self.body = body
        self.headers = {'Retry-After': retry_after} if retry_after else {}
        self.released = False

    async def text(self):
        return self.body

    def release(self):
        self.released = True


def test_rate_limit_retry_is_bounded_and_releases_rejected_responses(monkeypatch):
    responses = [Response(429), Response(429), Response(200)]
    session = type('Session', (), {'request': AsyncMock(side_effect=responses)})()
    sleep = AsyncMock()
    monkeypatch.setattr(upstream_retry.asyncio, 'sleep', sleep)
    result = asyncio.run(upstream_retry.request_with_rate_limit_retry(session, method='POST', data='unchanged'))
    assert result is responses[-1]
    assert [r.released for r in responses] == [True, True, False]
    assert [c.args[0] for c in sleep.call_args_list] == [3, 6]
    assert session.request.call_count == 3
    assert all(c.kwargs['data'] == 'unchanged' for c in session.request.call_args_list)


@pytest.mark.parametrize('response', [Response(200), Response(400), Response(500), Response(429, 'insufficient_quota'), Response(429, retry_after='60')])
def test_no_retry_after_success_other_errors_or_long_quota_wait(response):
    session = type('Session', (), {'request': AsyncMock(return_value=response)})()
    assert asyncio.run(upstream_retry.request_with_rate_limit_retry(session)) is response
    assert session.request.call_count == 1
    assert not response.released


def test_retry_after_is_honored_and_invalid_values_are_bounded():
    assert upstream_retry.retry_delay('12', 0, '') == 12
    assert upstream_retry.retry_delay('NaN', 0, '') is None
    assert upstream_retry.retry_delay('15', 1, '', 15) is None
    assert upstream_retry.retry_delay('invalid', 1, '') == 6


@pytest.mark.parametrize('arguments', ['{"plan":', '[]', 'null', '123'])
def test_malformed_tool_history_does_not_poison_next_request(arguments):
    output = [
        {'type': 'function_call', 'call_id': 'failed', 'name': 'test_tool', 'arguments': arguments},
        {'type': 'function_call_output', 'call_id': 'failed', 'output': [{'type': 'input_text', 'text': 'Error: tool arguments could not be parsed; not executed.'}]},
    ]
    original = copy.deepcopy(output)
    messages = convert_output_to_messages(output)
    assert json.loads(messages[0]['tool_calls'][0]['function']['arguments']) == {'_unparsed_arguments': arguments}
    assert 'not executed' in messages[1]['content']
    assert output == original


def test_error_classification_and_zero_usage_are_not_reservations():
    code, message = _channel_runtime_failure("{'status': 429, 'title': 'Too Many Requests'}")
    assert code == 'AI-UPSTREAM-RATE-LIMITED'
    assert 'AI-RUNTIME-FAILED' not in message
    assert _channel_result_tokens({'errorCode': code, 'usage': None}, 12343) == 0
    assert _channel_result_tokens({'usage': {'total_tokens': 0}}, 12000) == 0
    assert _channel_result_tokens({'errorCode': code, 'usage': {'input_tokens': 400, 'output_tokens': 100}}, 12000) == _usage_tokens({'input_tokens': 400, 'output_tokens': 100})
    assert _channel_result_tokens({'usage': None}, 12000) == 12000


def test_semantic_invalid_selection_returns_specific_field_without_execution(monkeypatch):
    monkeypatch.setattr(interact_semantic, 'runtime_context', AsyncMock(return_value=object()))
    execute = AsyncMock()
    monkeypatch.setattr(interact_semantic, 'execute_query', execute)
    plan = {'datasetId': 'test', 'dimensions': {'item': [str(i) for i in range(12)]}}
    result = json.loads(asyncio.run(interact_semantic.interact_semantic_query(plan)))
    assert result['ok'] is False
    assert result['validationIssues'][0]['field'] == 'dimensions'
    assert '8' in result['validationIssues'][0]['message']
    execute.assert_not_called()


def test_valid_semantic_plan_still_uses_authorized_query_service(monkeypatch):
    context = object()
    monkeypatch.setattr(interact_semantic, 'runtime_context', AsyncMock(return_value=context))
    execute = AsyncMock(return_value={'ok': True, 'rows': []})
    monkeypatch.setattr(interact_semantic, 'execute_query', execute)
    plan = {'datasetId': 'test', 'dimensions': {'item': ['name']}}
    assert json.loads(asyncio.run(interact_semantic.interact_semantic_query(plan)))['ok'] is True
    execute.assert_awaited_once_with(plan, context)
