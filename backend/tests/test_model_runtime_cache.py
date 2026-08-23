from types import SimpleNamespace

import pytest

from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups
from open_webui.models.models import Models
from open_webui.utils.models import (
    get_filtered_models,
    refresh_runtime_model_cache_entry,
    serialize_external_api_model,
)


class PersistedModel(SimpleNamespace):
    def model_dump(self):
        return {
            'id': self.id,
            'name': self.name,
            'is_active': self.is_active,
            'updated_at': self.updated_at,
            'meta': self.meta,
            'params': {'temperature': 0.3},
        }


def _request(models):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))


def test_runtime_model_cache_receives_latest_knowledge_immediately():
    models = {
        'model-1': {
            'id': 'model-1',
            'name': 'old',
            # DB timestamps use seconds, so two edits can share a timestamp.
            'info': {'updated_at': 2, 'meta': {'knowledge': []}},
            'action_ids': [],
            'filter_ids': [],
        }
    }
    persisted = PersistedModel(
        id='model-1',
        name='new',
        is_active=True,
        updated_at=2,
        meta={
            'knowledge': [{'type': 'collection', 'id': 'kb-1'}],
            'actionIds': ['action-1'],
            'filterIds': ['filter-1'],
        },
    )

    refreshed = refresh_runtime_model_cache_entry(_request(models), persisted)

    assert refreshed['name'] == 'new'
    assert refreshed['info']['meta']['knowledge'] == [{'type': 'collection', 'id': 'kb-1'}]
    assert 'params' not in refreshed['info']
    assert models['model-1']['action_ids'] == ['action-1']
    assert models['model-1']['filter_ids'] == ['filter-1']


def test_runtime_model_cache_removes_disabled_model():
    models = {'model-1': {'id': 'model-1', 'info': {'updated_at': 1}}}
    persisted = PersistedModel(
        id='model-1',
        name='disabled',
        is_active=False,
        updated_at=2,
        meta={},
    )

    assert refresh_runtime_model_cache_entry(_request(models), persisted) is None
    assert 'model-1' not in models


def test_external_api_model_serializer_omits_internal_acl_and_owner_data():
    model = {
        'id': 'agent-1',
        'name': 'Sales agent',
        'object': 'model',
        'created': 123,
        'owned_by': 'openai',
        'tags': [{'name': 'sales'}],
        'info': {
            'user_id': 'owner-1',
            'access_grants': [{'principal_id': 'user-2'}],
            'meta': {
                'description': 'Answers from approved knowledge.',
                'knowledge': [
                    {
                        'id': 'kb-1',
                        'user': {'email': 'owner@example.com'},
                        'access_grants': [{'principal_id': '*'}],
                    }
                ],
            },
        },
        'interact': {
            'source': 'workspace',
            'owned_by_current_user': False,
            'capabilities': {'knowledge': True},
        },
    }

    serialized = serialize_external_api_model(model)

    assert serialized == {
        'id': 'agent-1',
        'object': 'model',
        'created': 123,
        'owned_by': 'openai',
        'name': 'Sales agent',
        'description': 'Answers from approved knowledge.',
        'tags': [{'name': 'sales'}],
        'interact': {
            'source': 'workspace',
            'owned_by_current_user': False,
            'capabilities': {'knowledge': True},
        },
    }
    assert 'info' not in serialized


@pytest.mark.asyncio
async def test_filtered_models_hide_preset_when_base_model_access_is_missing(monkeypatch):
    user = SimpleNamespace(id='user-1', role='user')
    runtime_model = {'id': 'preset-1', 'info': {'user_id': 'owner-1'}}
    persisted_model = SimpleNamespace(id='preset-1', user_id='owner-1', base_model_id='base-1')

    async def groups_for_user(user_id, db=None):
        return []

    async def models_by_ids(model_ids, db=None):
        return [persisted_model]

    async def accessible_ids(**kwargs):
        return {'preset-1'}

    async def base_access(*args, **kwargs):
        return False

    monkeypatch.setattr(Groups, 'get_groups_by_member_id', groups_for_user)
    monkeypatch.setattr(Models, 'get_models_by_ids', models_by_ids)
    monkeypatch.setattr(AccessGrants, 'get_accessible_resource_ids', accessible_ids)
    monkeypatch.setattr('open_webui.utils.models.has_base_model_access', base_access)

    assert await get_filtered_models([runtime_model], user) == []


@pytest.mark.asyncio
async def test_filtered_models_include_preset_when_full_model_chain_is_authorized(monkeypatch):
    user = SimpleNamespace(id='user-1', role='user')
    runtime_model = {'id': 'preset-1', 'info': {'user_id': 'owner-1'}}
    persisted_model = SimpleNamespace(id='preset-1', user_id='owner-1', base_model_id='base-1')

    async def groups_for_user(user_id, db=None):
        return []

    async def models_by_ids(model_ids, db=None):
        return [persisted_model]

    async def accessible_ids(**kwargs):
        return {'preset-1'}

    async def base_access(*args, **kwargs):
        return True

    monkeypatch.setattr(Groups, 'get_groups_by_member_id', groups_for_user)
    monkeypatch.setattr(Models, 'get_models_by_ids', models_by_ids)
    monkeypatch.setattr(AccessGrants, 'get_accessible_resource_ids', accessible_ids)
    monkeypatch.setattr('open_webui.utils.models.has_base_model_access', base_access)

    assert await get_filtered_models([runtime_model], user) == [runtime_model]
