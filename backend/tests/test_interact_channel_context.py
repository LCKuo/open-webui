from types import SimpleNamespace

import pytest
from open_webui.routers.interact_channels import (
    ChannelChatRequest,
    _channel_request_messages,
    _estimated_reservation_tokens,
    _generate_context_summary,
    _prepare_channel_context,
    _response_content,
    _response_event_content,
    _should_summarize_context,
    _summary_history_state,
    _usage_tokens,
)
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
