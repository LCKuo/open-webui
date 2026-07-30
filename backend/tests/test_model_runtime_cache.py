from types import SimpleNamespace

from open_webui.utils.models import refresh_runtime_model_cache_entry


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
