import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.interact_channels import InteractEventClaim
from open_webui.routers.interact_channels import (
    ChannelChatRequest,
    _channel_request_messages,
    _claim_and_respond,
    _estimated_reservation_tokens,
    _generate_context_summary,
    _prepare_channel_context,
    _process_channel_job,
    _response_content,
    _response_event_content,
    _should_summarize_context,
    _summary_history_state,
    _usage_tokens,
    channel_chat,
    delete_channel,
    platform_webhook,
)
from starlette.requests import Request
from starlette.responses import StreamingResponse


def _payload(message: str = 'latest question') -> ChannelChatRequest:
    return ChannelChatRequest(
        companyEmail='company@example.com',
        channelType='line',
        channelIdentifier='line-channel',
        externalUserId='line-user',
        modelId='model',
        message=message,
        system='system prompt',
    )


def _messages(count: int, content: str = 'message') -> list[dict]:
    return [
        {
            'id': f'message-{index}',
            'role': 'user' if index % 2 == 0 else 'assistant',
            'content': f'{content}-{index}',
        }
        for index in range(count)
    ]


def _request(
    body: bytes,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b'',
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {'type': 'http.disconnect'}
        sent = True
        return {'type': 'http.request', 'body': body, 'more_body': False}

    return Request(
        {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'POST',
            'scheme': 'https',
            'path': '/webhook',
            'raw_path': b'/webhook',
            'query_string': query_string,
            'headers': headers or [],
            'client': ('127.0.0.1', 1234),
            'server': ('testserver', 443),
        },
        receive,
    )


def test_summary_state_keeps_only_messages_after_marker():
    messages = _messages(8)
    chat = SimpleNamespace(
        chat={
            'meta': {
                'contextSummary': {
                    'text': 'older memory',
                    'throughMessageId': 'message-3',
                }
            }
        }
    )

    summary, remaining = _summary_history_state(chat, messages)

    assert summary == 'older memory'
    assert [message['id'] for message in remaining] == [
        'message-4',
        'message-5',
        'message-6',
        'message-7',
    ]


def test_stale_summary_marker_is_ignored():
    messages = _messages(4)
    chat = SimpleNamespace(
        chat={
            'meta': {
                'contextSummary': {
                    'text': 'stale memory',
                    'throughMessageId': 'missing',
                }
            }
        }
    )

    assert _summary_history_state(chat, messages) == ('', messages)


@pytest.mark.asyncio
async def test_claim_and_respond_returns_fallback_when_channel_is_disabled(monkeypatch):
    async def claim_event(*args, **kwargs):
        return InteractEventClaim(
            allowed=False,
            duplicate=False,
            event_id='',
            reason='channel-disabled',
        )

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('disabled channel must not invoke the model')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_event',
        claim_event,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._run_channel_message',
        unexpected_model_call,
    )

    channel = SimpleNamespace(reply_mode='ai', fallback_message='service paused')
    claim, content = await _claim_and_respond(
        SimpleNamespace(),
        channel,
        'event',
        'external-user',
        'hello',
    )

    assert claim.reason == 'channel-disabled'
    assert content == 'service paused'


@pytest.mark.asyncio
async def test_claim_and_respond_stops_when_channel_was_deleted(monkeypatch):
    async def claim_event(*args, **kwargs):
        return InteractEventClaim(
            allowed=False,
            duplicate=False,
            event_id='',
            reason='channel-deleted',
        )

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('deleted channel must not invoke the model')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_event',
        claim_event,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._run_channel_message',
        unexpected_model_call,
    )

    channel = SimpleNamespace(reply_mode='ai', fallback_message='service paused')
    with pytest.raises(HTTPException) as error:
        await _claim_and_respond(
            SimpleNamespace(),
            channel,
            'event',
            'external-user',
            'hello',
        )

    assert error.value.status_code == 404


async def _disabled_claim(*args, **kwargs):
    return InteractEventClaim(
        allowed=False,
        duplicate=False,
        event_id='',
        reason='channel-disabled',
    )


