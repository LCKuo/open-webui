from types import SimpleNamespace

import pytest
from open_webui.utils.knowledge_scope import KnowledgeScopeViolation
from open_webui.utils.tools import get_builtin_tools


def _knowledge_only_model(knowledge):
    return {
        'info': {
            'meta': {
                'knowledge': knowledge,
                'builtinTools': {
                    'time': False,
                    'knowledge': True,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'interact_database': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                },
            }
        }
    }


async def _empty_config(*_args, **_kwargs):
    return {}


@pytest.mark.asyncio
async def test_model_without_attached_knowledge_gets_no_knowledge_tools(monkeypatch):
    monkeypatch.setattr('open_webui.utils.tools.Config.get_many', _empty_config)
    request = SimpleNamespace(state=SimpleNamespace())

    tools = await get_builtin_tools(
        request,
        {'__user__': {'id': 'user', 'role': 'admin'}, '__metadata__': {}},
        model=_knowledge_only_model([]),
    )

    assert tools == {}


@pytest.mark.asyncio
async def test_trusted_channel_tool_injection_rechecks_company_scope(monkeypatch):
    monkeypatch.setattr('open_webui.utils.tools.Config.get_many', _empty_config)

    async def reject_scope(*_args, **_kwargs):
        raise KnowledgeScopeViolation('cross-company')

    monkeypatch.setattr('open_webui.utils.tools.validate_company_knowledge_scope', reject_scope)
    request = SimpleNamespace(
        state=SimpleNamespace(
            interact_channel_runtime=True,
            interact_knowledge_scope_owner_user_id='company-user',
        )
    )

    with pytest.raises(KnowledgeScopeViolation, match='cross-company'):
        await get_builtin_tools(
            request,
            {'__user__': {'id': 'company-user', 'role': 'admin'}, '__metadata__': {}},
            model=_knowledge_only_model([{'type': 'collection', 'id': 'foreign-kb'}]),
        )
