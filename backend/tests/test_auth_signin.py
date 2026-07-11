from types import SimpleNamespace

import bcrypt
import pytest
from starlette.responses import Response

import open_webui.routers.auths as auth_router
from open_webui.models.auths import SigninForm
from open_webui.utils.auth import verify_password


@pytest.mark.asyncio
async def test_verify_password_supports_existing_bcrypt_hash():
    password = 'legacy-password'
    legacy_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    assert await verify_password(password, legacy_hash) is True
    assert await verify_password('wrong-password', legacy_hash) is False


@pytest.mark.asyncio
async def test_signin_normalizes_email_without_changing_password(monkeypatch):
    captured = {}
    user = SimpleNamespace(id='user-1')

    monkeypatch.setattr(auth_router, 'ENABLE_PASSWORD_AUTH', True)
    monkeypatch.setattr(auth_router, 'WEBUI_AUTH_TRUSTED_EMAIL_HEADER', '')
    monkeypatch.setattr(auth_router, 'WEBUI_AUTH', True)
    monkeypatch.setattr(auth_router.signin_rate_limiter, 'is_limited', lambda email: False)

    async def authenticate(email, password_verifier, db=None):
        captured['email'] = email
        captured['passwordMatches'] = await password_verifier(
            bcrypt.hashpw(b' password-with-spaces ', bcrypt.gensalt()).decode()
        )
        return user

    async def session_response(*args, **kwargs):
        return {'ok': True}

    monkeypatch.setattr(auth_router.Auths, 'authenticate_user', authenticate)
    monkeypatch.setattr(auth_router, 'create_session_response', session_response)

    result = await auth_router.signin(
        SimpleNamespace(headers={}),
        Response(),
        SigninForm(email=' User@Example.COM ', password=' password-with-spaces '),
        db=SimpleNamespace(),
    )

    assert result == {'ok': True}
    assert captured == {
        'email': 'user@example.com',
        'passwordMatches': True,
    }