async def _mark_delivery_noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_disabled_line_channel_replies_with_fallback(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=False,
        reply_mode='silent',
        fallback_message='service paused',
        channel_secret='line-secret',
        channel_access_token='line-access-token',
    )
    body = json.dumps(
        {
            'events': [
                {
                    'type': 'message',
                    'replyToken': 'reply-token',
                    'webhookEventId': 'event-id',
                    'source': {'type': 'group', 'groupId': 'line-group', 'userId': 'line-user'},
                    'message': {'type': 'text', 'text': 'hello'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(
        hmac.new(b'line-secret', body, hashlib.sha256).digest()
    )
    replies = []

    async def get_channel(*args):
        return channel

    async def send_reply(channel, reply_token, text):
        replies.append((reply_token, text))

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._platform_channel',
        get_channel,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_event',
        _disabled_claim,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._send_line_reply',
        send_reply,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._mark_delivery',
        _mark_delivery_noop,
    )

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert replies == [('reply-token', 'service paused')]
    assert json.loads(response.body)['results'][0]['rateLimited'] is False


@pytest.mark.asyncio
async def test_enabled_line_webhook_queues_without_waiting_for_model(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=True,
        reply_mode='ai',
        fallback_message='fallback',
        channel_secret='line-secret',
        channel_access_token='line-access-token',
    )
    body = json.dumps(
        {
            'events': [
                {
                    'type': 'message',
                    'replyToken': 'reply-token',
                    'webhookEventId': 'event-id',
                    'source': {'type': 'group', 'groupId': 'line-group', 'userId': 'line-user'},
                    'message': {'type': 'text', 'text': 'hello'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    queued = []
    replies = []

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        return InteractEventClaim(allowed=True, duplicate=False, event_id='claimed-event')

    async def enqueue_job(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id='job-id')

    async def send_reply(channel, reply_token, text):
        replies.append((reply_token, text))

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('LINE webhook must not wait for the model')

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels._complete_claimed_result', unexpected_model_call)
    monkeypatch.setattr('open_webui.routers.interact_channels.wake_channel_job_workers', lambda: None)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    result = json.loads(response.body)
    assert response.status_code == 200
    assert result['results'][0]['queued'] is True
    assert queued[0]['event_id'] == 'claimed-event'
    assert queued[0]['external_user_id'] == 'line-user'
    assert queued[0]['conversation_id'] == 'line-group'
    assert queued[0]['payload']['recipientId'] == 'line-group'
    assert replies == [('reply-token', '已收到，我正在查詢資料並整理答案。完成後會直接傳送結果。')]


@pytest.mark.asyncio
async def test_delivery_retry_reuses_saved_result_without_rerunning_model(monkeypatch):
    channel = SimpleNamespace(id='channel-id', enabled=True)
    saved_result = {'content': 'done', 'outputs': []}
    job = SimpleNamespace(
        id='job-id',
        event_id='event-id',
        channel_id='channel-id',
        external_user_id='line-user',
        platform='line',
        payload={'recipientId': 'line-user', 'platformEventId': 'platform-event', 'message': 'hello'},
        result=saved_result,
        attempts=2,
    )
    deliveries = []
    completed = []

    async def get_channel(channel_id):
        return channel

    async def send_result(channel, recipient, result, retry_key=None):
        deliveries.append((recipient, result, retry_key))

    async def complete_job(job_id):
        completed.append(job_id)

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('delivery retry must not rerun the model or workflow')

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.extend_job_lease', lambda *args: True)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.complete_job', complete_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_push_result', send_result)
    monkeypatch.setattr('open_webui.routers.interact_channels._complete_claimed_result', unexpected_model_call)

    await _process_channel_job(SimpleNamespace(state=SimpleNamespace()), 'worker', job, 300, 5)

    assert deliveries == [('line-user', saved_result, 'job-id')]
    assert completed == ['job-id']


@pytest.mark.asyncio
async def test_disabled_telegram_channel_replies_with_fallback(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=False,
        reply_mode='silent',
        fallback_message='service paused',
        channel_secret='telegram-bot-token',
        channel_access_token='telegram-webhook-secret',
    )
    body = json.dumps(
        {
            'update_id': 123,
            'message': {
                'text': 'hello',
                'chat': {'id': 456},
                'from': {'id': 789},
            },
        }
    ).encode()
    replies = []

    async def get_channel(*args):
        return channel

    async def send_reply(channel, chat_id, text):
        replies.append((chat_id, text))

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._platform_channel',
        get_channel,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_event',
        _disabled_claim,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._send_telegram_reply',
        send_reply,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._mark_delivery',
        _mark_delivery_noop,
    )

    response = await platform_webhook(
        _request(
            body,
            [
                (
                    b'x-telegram-bot-api-secret-token',
                    b'telegram-webhook-secret',
                )
            ],
        ),
        'telegram',
        'telegram-channel',
    )

    assert response['rateLimited'] is False
    assert replies == [(456, 'service paused')]


@pytest.mark.asyncio
async def test_disabled_wechat_channel_replies_with_fallback(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=False,
        reply_mode='silent',
        fallback_message='service paused',
        channel_secret='wechat-app-secret',
        channel_access_token='wechat-verification-token',
    )
    body = (
        b'<xml>'
        b'<ToUserName><![CDATA[official-account]]></ToUserName>'
        b'<FromUserName><![CDATA[wechat-user]]></FromUserName>'
        b'<MsgType><![CDATA[text]]></MsgType>'
        b'<Content><![CDATA[hello]]></Content>'
        b'<MsgId>123</MsgId>'
        b'</xml>'
    )
    timestamp = '123456'
    nonce = 'nonce'
    signature = hashlib.sha1(
        ''.join(
            sorted(['wechat-verification-token', timestamp, nonce])
        ).encode()
    ).hexdigest()
    query = f'signature={signature}&timestamp={timestamp}&nonce={nonce}'.encode()

    async def get_channel(*args):
        return channel

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._platform_channel',
        get_channel,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_event',
        _disabled_claim,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._mark_delivery',
        _mark_delivery_noop,
    )

    response = await platform_webhook(
        _request(body, query_string=query),
        'wechat',
        'wechat-channel',
    )

    assert response.status_code == 200
    assert 'service paused' in response.body.decode()


@pytest.mark.asyncio
async def test_delete_channel_falls_back_to_verified_platform_identity(monkeypatch):
    platform_channel = SimpleNamespace(
        id='current-id',
        company_email='company@example.com',
        channel_type='line',
        channel_identifier='line-channel',
    )
    deleted_ids = []

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )

    async def get_by_id(channel_id):
        return None

    async def get_by_platform(channel_type, channel_identifier):
        return platform_channel

    async def delete(channel_id):
        deleted_ids.append(channel_id)
        return True

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_id',
        get_by_id,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_platform',
        get_by_platform,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.delete',
        delete,
    )

    result = await delete_channel(
        'legacy-id',
        'company@example.com',
        'line',
        'line-channel',
        None,
        None,
    )

    assert result['deleted'] is True
    assert result['channelId'] == 'current-id'
    assert deleted_ids == ['current-id']


@pytest.mark.asyncio
async def test_delete_channel_rejects_another_company(monkeypatch):
    requested_channel = SimpleNamespace(
        id='channel-id',
        company_email='other@example.com',
        channel_type='line',
        channel_identifier='line-channel',
    )

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )

    async def get_by_id(channel_id):
        return requested_channel

    async def get_by_platform(channel_type, channel_identifier):
        return requested_channel

    async def unexpected_delete(channel_id):
        raise AssertionError('another company channel must not be deleted')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_id',
        get_by_id,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_platform',
        get_by_platform,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.delete',
        unexpected_delete,
    )

    with pytest.raises(HTTPException) as error:
        await delete_channel(
            'channel-id',
            'company@example.com',
            'line',
            'line-channel',
            None,
            None,
        )

    assert error.value.status_code == 409


def test_request_contains_system_memory_recent_history_and_latest_question():
    request_messages = _channel_request_messages(
        _payload(),
        'older memory',
        _messages(2),
    )

    assert [message['role'] for message in request_messages] == [
        'system',
        'assistant',
        'user',
        'assistant',
        'user',
    ]
    assert request_messages[1]['content'].endswith('older memory')
    assert request_messages[-1]['content'] == 'latest question'


def test_summary_triggers_for_token_threshold_or_message_limit():
    assert _should_summarize_context(
        _payload(),
        '',
        _messages(14, content='x' * 2_000),
        trigger_tokens=2_000,
        recent_message_count=12,
    )
    assert _should_summarize_context(
        _payload(),
        '',
        _messages(41),
        trigger_tokens=100_000,
        recent_message_count=12,
    )
    assert not _should_summarize_context(
        _payload(),
        '',
        _messages(12, content='x' * 2_000),
        trigger_tokens=2_000,
        recent_message_count=12,
    )


def test_channel_limits_use_weighted_billable_tokens():
    assert (
        _usage_tokens(
            {
                'prompt_tokens': 100,
                'completion_tokens': 20,
            }
        )
        == 200
    )
    assert _estimated_reservation_tokens('hello') > 2048


def test_stream_event_content_supports_openai_and_wrapped_events():
    assert _response_event_content({'choices': [{'delta': {'content': 'partial'}}]}) == ('', 'partial')
    assert _response_event_content({'data': {'choices': [{'message': {'content': 'complete'}}]}}) == ('complete', '')


@pytest.mark.asyncio
async def test_streaming_summary_content_is_collected():
    async def body():
        yield 'data: {"choices":[{"delta":{"content":"first "}}]}\n\n'
        yield 'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
        yield 'data: [DONE]\n\n'

    assert await _response_content(StreamingResponse(body(), media_type='text/event-stream')) == 'first second'


@pytest.mark.asyncio
async def test_channel_chat_awaits_model_features(monkeypatch):
    captured = {}
    captured_runtime = {}
    user = SimpleNamespace(id='shared-user', role='user')
    chat = SimpleNamespace(chat={'history': {'currentId': None}})

    monkeypatch.setattr('open_webui.routers.interact_channels._require_service_token', lambda *args: None)

    async def get_user(email):
        return user

    async def ensure_chat(*args):
        return chat

    async def prepare_context(*args):
        return [{'role': 'user', 'content': 'hello'}], 0

    async def resolve_features(*args):
        return {'image_generation': True}

    async def select_workflow(*args, **kwargs):
        return SimpleNamespace(decision='none', selected_workflow_id=None, selected_version_id=None)

    async def get_message(*args):
        return {
            'content': '',
            'output': [
                {
                    'type': 'reasoning',
                    'content': [{'type': 'output_text', 'text': 'private reasoning'}],
                },
                {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'done'}],
                },
            ],
        }

    async def handler(request, form_data, user):
        captured.update(form_data)
        captured_runtime['trusted'] = request.state.interact_channel_runtime
        return {}

    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', get_user)
    monkeypatch.setattr('open_webui.routers.interact_channels._ensure_channel_chat', ensure_chat)
    monkeypatch.setattr('open_webui.routers.interact_channels._prepare_channel_context', prepare_context)
    monkeypatch.setattr('open_webui.routers.interact_channels._resolve_model_features', resolve_features)
    monkeypatch.setattr('open_webui.routers.interact_channels.Chats.get_message_by_id_and_message_id', get_message)
    monkeypatch.setattr('open_webui.routers.workflows.select_workflow_for_user_context', select_workflow)

    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'model': {}},
                CHAT_COMPLETION_HANDLER=handler,
            )
        )
    )
    response = await channel_chat(request, _payload(), None, None)

    assert captured['features'] == {'image_generation': True}
    assert captured_runtime['trusted'] is True
    assert response['content'] == 'done'


