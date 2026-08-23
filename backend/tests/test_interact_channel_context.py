import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.interact_channels import InteractEventClaim
from open_webui.routers.interact_channels import (
    CRM_AM_ACTION_INSTRUCTION,
    CRM_BD_ACTION_INSTRUCTION,
    ChannelChatRequest,
    _channel_output_text,
    _channel_request_messages,
    _claim_and_respond,
    _complete_claimed_result,
    _estimated_reservation_tokens,
    _generate_context_summary,
    _line_delivery_messages,
    _line_filter_workflow_options,
    _line_result_messages,
    _line_workflow_menu_message,
    _line_workflow_menu_requested,
    _line_workflow_postback,
    _prepare_channel_context,
    _process_channel_job,
    _response_content,
    _response_event_content,
    _run_channel_message,
    _should_summarize_context,
    _stable_channel_chat_id,
    _summary_history_state,
    _usage_tokens,
    channel_chat,
    delete_channel,
    platform_webhook,
)
from starlette.requests import Request
from starlette.responses import StreamingResponse


def test_am_instruction_names_the_only_allowed_crm_write_apis():
    assert 'interact_crm_follow_up_create' in CRM_AM_ACTION_INSTRUCTION
    assert 'interact_crm_follow_up_update' in CRM_AM_ACTION_INSTRUCTION
    assert 'Never use interact_crm_bd_discovery_start' in CRM_AM_ACTION_INSTRUCTION
    assert 'never write CRM tables directly' in CRM_AM_ACTION_INSTRUCTION


def test_bd_instruction_allows_public_business_contacts_but_blocks_am_writes():
    assert 'public business contact pages' in CRM_BD_ACTION_INSTRUCTION
    assert 'verifiable corporate phone numbers and email addresses' in CRM_BD_ACTION_INSTRUCTION
    assert 'interact_crm_bd_discovery_start' in CRM_BD_ACTION_INSTRUCTION
    assert 'interact_crm_bd_profile_suggestion_create' in CRM_BD_ACTION_INSTRUCTION
    assert 'Never use interact_crm_follow_up_create' in CRM_BD_ACTION_INSTRUCTION
    assert 'never write CRM tables directly' in CRM_BD_ACTION_INSTRUCTION


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


def test_channel_chat_id_isolated_when_knowledge_scope_changes():
    payload = _payload()
    legacy = _stable_channel_chat_id('user', payload)

    first = _stable_channel_chat_id(
        'user',
        payload,
        knowledge_scope_fingerprint='scope-a',
    )
    second = _stable_channel_chat_id(
        'user',
        payload,
        knowledge_scope_fingerprint='scope-b',
    )

    assert legacy != first
    assert first != second


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


def _active_line_identity():
    return SimpleNamespace(
        id='binding-id',
        company_user_id='company-id',
        company_member_id='member-id',
        member_email='member@example.com',
        member_role='member',
        member_status='active',
        group_ids=[],
        role_verified_at=9999999999,
    )


def test_channel_text_output_uses_its_text_instead_of_type_label():
    assert _channel_output_text({'type': 'text', 'text': '郵件已送出。'}) == '郵件已送出。'


def test_line_result_deduplicates_content_and_text_output():
    messages = _line_result_messages(
        {
            'content': '郵件已送出。',
            'outputs': [{'type': 'text', 'text': '郵件已送出。'}],
        }
    )

    assert messages == [{'type': 'text', 'text': '郵件已送出。'}]


def test_line_result_accepts_card_without_plain_text():
    messages = _line_result_messages(
        {
            'content': '',
            'outputs': [
                {
                    'type': 'card',
                    'title': '確認寄送郵件',
                    'body': '確認後才會寄送。',
                    'data': {
                        'workflow_id': 'workflow-id',
                        'run_id': 'run-id',
                        'revision': 1,
                    },
                    'actions': [
                        {
                            'type': 'workflow_resume',
                            'label': '確認寄送',
                            'decision': 'approved',
                        }
                    ],
                }
            ],
        }
    )

    assert len(messages) == 1
    assert messages[0]['type'] == 'flex'
    assert messages[0]['altText'] == '確認寄送郵件'


