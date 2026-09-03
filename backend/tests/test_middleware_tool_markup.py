from types import SimpleNamespace

import pytest

from open_webui.utils.middleware import (
    _extract_legacy_markup_tool_calls,
    _is_builtin_tool_runtime,
    add_file_context,
    resolve_function_calling_mode,
)


def test_minimax_tool_call_markup_is_converted_to_a_real_call():
    text = '''好的，讓我查詢：
<minimax:tool_call>
<invoke name="interact_database_query">
<parameter name="columns">["id", "name", "email"]</parameter>
<parameter name="limit">5</parameter>
<parameter name="operation">select</parameter>
<parameter name="order_by">[{"column":"id","direction":"asc"}]</parameter>
<parameter name="table">crm.users</parameter>
</invoke>
</minimax:tool_call>'''

    calls, cleaned = _extract_legacy_markup_tool_calls(text, {'interact_database_query'})

    assert cleaned == '好的，讓我查詢：'
    assert len(calls) == 1
    assert calls[0]['function']['name'] == 'interact_database_query'
    assert calls[0]['function']['arguments'] == (
        '{"columns": ["id", "name", "email"], "limit": 5, "operation": "select", '
        '"order_by": [{"column": "id", "direction": "asc"}], "table": "crm.users"}'
    )


def test_unknown_markup_tool_cannot_bypass_the_tool_allowlist():
    text = '<tool_call><invoke name="dangerous_tool"></invoke></tool_call>'

    calls, cleaned = _extract_legacy_markup_tool_calls(text, {'interact_database_query'})

    assert calls == []
    assert cleaned == text


def test_standard_function_calls_markup_remains_supported():
    text = '''<function_calls>
<invoke name="interact_database_query">
<parameter name="table">crm.users</parameter>
</invoke>
</function_calls>'''

    calls, cleaned = _extract_legacy_markup_tool_calls(text, {'interact_database_query'})

    assert len(calls) == 1
    assert cleaned == ''


def test_browser_session_can_use_builtin_tools():
    request = SimpleNamespace(state=SimpleNamespace())

    assert _is_builtin_tool_runtime(request, {'session_id': 'browser-session'}) is True


@pytest.mark.parametrize('source', ['channel', 'crm_embedded'])
def test_verified_channel_runtime_can_use_builtin_tools_without_browser_session(source):
    request = SimpleNamespace(state=SimpleNamespace(interact_channel_runtime=True))
    metadata = {'interact_channel': {'source': source}}

    assert _is_builtin_tool_runtime(request, metadata) is True


def test_api_key_runtime_can_use_acl_checked_model_tools():
    request = SimpleNamespace(state=SimpleNamespace(auth_type='api_key'))

    assert _is_builtin_tool_runtime(request, {}) is True


@pytest.mark.parametrize(
    ('requested', 'configured', 'expected'),
    [
        ('legacy', 'native', 'legacy'),
        (None, 'legacy', 'legacy'),
        ('native', 'legacy', 'native'),
        (None, None, 'default'),
    ],
)
def test_function_calling_mode_preserves_explicit_model_behavior(requested, configured, expected):
    assert resolve_function_calling_mode(requested, configured) == expected


@pytest.mark.parametrize('source', ['channel', 'crm_embedded'])
def test_channel_metadata_cannot_enable_builtin_tools_without_trusted_runtime(source):
    request = SimpleNamespace(state=SimpleNamespace())
    metadata = {'interact_channel': {'source': source}}

    assert _is_builtin_tool_runtime(request, metadata) is False


@pytest.mark.asyncio
async def test_file_context_accepts_stored_messages_with_null_files(monkeypatch):
    chat = SimpleNamespace(
        chat={
            'history': {
                'currentId': 'message-1',
                'messages': {
                    'message-1': {
                        'id': 'message-1',
                        'role': 'user',
                        'content': 'hello',
                        'files': None,
                    }
                },
            }
        }
    )

    async def get_chat(*args, **kwargs):
        return chat

    monkeypatch.setattr('open_webui.utils.middleware.Chats.get_chat_by_id_and_user_id', get_chat)
    messages = [{'role': 'user', 'content': 'hello'}]

    assert await add_file_context(messages, 'interact-channel-test', SimpleNamespace(id='user')) == messages