@pytest.mark.asyncio
async def test_summary_generation_uses_internal_channel_operation(monkeypatch):
    captured = {}

    async def handler(request, form_data, user):
        captured.update(form_data)
        return {
            'choices': [{'message': {'content': 'compact memory'}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 3},
        }

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.generate_chat_completion',
        handler,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.is_billing_enabled',
        lambda: False,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    summary, billable_tokens = await _generate_context_summary(
        request,
        SimpleNamespace(id='user'),
        'model',
        _payload(),
        '',
        _messages(4),
        500,
    )

    assert summary == 'compact memory'
    assert billable_tokens == 35
    assert captured['metadata']['interact_channel']['operation'] == 'context-summary'


@pytest.mark.asyncio
async def test_daily_channel_limit_skips_summary_before_model_call(monkeypatch):
    history = _messages(14, content='x' * 2_000)
    chat = SimpleNamespace(
        id='chat',
        chat={
            'history': {'currentId': 'message-13', 'messages': {}},
            'meta': {},
        },
    )
    payload = _payload()
    payload.metadata = {
        'channelEventId': 'event',
        'dailyBotTokenLimit': 100,
    }

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.get_message_list',
        lambda messages, current_id: history,
    )

    async def get_chat(chat_id, user_id):
        return chat

    async def reserve(*args, **kwargs):
        return False

    async def unexpected_summary(*args, **kwargs):
        raise AssertionError('summary model must not run past the daily limit')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.Chats.get_chat_by_id_and_user_id',
        get_chat,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.reserve_event_tokens',
        reserve,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._generate_context_summary',
        unexpected_summary,
    )

    messages, summary_tokens = await _prepare_channel_context(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
        SimpleNamespace(id='user'),
        chat,
        'model',
        payload,
    )

    assert summary_tokens == 0
    assert messages[-1]['content'] == 'latest question'