@pytest.mark.asyncio
async def test_complete_claimed_result_does_not_replace_structured_output_with_fallback(monkeypatch):
    result = {
        'ok': True,
        'content': '',
        'outputs': [{'type': 'card', 'title': '確認寄送郵件'}],
    }
    saved = {}

    async def run_message(*args, **kwargs):
        return result

    async def set_response(event_id, content, tokens, reason):
        saved.update(event_id=event_id, content=content, tokens=tokens, reason=reason)

    monkeypatch.setattr('open_webui.routers.interact_channels._run_channel_message', run_message)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', set_response)

    completed = await _complete_claimed_result(
        SimpleNamespace(),
        SimpleNamespace(reply_mode='ai', fallback_message='不應出現的 fallback'),
        SimpleNamespace(event_id='event-id'),
        'line-user',
        'platform-event',
        '寄信',
    )

    assert completed['content'] == ''
    assert completed['outputs'] == result['outputs']
    assert saved['content'] == ''


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
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
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
        access_mode='public',
        model_id='support-agent',
        agent_bindings=[{
            'modelId': 'support-agent',
            'label': 'Support Agent',
            'role': 'general',
            'enabled': True,
            'isDefault': True,
        }],
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
                    'source': {'type': 'user', 'userId': 'line-user'},
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

    async def no_workflow(*args, **kwargs):
        return None

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('LINE webhook must not wait for the model')

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_selected_workflow_request', no_workflow)
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
    assert queued[0]['conversation_id'] == 'line-user'
    assert queued[0]['payload']['recipientId'] == 'line-user'
    assert 'workflowId' not in queued[0]['payload']
    assert replies == []


@pytest.mark.asyncio
async def test_selected_workflow_request_gets_progress_reply(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=True,
        reply_mode='ai',
        access_mode='public',
        model_id='support-agent',
        agent_bindings=[{
            'modelId': 'support-agent',
            'label': 'Support Agent',
            'role': 'general',
            'enabled': True,
            'isDefault': True,
        }],
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
                    'webhookEventId': 'workflow-message-event',
                    'source': {'type': 'user', 'userId': 'line-user'},
                    'message': {'type': 'text', 'text': '查詢簽約銷售額前五名'},
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
        return InteractEventClaim(allowed=True, duplicate=False, event_id='claimed-workflow-message')

    async def select_workflow(*args, **kwargs):
        return 'workflow-id', 'version-id'

    async def enqueue_job(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id='workflow-message-job')

    async def send_reply(channel, reply_token, text):
        replies.append((reply_token, text))

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_selected_workflow_request', select_workflow)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels.wake_channel_job_workers', lambda: None)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert queued[0]['payload']['workflowId'] == 'workflow-id'
    assert queued[0]['payload']['workflowVersionId'] == 'version-id'
    assert queued[0]['payload']['workflowTrigger'] == 'line.message'
    assert replies == [('reply-token', '已收到，我正在查詢資料並整理答案。完成後會直接傳送結果。')]


