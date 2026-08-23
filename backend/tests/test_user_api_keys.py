from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.users import Users, hash_api_key
from open_webui.routers.auths import (
    ApiKeyCreateForm,
    _check_api_key_permission,
    _api_key_summary,
    _create_user_api_key,
    build_api_tour,
)
from open_webui.utils.auth import is_api_key_scope_allowed
from pydantic import ValidationError


def test_api_key_hash_never_contains_secret():
    secret = 'sk-test-secret-value'
    stored = hash_api_key(secret)

    assert stored.startswith('sha256:')
    assert secret not in stored
    assert stored == hash_api_key(secret)


def test_api_key_summary_only_returns_masked_metadata():
    record = SimpleNamespace(
        id='key-1',
        key=hash_api_key('sk-test-secret-value'),
        data={
            'name': 'CRM production',
            'prefix': 'sk-test-se',
            'last_four': 'alue',
            'scopes': ['models:read', 'chat:write'],
        },
        expires_at=None,
        last_used_at=None,
        created_at=100,
    )

    summary = _api_key_summary(record)
    assert summary['prefix'] == 'sk-test-se'
    assert summary['last_four'] == 'alue'
    assert 'key' not in summary
    assert 'api_key' not in summary


def test_api_key_expiry_accepts_only_supported_choices():
    assert ApiKeyCreateForm(name='CRM', expires_in_days=90).expires_in_days == 90
    assert ApiKeyCreateForm(name='CRM', expires_in_days=None).expires_in_days is None

    with pytest.raises(ValidationError):
        ApiKeyCreateForm(name='CRM', expires_in_days=7)


def test_api_tour_documents_model_discovery_and_billing():
    guide = build_api_tour('https://ai.example.com/')

    assert 'https://ai.example.com/api/v1/models' in guide
    assert 'https://ai.example.com/api/v1/chat/completions' in guide
    assert '工作區模型' in guide
    assert '依企業方案與實際使用量' in guide
    assert '2 倍' not in guide


def test_api_key_scopes_fail_closed_for_internal_endpoints():
    scopes = {'models:read', 'chat:write'}

    assert is_api_key_scope_allowed(scopes, 'GET', '/api/v1/models')
    assert is_api_key_scope_allowed(scopes, 'POST', '/api/v1/chat/completions')
    assert not is_api_key_scope_allowed(scopes, 'GET', '/api/v1/users')
    assert not is_api_key_scope_allowed(scopes, 'POST', '/api/v1/models')
    assert not is_api_key_scope_allowed(scopes, 'POST', '/api/v1/workflows')


@pytest.mark.asyncio
async def test_expired_keys_do_not_block_new_key_creation(monkeypatch):
    expired = [SimpleNamespace(expires_at=1) for _ in range(10)]

    async def list_keys(user_id, db=None):
        return expired

    async def create_key(user_id, api_key, data, expires_at=None, db=None):
        return SimpleNamespace(
            id='key-new',
            key=hash_api_key(api_key),
            data=data,
            expires_at=expires_at,
            last_used_at=None,
            created_at=100,
        )

    monkeypatch.setattr(Users, 'get_user_api_keys_by_id', list_keys)
    monkeypatch.setattr(Users, 'create_user_api_key', create_key)

    result = await _create_user_api_key(
        SimpleNamespace(id='user-1'),
        ApiKeyCreateForm(name='CRM', expires_in_days=90),
        db=None,
    )

    assert result['id'] == 'key-new'
    assert result['name'] == 'CRM'
    assert result['api_key'].startswith('sk-')


@pytest.mark.asyncio
async def test_ten_active_keys_require_revoking_one(monkeypatch):
    active = [SimpleNamespace(expires_at=None) for _ in range(10)]

    async def list_keys(user_id, db=None):
        return active

    monkeypatch.setattr(Users, 'get_user_api_keys_by_id', list_keys)

    with pytest.raises(HTTPException) as exc_info:
        await _create_user_api_key(
            SimpleNamespace(id='user-1'),
            ApiKeyCreateForm(name='CRM', expires_in_days=90),
            db=None,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_api_key_management_requires_user_permission(monkeypatch):
    async def config_get(key):
        if key == 'auth.enable_api_keys':
            return True
        if key == 'user.permissions':
            return {'features': {'api_keys': False}}
        return None

    async def permission_denied(user_id, permission_key, default_permissions, db=None):
        assert user_id == 'user-1'
        assert permission_key == 'features.api_keys'
        return False

    monkeypatch.setattr('open_webui.routers.auths.Config.get', config_get)
    monkeypatch.setattr('open_webui.routers.auths.has_permission', permission_denied)

    with pytest.raises(HTTPException) as exc_info:
        await _check_api_key_permission(None, SimpleNamespace(id='user-1', role='user'), None)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_api_key_management_allows_admin_when_globally_enabled(monkeypatch):
    async def config_get(key):
        return key == 'auth.enable_api_keys'

    monkeypatch.setattr('open_webui.routers.auths.Config.get', config_get)

    await _check_api_key_permission(None, SimpleNamespace(id='admin-1', role='admin'), None)
