from types import SimpleNamespace

import pytest
from open_webui.tools.builtin import _has_read_access_to_file
from open_webui.tools.knowledge_fs import _get_accessible_kb_ids
from open_webui.utils.knowledge_scope import (
    KnowledgeScopeViolation,
    channel_knowledge_scope_fingerprint,
    normalized_knowledge_refs,
    validate_company_knowledge_scope,
)


def test_knowledge_scope_fingerprint_is_order_independent_and_scope_sensitive():
    first = [
        {'type': 'file', 'id': 'file-1'},
        {'type': 'collection', 'id': 'kb-1'},
    ]
    reordered = list(reversed(first))

    assert normalized_knowledge_refs(first) == normalized_knowledge_refs(reordered)
    assert channel_knowledge_scope_fingerprint('model', first) == channel_knowledge_scope_fingerprint(
        'model', reordered
    )
    assert channel_knowledge_scope_fingerprint('model', first) != channel_knowledge_scope_fingerprint(
        'model', [{'type': 'collection', 'id': 'kb-2'}]
    )


@pytest.mark.asyncio
async def test_company_scope_rejects_knowledge_owned_by_another_webui_account(monkeypatch):
    async def get_knowledge(_knowledge_id):
        return SimpleNamespace(user_id='other-company')

    monkeypatch.setattr(
        'open_webui.utils.knowledge_scope.Knowledges.get_knowledge_by_id',
        get_knowledge,
    )

    with pytest.raises(KnowledgeScopeViolation, match='outside the company scope'):
        await validate_company_knowledge_scope(
            [{'type': 'collection', 'id': 'kb-1'}],
            owner_user_id='current-company',
        )


@pytest.mark.asyncio
async def test_empty_model_knowledge_cannot_fall_back_to_all_knowledge():
    assert await _get_accessible_kb_ids({'id': 'user', 'role': 'admin'}, None) == []
    assert await _get_accessible_kb_ids({'id': 'user', 'role': 'admin'}, []) == []


@pytest.mark.asyncio
async def test_file_access_requires_attachment_even_for_owner():
    file = SimpleNamespace(id='file-1', user_id='company-user')

    assert await _has_read_access_to_file(file, 'company-user', 'admin', []) is False
    assert (
        await _has_read_access_to_file(
            file,
            'company-user',
            'user',
            [{'type': 'file', 'id': 'file-1'}],
        )
        is True
    )
