import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from open_webui.utils import upstream_retry


class Response:
    def __init__(self, status, body='', headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.released = False
    async def text(self):
        return self.body
    def release(self):
        self.released = True


@pytest.mark.parametrize('status', [429, 502, 503, 504])
def test_explicit_rejection_retries_only_provider_request(monkeypatch, status):
    responses = [Response(status), Response(status), Response(200)]
    session = type('Session', (), {'request': AsyncMock(side_effect=responses)})()
    sleep = AsyncMock()
    monkeypatch.setattr(upstream_retry.asyncio, 'sleep', sleep)
    assert asyncio.run(upstream_retry.request_with_rate_limit_retry(session, method='POST', data='same')) is responses[2]
    assert [r.released for r in responses] == [True, True, False]
    assert [c.args[0] for c in sleep.call_args_list] == [3, 6]
    assert all(c.kwargs['data'] == 'same' for c in session.request.call_args_list)


@pytest.mark.parametrize('response', [Response(200), Response(400), Response(401), Response(500), Response(503, 'quota_exceeded'), Response(503, headers={'Retry-After':'60'})])
def test_nonretryable_response_is_preserved(monkeypatch, response):
    session = type('Session', (), {'request': AsyncMock(return_value=response)})()
    assert asyncio.run(upstream_retry.request_with_rate_limit_retry(session)) is response
    assert session.request.call_count == 1
    assert not response.released


def test_three_rejections_stop(monkeypatch):
    responses = [Response(503) for _ in range(3)]
    session = type('Session', (), {'request': AsyncMock(side_effect=responses)})()
    monkeypatch.setattr(upstream_retry.asyncio, 'sleep', AsyncMock())
    assert asyncio.run(upstream_retry.request_with_rate_limit_retry(session)) is responses[-1]
    assert session.request.call_count == 3


def test_busy_error_is_actionable_not_unknown():
    path = Path(__file__).parents[1] / 'open_webui/routers/interact_channels.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_channel_runtime_failure'
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CHANNEL_RUNTIME_ERROR_MESSAGES' for t in n.targets)]
    namespace = {'provider_quota_exhausted':upstream_retry.provider_quota_exhausted}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    code, message = namespace['_channel_runtime_failure']('Upstream HTTP 503 during tool continuation.')
    assert code == 'AI-UPSTREAM-UNAVAILABLE'
    assert '工作紀錄' in message
    code, message = namespace['_channel_runtime_failure']("{'code': 429, 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}")
    assert code == 'AI-UPSTREAM-QUOTA-EXHAUSTED'
    assert '不是 CRM 儲值餘額' in message
    assert upstream_retry.retry_delay(None,0,'GenerateRequestsPerDayPerProjectPerModel-FreeTier') is None
    assert namespace['_channel_runtime_failure']("{'code': 429, 'status': 'RESOURCE_EXHAUSTED'}")[0] == 'AI-UPSTREAM-RATE-LIMITED'