@pytest.mark.asyncio
async def test_line_workflow_postback_queues_exact_published_version(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=True,
        reply_mode='ai',
        fallback_message='fallback',
        channel_secret='line-secret',
        channel_access_token='line-access-token',
    )
    postback_data = json.dumps(
        {'a': 'workflow.run.v1', 'w': 'workflow-id', 'v': 'version-id'},
        separators=(',', ':'),
    )
    body = json.dumps(
        {
            'events': [
                {
                    'type': 'postback',
                    'replyToken': 'reply-token',
                    'webhookEventId': 'event-id',
                    'source': {'type': 'user', 'userId': 'line-user'},
                    'postback': {'data': postback_data},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    queued = []

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        return InteractEventClaim(allowed=True, duplicate=False, event_id='claimed-event')

    async def enqueue_job(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id='job-id')

    async def workflow_options(*args, **kwargs):
        return [
            {
                'id': 'workflow-id',
                'versionId': 'version-id',
                'name': '立即查詢',
                'launchMode': 'instant',
            }
        ]

    async def refresh_identity(*args, **kwargs):
        return _active_line_identity()

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', workflow_options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', refresh_identity)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', _mark_delivery_noop)
    monkeypatch.setattr('open_webui.routers.interact_channels.wake_channel_job_workers', lambda: None)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert queued[0]['payload']['workflowId'] == 'workflow-id'
    assert queued[0]['payload']['workflowVersionId'] == 'version-id'
    assert queued[0]['payload']['workflowTrigger'] == 'line.quick_action'
    assert queued[0]['payload']['message'] == '執行 LINE 快速工作流'
    assert _line_workflow_postback({'postback': {'data': 'not-json'}}) is None


@pytest.mark.asyncio
async def test_unbound_line_user_cannot_execute_workflow_postback(monkeypatch):
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
                    'type': 'postback',
                    'replyToken': 'reply-token',
                    'webhookEventId': 'unbound-workflow-event',
                    'source': {'type': 'user', 'userId': 'unbound-line-user'},
                    'postback': {'data': '{"a":"workflow.launch.v2","w":"workflow-id"}'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    replies = []
    queued = []

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        return InteractEventClaim(allowed=True, duplicate=False, event_id='unbound-claim')

    async def no_identity(*args, **kwargs):
        return None

    async def send_reply(channel, reply_token, text):
        replies.append(text)

    async def no_op(*args, **kwargs):
        return None

    async def enqueue_job(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', no_identity)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels._mark_delivery', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert queued == []
    assert replies == [
        '無法確認您的企業身分，這次動作沒有執行。\n請先點選「綁定帳號」；若已綁定，請稍後重試或到主站台檢查帳號狀態。'
    ]
    assert json.loads(response.body)['results'][0]['identityRequired'] is True


@pytest.mark.asyncio
async def test_line_account_link_text_command_issues_secure_link(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=True,
        reply_mode='silent',
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
                    'webhookEventId': 'account-link-command-event',
                    'source': {'type': 'user', 'userId': 'line-user'},
                    'message': {'type': 'text', 'text': '綁定帳號'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    issued = []
    delivery_errors = []

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        return InteractEventClaim(allowed=True, duplicate=False, event_id='account-link-claim')

    async def no_identity(*args, **kwargs):
        return None

    async def issue_link(channel, user_id, reply_token):
        issued.append((user_id, reply_token))

    async def no_op(*args, **kwargs):
        return None

    async def mark_delivery(claim, error=None):
        delivery_errors.append(error)

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', no_identity)
    monkeypatch.setattr('open_webui.routers.interact_channels._issue_line_account_link', issue_link)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels._mark_delivery', mark_delivery)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200, (response.body, delivery_errors)
    assert issued == [('line-user', 'reply-token')]
    assert json.loads(response.body)['results'][0]['accountLinkIssued'] is True


@pytest.mark.asyncio
async def test_line_account_link_event_assigns_role_menu_once(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        enabled=True,
        reply_mode='ai',
        channel_secret='line-secret',
        channel_access_token='line-access-token',
    )
    account_link_event = {
        'type': 'accountLink',
        'replyToken': 'reply-token',
        'source': {'type': 'user', 'userId': 'line-user'},
        'link': {'result': 'ok', 'nonce': 'one-use-nonce'},
    }
    body = json.dumps({'events': [account_link_event, account_link_event]}).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    replies = []
    assignments = []
    consume_count = 0

    async def get_channel(*args):
        return channel

    async def consume(**kwargs):
        nonlocal consume_count
        consume_count += 1
        return _active_line_identity() if consume_count == 1 else None

    async def assign(channel, user_id, audience):
        assignments.append((user_id, audience))

    async def send_reply(channel, reply_token, text):
        replies.append(text)

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.consume_line_identity_link',
        consume,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_assign_role_menu', assign)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert assignments == [('line-user', 'member')]
    assert '帳號已綁定：member@example.com' in replies[0]
    assert replies[1] == '綁定未完成或連結已失效，請從選單重新發起綁定。'
    assert [item['accountLinked'] for item in json.loads(response.body)['results']] == [True, False]


@pytest.mark.asyncio
async def test_line_guided_workflow_postback_prompts_without_execution(monkeypatch):
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
                    'type': 'postback',
                    'replyToken': 'reply-token',
                    'webhookEventId': 'guided-event',
                    'source': {'type': 'user', 'userId': 'line-user'},
                    'postback': {'data': '{"a":"workflow.launch.v2","w":"guided-id"}'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    replies = []
    guided_option = {
        'id': 'guided-id',
        'versionId': 'guided-version',
        'name': '建立月報',
        'launchMode': 'form_input',
        'instruction': '請設定報表月份。',
        'inputSchema': {
            'type': 'object',
            'properties': {'month': {'type': 'string', 'title': '月份'}},
            'required': ['month'],
            'additionalProperties': False,
        },
        'defaultInput': {},
        'fileRules': {},
    }

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        return InteractEventClaim(allowed=True, duplicate=False, event_id='guided-claim')

    async def workflow_options(*args, **kwargs):
        return [guided_option]

    async def refresh_identity(*args, **kwargs):
        return _active_line_identity()

    async def start_session(**kwargs):
        return SimpleNamespace(id='session-id', **kwargs)

    async def send_reply(channel, reply_token, text):
        replies.append(text)

    async def no_op(*args, **kwargs):
        return None

    async def unexpected_enqueue(**kwargs):
        raise AssertionError('guided workflow must wait for user input')

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', workflow_options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', refresh_identity)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.start_workflow_session',
        start_session,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', unexpected_enqueue)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels._mark_delivery', no_op)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload['results'][0]['workflowInputPending'] is True
    assert '第 1/1 項：月份（必填）' in replies[0]


def test_line_workflow_menu_is_compact_accessible_flex_message():
    options = [
        {
            'id': 'workflow-sales',
            'versionId': 'version-sales',
            'name': '簽約銷售額前 5 名',
            'description': '依簽約總額由高到低排序',
        },
        {
            'id': 'workflow-followups',
            'versionId': 'version-followups',
            'name': '跟進頻率前 5 名',
            'description': '比較員工跟進事件數',
        },
    ]

    message = _line_workflow_menu_message(options)
    serialized = json.dumps(message, ensure_ascii=False)

    assert message['type'] == 'flex'
    assert message['altText'] == '全部工作流選單'
    assert '簽約銷售額前 5 名' in serialized
    assert '跟進頻率前 5 名' in serialized
    assert serialized.count('workflow.run.v1') == 2
    assert '僅顯示此公司、模型與 LINE 渠道有權使用' in serialized
    assert _line_workflow_menu_requested(' 工作流選單！ ')
    assert not _line_workflow_menu_requested('請幫我執行工作流查詢銷售額')


def test_line_workflow_menu_caps_carousel_and_omits_blank_descriptions():
    options = [
        {
            'id': f'workflow-{index}',
            'versionId': f'version-{index}',
            'name': f'工作流 {index}',
            'description': '   ',
            'launchMode': 'file_input' if index % 3 == 0 else 'form_input',
        }
        for index in range(100)
    ]

    message = _line_workflow_menu_message(options)
    serialized = json.dumps(message, ensure_ascii=False)

    assert message['contents']['type'] == 'carousel'
    assert len(message['contents']['contents']) == 12
    assert serialized.count('workflow.run.v1') == 72
    assert '顯示前 72 個，共 100 個可用工作流' in serialized
    assert '"text": ""' not in serialized
    assert '需要檔案' in serialized


def test_line_workflow_categories_do_not_duplicate_file_workflows():
    options = [
        {'id': 'instant', 'launchMode': 'instant'},
        {'id': 'guided', 'launchMode': 'form_input'},
        {'id': 'file', 'launchMode': 'file_input'},
    ]

    guided, guided_title = _line_filter_workflow_options(options, 'guided')
    files, file_title = _line_filter_workflow_options(options, 'file')

    assert guided_title == '需要輸入'
    assert [item['id'] for item in guided] == ['guided']
    assert file_title == '需要檔案'
    assert [item['id'] for item in files] == ['file']


@pytest.mark.asyncio
async def test_line_group_chat_is_blocked_before_identity_or_ai_execution(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
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
                    'webhookEventId': 'group-event',
                    'source': {
                        'type': 'group',
                        'groupId': 'line-group',
                        'userId': 'line-user',
                    },
                    'message': {'type': 'text', 'text': '查詢公司資料'},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.new(b'line-secret', body, hashlib.sha256).digest())
    replies = []
    claims = []

    async def get_channel(*args):
        return channel

    async def claim_event(*args, **kwargs):
        claims.append((args, kwargs))
        return InteractEventClaim(allowed=True, duplicate=False, event_id='event-id')

    async def send_reply(channel, reply_token, text):
        replies.append(text)

    async def no_op(*args, **kwargs):
        return None

    async def unexpected(*args, **kwargs):
        raise AssertionError('group chat must not resolve identity or enqueue AI work')

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels._mark_delivery', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', unexpected)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', unexpected)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload['results'][0]['directChatRequired'] is True
    assert '一對一聊天室' in replies[0]
    assert claims[0][1]['quota_exempt'] is True


@pytest.mark.asyncio
async def test_line_workflow_menu_uses_carousel_without_quick_replies(monkeypatch):
    options = [
        {
            'id': f'workflow-{index}',
            'versionId': f'version-{index}',
            'name': f'工作流 {index}',
            'description': '立即執行',
            'buttonLabel': f'工作流 {index}',
        }
        for index in range(8)
    ]
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
    )

    async def load_options(channel):
        return options

    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', load_options)

    messages = await _line_delivery_messages(channel, {'lineWorkflowMenu': True})

    assert messages == [
        {
            'type': 'text',
            'text': '工作流選單已更新，請重新點選 LINE 選單中的工作流分類。',
        }
    ]


@pytest.mark.asyncio
async def test_line_menu_command_replies_with_authorized_panel_without_queue(monkeypatch):
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
                    'webhookEventId': 'menu-event',
                    'source': {'type': 'user', 'userId': 'line-user'},
                    'message': {'type': 'text', 'text': '選單'},
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
        return InteractEventClaim(allowed=True, duplicate=False, event_id='claimed-menu')

    async def enqueue_job(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id='menu-job')

    async def send_reply(*args):
        replies.append(args)

    async def send_messages(channel, reply_token, messages):
        replies.extend(messages)

    async def refresh_identity(*args, **kwargs):
        return _active_line_identity()

    async def workflow_options(*args, **kwargs):
        return [
            {
                'id': 'workflow-id',
                'versionId': 'version-id',
                'name': '立即查詢',
                'launchMode': 'instant',
            }
        ]

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr('open_webui.routers.interact_channels._platform_channel', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.claim_event', claim_event)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.enqueue_job', enqueue_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_reply', send_reply)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_messages', send_messages)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_refresh_identity', refresh_identity)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', workflow_options)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels._mark_delivery', no_op)
    monkeypatch.setattr('open_webui.routers.interact_channels.wake_channel_job_workers', lambda: None)

    response = await platform_webhook(
        _request(body, [(b'x-line-signature', signature)]),
        'line',
        'line-channel',
    )

    assert response.status_code == 200
    assert queued == []
    assert replies[0]['type'] == 'flex'


@pytest.mark.asyncio
async def test_channel_worker_forwards_quick_action_without_intent_selection(monkeypatch):
    captured = {}
    channel = SimpleNamespace(
        id='channel-id',
        company_email='company@example.com',
        channel_type='line',
        channel_identifier='line-channel',
        name='LINE bot',
        reply_mode='ai',
        model_id='model-id',
        fallback_model_id=None,
        daily_bot_token_limit=10000,
    )

    async def fake_channel_chat(request, payload, **kwargs):
        captured['payload'] = payload
        return {'ok': True, 'content': 'done', 'outputs': []}

    monkeypatch.setattr('open_webui.routers.interact_channels.channel_chat', fake_channel_chat)

    result = await _run_channel_message(
        SimpleNamespace(),
        channel,
        'channel-event',
        'line-user',
        'platform-event',
        '執行 LINE 快速工作流',
        conversation_id='line-user',
        workflow_id='workflow-id',
        workflow_version_id='version-id',
        workflow_trigger='line.quick_action',
        workflow_data={'range': 'today'},
    )

    assert result['content'] == 'done'
    assert captured['payload'].workflowId == 'workflow-id'
    assert captured['payload'].workflowVersionId == 'version-id'
    assert captured['payload'].workflowTrigger == 'line.quick_action'
    assert captured['payload'].workflowData == {'range': 'today'}


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

    async def complete_job(job_id, worker_id):
        completed.append(job_id)
        return True

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
async def test_menu_job_builds_panel_result_without_calling_model(monkeypatch):
    channel = SimpleNamespace(id='channel-id', enabled=True)
    job = SimpleNamespace(
        id='menu-job',
        event_id='menu-event',
        channel_id='channel-id',
        external_user_id='line-user',
        platform='line',
        payload={
            'recipientId': 'line-user',
            'platformEventId': 'platform-menu-event',
            'message': '選單',
            'workflowMenu': True,
        },
        result=None,
        attempts=1,
    )
    saved = []
    responses = []
    deliveries = []
    completed = []

    async def get_channel(channel_id):
        return channel

    async def set_response(*args):
        responses.append(args)

    async def save_result(*args):
        saved.append(args)
        return True

    async def send_result(channel, recipient, result, retry_key=None):
        deliveries.append((recipient, result, retry_key))

    async def complete_job(job_id, worker_id):
        completed.append(job_id)
        return True

    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError('workflow menu must not invoke the model')

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.set_response', set_response)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.save_job_result', save_result)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.complete_job', complete_job)
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_push_result', send_result)
    monkeypatch.setattr('open_webui.routers.interact_channels._complete_claimed_result', unexpected_model_call)

    await _process_channel_job(SimpleNamespace(state=SimpleNamespace()), 'worker', job, 300, 5)

    assert responses == [('menu-event', '快速工作流選單', 0, None)]
    assert saved[0][0] == 'menu-job'
    assert saved[0][1] == 'worker'
    assert saved[0][2]['lineWorkflowMenu'] is True
    assert deliveries[0][1]['lineWorkflowMenu'] is True
    assert completed == ['menu-job']


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
    signature = hashlib.sha1(''.join(sorted(['wechat-verification-token', timestamp, nonce])).encode()).hexdigest()
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

    async def ensure_chat(*args, **kwargs):
        return chat

    async def prepare_context(*args):
        return [{'role': 'user', 'content': 'hello'}], 0

    async def resolve_features(*args):
        return {'image_generation': True}

    def resolve_tools(*args):
        return ['private-company-tool']

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
    monkeypatch.setattr('open_webui.routers.interact_channels._resolve_model_tool_ids', resolve_tools)
    monkeypatch.setattr('open_webui.routers.interact_channels.Chats.get_message_by_id_and_message_id', get_message)
    monkeypatch.setattr('open_webui.routers.workflows.select_workflow_for_user_context', select_workflow)

    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'model': {}},
                CHAT_COMPLETION_HANDLER=handler,
            )
        ),
    )
    response = await channel_chat(request, _payload(), None, None)

    assert captured['features'] == {'image_generation': True}
    assert 'tool_ids' not in captured
    assert captured_runtime['trusted'] is True
    assert response['content'] == 'done'


@pytest.mark.asyncio
async def test_channel_chat_exposes_stored_billing_error_instead_of_fallback(monkeypatch):
    user = SimpleNamespace(id='shared-user', role='user')
    chat = SimpleNamespace(chat={'history': {'currentId': None}})

    monkeypatch.setattr('open_webui.routers.interact_channels._require_service_token', lambda *args: None)

    async def get_user(email):
        return user

    async def ensure_chat(*args, **kwargs):
        return chat

    async def prepare_context(*args):
        return [{'role': 'user', 'content': 'hello'}], 0

    async def resolve_features(*args):
        return {}

    def resolve_tools(*args):
        return []

    async def select_workflow(*args, **kwargs):
        return SimpleNamespace(decision='none', selected_workflow_id=None, selected_version_id=None)

    async def get_message(*args):
        return {
            'content': '',
            'done': False,
            'error': {'content': 'Billing authorization failed: MEMBER_NOT_FOUND'},
        }

    async def handler(request, form_data, user):
        return {}

    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', get_user)
    monkeypatch.setattr('open_webui.routers.interact_channels._ensure_channel_chat', ensure_chat)
    monkeypatch.setattr('open_webui.routers.interact_channels._prepare_channel_context', prepare_context)
    monkeypatch.setattr('open_webui.routers.interact_channels._resolve_model_features', resolve_features)
    monkeypatch.setattr('open_webui.routers.interact_channels._resolve_model_tool_ids', resolve_tools)
    monkeypatch.setattr('open_webui.routers.interact_channels.Chats.get_message_by_id_and_message_id', get_message)
    monkeypatch.setattr('open_webui.routers.workflows.select_workflow_for_user_context', select_workflow)

    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'model': {}},
                CHAT_COMPLETION_HANDLER=handler,
            )
        ),
    )
    response = await channel_chat(request, _payload(), None, None)

    assert response['ok'] is False
    assert response['reason'] == 'COMPANY-IDENTITY-NOT-FOUND'
    assert 'COMPANY-IDENTITY-NOT-FOUND' in response['content']
    assert '暫停服務' not in response['content']


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
