from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from urllib.parse import quote, urlparse

import aiofiles
import aiohttp
from aiocache import cached
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from open_webui.config import (
    CACHE_DIR,
)
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event, publish_model_provider_request_failed
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    BYPASS_MODEL_ACCESS_CONTROL,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    ENABLE_OPENAI_API_PASSTHROUGH,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    MODELS_CACHE_TTL,
)
from open_webui.models.access_grants import AccessGrants
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.models import Models
from open_webui.models.provider_credentials import UserProviderCredentials
from open_webui.models.users import UserModel
from open_webui.utils.access_control import check_model_access, has_permission
from open_webui.utils.anthropic import get_anthropic_models, is_anthropic_url
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.headers import get_custom_headers, include_user_info_headers
from open_webui.utils.openai_tool_compat import apply_chat_completion_tool_compat
from open_webui.utils.interact_billing import require_metered_inference_entrypoint
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.model_ids import strip_provider_model_prefix
from open_webui.utils.misc import (
    convert_logit_bias_input_to_json,
    stream_chunks_handler,
)
from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_system_prompt_to_body,
)
from open_webui.utils.session_pool import (
    cleanup_response,
    get_client_timeout,
    get_session,
    stream_wrapper,
)
from open_webui.retrieval.web.utils import get_ssrf_safe_session, validate_url
from open_webui.utils.upstream_retry import request_with_rate_limit_retry
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)


##########################################
#
# Utility functions
# Let the responses returned through this gate be worth
# the question that summoned them.
#
##########################################

# Headers that become stale after aiohttp auto-decompresses the upstream
# response body.  Forwarding them verbatim causes desktop / programmatic
# clients to attempt decompression of an already-decoded payload, resulting
# in ZlibError.  See https://github.com/aio-libs/aiohttp/issues/4462.
_STRIP_PROXY_HEADERS = frozenset({'Content-Encoding', 'Content-Length', 'Transfer-Encoding'})
_MODEL_LIST_TIMEOUT = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
_UNSUPPORTED_OPENAI_MODEL_KEYWORDS = ('babbage', 'dall-e', 'davinci', 'embedding', 'tts', 'whisper')


def _clean_proxy_headers(raw_headers) -> dict:
    """Return a copy of *raw_headers* with stale encoding headers removed."""
    return {k: v for k, v in raw_headers.items() if k not in _STRIP_PROXY_HEADERS}


async def send_get_request(
    request: Request = None,
    url=None,
    key=None,
    user: UserModel = None,
    config=None,
):
    session = None
    try:
        session, owns_session = await get_upstream_session(config)
        if request and config:
            headers, cookies = await get_headers_and_cookies(request, url, key, config, user=user)
        else:
            headers = {
                **({'Authorization': f'Bearer {key}'} if key else {}),
            }
            cookies = None

            if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                headers = include_user_info_headers(headers, user)

        async with session.get(
            url,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=_MODEL_LIST_TIMEOUT,
        ) as response:
            return await response.json(loads=JSONCodec.loads)
    except Exception as e:
        # Handle connection error here
        log.error(f'Connection error: {e}')
        return None
    finally:
        if session is not None and locals().get('owns_session'):
            await session.close()


async def get_models_request(
    request: Request = None,
    url=None,
    key=None,
    user: UserModel = None,
    config=None,
):
    if is_anthropic_url(url):
        return await get_anthropic_models(url, key, user=user)
    return await send_get_request(request, f'{url}/models', key, user=user, config=config)


def openai_reasoning_model_handler(payload):
    """
    Handle reasoning model specific parameters
    """
    if 'max_tokens' in payload:
        # Convert "max_tokens" to "max_completion_tokens" for all reasoning models
        payload['max_completion_tokens'] = payload['max_tokens']
        del payload['max_tokens']

    # Handle system role conversion based on model type
    if payload['messages'][0]['role'] == 'system':
        model_lower = payload['model'].lower()
        # Legacy models use "user" role instead of "system"
        if model_lower.startswith('o1-mini') or model_lower.startswith('o1-preview'):
            payload['messages'][0]['role'] = 'user'
        else:
            payload['messages'][0]['role'] = 'developer'

    return payload


async def get_headers_and_cookies(
    request: Request,
    url,
    key=None,
    config=None,
    metadata: dict | None = None,
    user: UserModel = None,
):
    cookies = {}
    headers = {
        'Content-Type': 'application/json',
        **(
            {
                'HTTP-Referer': 'https://openwebui.com/',
                'X-Title': 'Interact Ai',
            }
            if 'openrouter.ai' in url
            else {}
        ),
    }

    if ENABLE_FORWARD_USER_INFO_HEADERS and user:
        headers = include_user_info_headers(headers, user)
        if metadata and metadata.get('chat_id'):
            headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = metadata.get('chat_id')

    token = None
    auth_type = config.get('auth_type')

    if auth_type == 'bearer' or auth_type is None:
        # Default to bearer if not specified
        token = f'{key}'
    elif auth_type == 'none':
        token = None
    elif auth_type == 'session':
        cookies = request.cookies
        token = request.state.token.credentials
    elif auth_type == 'system_oauth':
        cookies = request.cookies

        oauth_token = None
        try:
            if request.cookies.get('oauth_session_id', None):
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    request.cookies.get('oauth_session_id', None),
                )
        except Exception as e:
            log.error(f'Error getting OAuth token: {e}')

        if oauth_token:
            token = f'{oauth_token.get("access_token", "")}'

    elif auth_type in ('azure_ad', 'microsoft_entra_id'):
        token = get_microsoft_entra_id_access_token()

    if token:
        headers['Authorization'] = f'Bearer {token}'

    if config.get('headers') and isinstance(config.get('headers'), dict):
        custom_headers = await get_custom_headers(config.get('headers'), user, metadata, request=request)
        headers.update(custom_headers)

    return headers, cookies


def get_microsoft_entra_id_access_token():
    """
    Get Microsoft Entra ID access token using DefaultAzureCredential for Azure OpenAI.
    Returns the token string or None if authentication fails.
    """
    try:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), 'https://cognitiveservices.azure.com/.default'
        )
        return token_provider()
    except Exception as e:
        log.error(f'Error getting Microsoft Entra ID access token: {e}')
        return None


##########################################
#
# API routes
#
##########################################

router = APIRouter()

LLAMACPP_LOADED_STATES = {'loaded', 'sleeping'}
LLAMACPP_UNLOADED_STATES = {'loading', 'unloaded'}


def get_llamacpp_model_loaded_state(model: dict, provider: str, manual_model_ids: bool = False) -> bool | None:
    if provider != 'llama.cpp':
        return None

    status = model.get('status')
    if isinstance(status, dict):
        value = status.get('value')
        if value in LLAMACPP_LOADED_STATES:
            return True
        if value in LLAMACPP_UNLOADED_STATES:
            return False

    if not manual_model_ids and 'status' not in model:
        return True

    return None


OPENAI_CONFIG_KEYS = {
    'ENABLE_OPENAI_API': 'openai.enable',
    'OPENAI_API_BASE_URLS': 'openai.api_base_urls',
    'OPENAI_API_KEYS': 'openai.api_keys',
    'OPENAI_API_CONFIGS': 'openai.api_configs',
}


async def get_openai_config() -> dict:
    values = await Config.get_many(*OPENAI_CONFIG_KEYS.values())
    return {field: values[storage_key] for field, storage_key in OPENAI_CONFIG_KEYS.items() if storage_key in values}


async def get_openai_runtime_config() -> tuple[bool, list[str], list[str], dict]:
    values = await Config.get_many('openai.enable', 'openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    return (
        values.get('openai.enable'),
        values.get('openai.api_base_urls') or [],
        values.get('openai.api_keys') or [],
        values.get('openai.api_configs') or {},
    )


async def normalize_openai_api_keys(api_base_urls: list[str], api_keys: list[str]) -> list[str]:
    if len(api_keys) > len(api_base_urls):
        api_keys = api_keys[: len(api_base_urls)]
    elif len(api_keys) < len(api_base_urls):
        api_keys = [*api_keys, *([''] * (len(api_base_urls) - len(api_keys)))]

    await Config.upsert({'openai.api_keys': api_keys})
    return api_keys


def get_openai_api_config(api_configs: dict, idx: int, url: str) -> dict:
    return api_configs.get(str(idx), api_configs.get(url, {}))


async def get_openai_runtime_connections(
    user: UserModel | None = None,
    *,
    use_personal: bool = True,
) -> tuple[bool, list[dict]]:
    """Resolve the exclusive per-user provider set or the shared platform set."""
    if user and use_personal:
        personal_connections = await UserProviderCredentials.get_runtime_connections(user.id)
        if personal_connections:
            return True, personal_connections

    enabled, api_base_urls, api_keys, api_configs = await get_openai_runtime_config()
    if len(api_keys) != len(api_base_urls):
        api_keys = await normalize_openai_api_keys(api_base_urls, api_keys)

    return False, [
        {
            'url': url,
            'key': api_keys[idx],
            'config': get_openai_api_config(api_configs, idx, url),
        }
        for idx, url in enumerate(api_base_urls)
        if enabled
    ]


async def get_openai_connection(
    idx: int,
    user: UserModel | None = None,
    *,
    use_personal: bool = True,
) -> tuple[str, str, dict]:
    _, connections = await get_openai_runtime_connections(user, use_personal=use_personal)
    try:
        connection = connections[idx]
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND()) from exc
    return connection['url'], connection['key'], connection['config']


async def get_upstream_session(api_config: dict | None) -> tuple[aiohttp.ClientSession, bool]:
    if isinstance(api_config, dict) and api_config.get('user_supplied'):
        return get_ssrf_safe_session(), True
    return await get_session(), False


PERSONAL_PROVIDER_URLS = {
    'openai': 'https://api.openai.com/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
}


class PersonalAPIKeyForm(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str
    base_url: str | None = Field(default=None, max_length=2048)
    auth_type: str = 'bearer'
    api_key: str | None = Field(default=None, max_length=4000)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()

    @field_validator('provider')
    @classmethod
    def clean_provider(cls, value: str) -> str:
        clean = value.strip().lower()
        if clean not in {'openai', 'gemini', 'custom'}:
            raise ValueError('Unsupported provider')
        return clean

    @field_validator('auth_type')
    @classmethod
    def clean_auth_type(cls, value: str) -> str:
        clean = value.strip().lower()
        if clean not in {'bearer', 'none'}:
            raise ValueError('Unsupported authentication type')
        return clean

    @field_validator('api_key')
    @classmethod
    def clean_api_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class PersonalAPIKeyConnection(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    host: str
    auth_type: str
    key_last4: str | None = None
    last_verified_at: int | None = None
    verification_status: str | None = None


def personal_connection_public(item) -> PersonalAPIKeyConnection:
    return PersonalAPIKeyConnection(
        id=item.connection_id,
        name=item.name,
        provider=item.provider,
        base_url=item.base_url,
        host=urlparse(item.base_url).hostname or item.base_url,
        auth_type=item.auth_type,
        key_last4=item.key_last4 or None,
        last_verified_at=item.last_verified_at,
        verification_status=item.verification_status,
    )


async def prepare_personal_connection(form_data: PersonalAPIKeyForm) -> tuple[str, str, str]:
    provider = form_data.provider
    if provider in PERSONAL_PROVIDER_URLS:
        base_url = PERSONAL_PROVIDER_URLS[provider]
        auth_type = 'bearer'
    else:
        base_url = (form_data.base_url or '').strip()
        auth_type = form_data.auth_type
        if not base_url:
            raise HTTPException(status_code=422, detail='自訂服務必須填寫 API 網址。')

    parsed = urlparse(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail='API 網址不可包含帳密、查詢參數或片段。')
    try:
        await run_in_threadpool(validate_url, base_url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail='API 網址無法連線，或不符合系統安全規則。') from exc

    if auth_type == 'bearer' and len(form_data.api_key or '') < 8:
        raise HTTPException(status_code=422, detail='API 金鑰至少需要 8 個字元。')
    return provider, base_url, auth_type


async def validate_personal_connection(
    request: Request,
    user: UserModel,
    *,
    provider: str,
    base_url: str,
    auth_type: str,
    api_key: str | None,
) -> None:
    config = {
        'enable': True,
        'auth_type': auth_type,
        'user_supplied': True,
        'personal_provider': provider,
    }
    result = await get_models_request(
        request,
        base_url,
        api_key or '',
        user=user,
        config=config,
    )
    models = result if isinstance(result, list) else (result or {}).get('data')
    if not isinstance(models, list) or not models:
        raise HTTPException(
            status_code=502,
            detail='無法讀取模型清單，未變更目前設定。請確認 API 網址、金鑰與服務狀態。',
        )


async def clear_personal_api_key_model_cache(request: Request) -> None:
    await get_all_models.cache.clear()


@router.get('/personal-api-keys')
async def list_personal_api_keys(user=Depends(get_verified_user)):
    items = await UserProviderCredentials.list_by_user_id(user.id)
    return {
        'mode': 'personal' if items else 'platform',
        'connections': [personal_connection_public(item) for item in items],
    }


@router.post('/personal-api-keys', response_model=PersonalAPIKeyConnection)
async def create_personal_api_key(
    request: Request,
    form_data: PersonalAPIKeyForm,
    user=Depends(get_verified_user),
):
    provider, base_url, auth_type = await prepare_personal_connection(form_data)
    await validate_personal_connection(
        request,
        user,
        provider=provider,
        base_url=base_url,
        auth_type=auth_type,
        api_key=form_data.api_key,
    )
    item = await UserProviderCredentials.upsert(
        user.id,
        name=form_data.name,
        provider=provider,
        base_url=base_url,
        auth_type=auth_type,
        api_key=form_data.api_key,
    )
    await UserProviderCredentials.set_verification_status(user.id, item.connection_id, 'ready')
    item = await UserProviderCredentials.get_by_connection_id(user.id, item.connection_id)
    await clear_personal_api_key_model_cache(request)
    return personal_connection_public(item)


@router.put('/personal-api-keys/{connection_id}', response_model=PersonalAPIKeyConnection)
async def update_personal_api_key(
    request: Request,
    connection_id: str,
    form_data: PersonalAPIKeyForm,
    user=Depends(get_verified_user),
):
    existing = await UserProviderCredentials.get_by_connection_id(user.id, connection_id)
    if not existing:
        raise HTTPException(status_code=404, detail='找不到這個自有 AI 連線。')
    provider, base_url, auth_type = await prepare_personal_connection(form_data)
    await validate_personal_connection(
        request,
        user,
        provider=provider,
        base_url=base_url,
        auth_type=auth_type,
        api_key=form_data.api_key,
    )
    item = await UserProviderCredentials.upsert(
        user.id,
        name=form_data.name,
        provider=provider,
        base_url=base_url,
        auth_type=auth_type,
        api_key=form_data.api_key,
    )
    if item.connection_id != connection_id:
        await UserProviderCredentials.delete(user.id, connection_id)
    await UserProviderCredentials.set_verification_status(user.id, item.connection_id, 'ready')
    item = await UserProviderCredentials.get_by_connection_id(user.id, item.connection_id)
    await clear_personal_api_key_model_cache(request)
    return personal_connection_public(item)


@router.delete('/personal-api-keys/{connection_id}')
async def delete_personal_api_key(
    request: Request,
    connection_id: str,
    user=Depends(get_verified_user),
):
    if not await UserProviderCredentials.delete(user.id, connection_id):
        raise HTTPException(status_code=404, detail='找不到這個自有 AI 連線。')
    await clear_personal_api_key_model_cache(request)
    remaining = await UserProviderCredentials.list_by_user_id(user.id)
    return {'ok': True, 'mode': 'personal' if remaining else 'platform'}


@router.post('/personal-api-keys/{connection_id}/verify')
async def verify_personal_api_key(
    request: Request,
    connection_id: str,
    user=Depends(get_verified_user),
):
    item = await UserProviderCredentials.get_by_connection_id(user.id, connection_id)
    if not item:
        raise HTTPException(status_code=404, detail='找不到這個自有 AI 連線。')
    runtime = await UserProviderCredentials.get_runtime_connections(user.id)
    connection = next(entry for entry in runtime if entry['config']['personal_connection_id'] == connection_id)

    verification_status = 'failed'
    try:
        result = await get_models_request(
            request,
            connection['url'],
            connection['key'],
            user=user,
            config=connection['config'],
        )
        models = result.get('data') if isinstance(result, dict) else result
        if not isinstance(models, list) or not models:
            raise HTTPException(status_code=502, detail='連線未通過測試，請確認網址、金鑰與服務狀態。')
        verification_status = 'ready'
        return {'ok': True, 'message': '自有 AI 連線可正常讀取模型。'}
    finally:
        await UserProviderCredentials.set_verification_status(user.id, connection_id, verification_status)
        await clear_personal_api_key_model_cache(request)


async def get_anthropic_token_count_target(request: Request, form_data: dict, user: UserModel):
    """Resolve the upstream LiteLLM connection for an Anthropic token-count request."""
    requested_model = form_data.get('model')
    if not requested_model:
        raise HTTPException(status_code=400, detail='model is required')

    payload = {**form_data}
    model_id = requested_model
    model_info = await Models.get_model_by_id(model_id)

    if model_info and model_info.base_model_id:
        model_id = model_info.base_model_id
        payload['model'] = model_id

    models = await get_user_openai_models(request, user)

    model = models.get(model_id)
    if not model or 'urlIdx' not in model:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND())
    if model_info:
        await check_model_access(user, model_info, BYPASS_MODEL_ACCESS_CONTROL)
    elif model.get('personal_owner_id') != user.id:
        await check_model_access(user, None, BYPASS_MODEL_ACCESS_CONTROL)

    url, key, api_config = await get_openai_connection(model['urlIdx'], user=user)
    prefix_id = api_config.get('prefix_id')
    payload['model'] = strip_provider_model_prefix(payload['model'], prefix_id)

    headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)
    return requested_model, payload, url, key, api_config, headers, cookies


async def count_anthropic_tokens(request: Request, form_data: dict, user: UserModel) -> int:
    """Forward an Anthropic token-count request through an OpenAI-compatible connection."""
    requested_model, payload, url, key, api_config, headers, cookies = await get_anthropic_token_count_target(
        request, form_data, user
    )
    request_url = f'{url.rstrip("/")}/messages/count_tokens'
    response = None
    session = None
    owns_session = False

    try:
        session, owns_session = await get_upstream_session(api_config)
        response = await session.request(
            method='POST',
            url=request_url,
            data=json.dumps(payload),
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=get_client_timeout(),
        )

        try:
            response_data = await response.json(loads=JSONCodec.loads)
        except Exception:
            response_data = await response.text()

        if response.status >= 400:
            await publish_model_provider_request_failed(
                request,
                actor=user,
                provider='openai-compatible',
                base_url=url,
                api_key=key,
                status=response.status,
                requested_model=requested_model,
                upstream_error=response_data,
            )
            raise HTTPException(status_code=response.status, detail=response_data)

        input_tokens = response_data.get('input_tokens') if isinstance(response_data, dict) else None
        if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
            raise HTTPException(status_code=502, detail='Invalid token-count response from upstream provider')

        return input_tokens
    except HTTPException:
        raise
    except Exception:
        log.exception('Failed to count Anthropic tokens for model %s', requested_model)
        raise HTTPException(status_code=502, detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR)
    finally:
        await cleanup_response(response, session if owns_session else None)


@router.get('/config')
async def get_config(request: Request, user=Depends(get_admin_user)):
    return await get_openai_config()


class OpenAIConfigForm(BaseModel):
    ENABLE_OPENAI_API: bool | None = None
    OPENAI_API_BASE_URLS: list[str]
    OPENAI_API_KEYS: list[str]
    OPENAI_API_CONFIGS: dict


@router.post('/config/update')
async def update_config(request: Request, form_data: OpenAIConfigForm, user=Depends(get_admin_user)):
    api_keys = form_data.OPENAI_API_KEYS

    if len(api_keys) > len(form_data.OPENAI_API_BASE_URLS):
        api_keys = api_keys[: len(form_data.OPENAI_API_BASE_URLS)]
    elif len(api_keys) < len(form_data.OPENAI_API_BASE_URLS):
        api_keys = [*api_keys, *([''] * (len(form_data.OPENAI_API_BASE_URLS) - len(api_keys)))]

    valid_keys = set(map(str, range(len(form_data.OPENAI_API_BASE_URLS))))
    api_configs = {key: value for key, value in form_data.OPENAI_API_CONFIGS.items() if key in valid_keys}

    await Config.upsert(
        {
            'openai.enable': form_data.ENABLE_OPENAI_API,
            'openai.api_base_urls': form_data.OPENAI_API_BASE_URLS,
            'openai.api_keys': api_keys,
            'openai.api_configs': api_configs,
        }
    )

    await get_all_models.cache.clear()
    request.app.state.BASE_MODELS = []
    request.app.state.OPENAI_MODELS = {}
    models = getattr(request.app.state, 'MODELS', None)
    if hasattr(models, 'clear'):
        models.clear()
    else:
        request.app.state.MODELS = {}

    await publish_event(
        request,
        EVENTS.MODEL_PROVIDER_CONFIG_UPDATED,
        actor=user,
        subject_id='openai',
        subject_type='model.provider_config',
        data={
            'provider': 'openai',
            'enabled': form_data.ENABLE_OPENAI_API,
            'base_url_count': len(form_data.OPENAI_API_BASE_URLS),
        },
    )

    return {
        'ENABLE_OPENAI_API': form_data.ENABLE_OPENAI_API,
        'OPENAI_API_BASE_URLS': form_data.OPENAI_API_BASE_URLS,
        'OPENAI_API_KEYS': api_keys,
        'OPENAI_API_CONFIGS': api_configs,
    }


@router.post('/audio/speech')
async def speech(request: Request, user=Depends(get_verified_user)):
    require_metered_inference_entrypoint(request)
    if user.role != 'admin' and not await has_permission(user.id, 'chat.tts', await Config.get('user.permissions')):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    idx = None
    try:
        _, api_base_urls, _, _ = await get_openai_runtime_config()
        idx = api_base_urls.index('https://api.openai.com/v1')

        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = CACHE_DIR / 'audio' / 'speech'
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f'{name}.mp3')
        file_body_path = SPEECH_CACHE_DIR.joinpath(f'{name}.json')

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        url, key, api_config = await get_openai_connection(idx, user=user, use_personal=False)

        headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

        r = None
        try:
            session = await get_session()
            r = await session.post(
                url=f'{url}/audio/speech',
                data=body,
                headers=headers,
                cookies=cookies,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            )

            r.raise_for_status()

            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in r.content.iter_chunked(8192):
                    await f.write(chunk)

            async with aiofiles.open(file_body_path, 'w') as f:
                await f.write(json.dumps(json.loads(body.decode('utf-8'))))

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            log.exception(e)

            detail = None
            if r is not None:
                try:
                    res = await r.json(loads=JSONCodec.loads)
                    if 'error' in res:
                        detail = f'External: {res["error"]}'
                except Exception:
                    detail = f'External: {e}'

            raise HTTPException(
                status_code=r.status if r else 500,
                detail=detail if detail else 'Interact Ai: Server Connection Error',
            )

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def get_all_models_responses(
    request: Request,
    user: UserModel,
    runtime_connections: list[dict] | None = None,
) -> list:
    if runtime_connections is None:
        _, runtime_connections = await get_openai_runtime_connections(user)
    if not runtime_connections:
        return []

    request_tasks = []
    for idx, connection in enumerate(runtime_connections):
        url = connection['url']
        key = connection['key']
        api_config = connection['config']
        if not api_config:  # Legacy support
            request_tasks.append(get_models_request(request, url, key, user=user))
        else:
            enable = api_config.get('enable', True)
            model_ids = api_config.get('model_ids', [])

            if enable:
                if len(model_ids) == 0:
                    request_tasks.append(get_models_request(request, url, key, user=user, config=api_config))
                else:
                    model_list = {
                        'object': 'list',
                        'data': [
                            {
                                'id': model_id,
                                'name': model_id,
                                'owned_by': 'openai',
                                'openai': {'id': model_id},
                                'urlIdx': idx,
                            }
                            for model_id in model_ids
                        ],
                    }

                    request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, model_list)))
            else:
                request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))

    responses = await asyncio.gather(*request_tasks)

    for idx, response in enumerate(responses):
        if response:
            api_config = runtime_connections[idx]['config']

            connection_type = api_config.get('connection_type', 'external')
            prefix_id = api_config.get('prefix_id', None)
            tags = api_config.get('tags', [])
            provider = api_config.get('provider', '')

            model_list = response if isinstance(response, list) else response.get('data', [])
            if not isinstance(model_list, list):
                # Catch non-list responses
                model_list = []

            for model in model_list:
                # Remove name key if its value is None #16689
                if 'name' in model and model['name'] is None:
                    del model['name']

                if prefix_id:
                    model['id'] = f'{prefix_id}.{model.get("id", model.get("name", ""))}'

                if api_config.get('personal_owner_id'):
                    model['personal_owner_id'] = api_config['personal_owner_id']
                    model['personal_connection_id'] = api_config['personal_connection_id']
                    connection_name = api_config.get('personal_connection_name')
                    if connection_name:
                        original_name = model.get('name') or model.get('id')
                        model['name'] = f'{original_name} · {connection_name}'

                if tags:
                    model['tags'] = tags

                if connection_type:
                    model['connection_type'] = connection_type

                if provider:
                    model['provider'] = provider

    log.debug(f'get_all_models:responses() {responses}')
    return responses


async def get_filtered_models(models, user, db=None):
    # Filter models based on user access control
    model_ids = [model['id'] for model in models.get('data', [])]
    model_infos = {model_info.id: model_info for model_info in await Models.get_models_by_ids(model_ids, db=db)}
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    # Batch-fetch accessible resource IDs in a single query instead of N has_access calls
    accessible_model_ids = await AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type='model',
        resource_ids=list(model_infos.keys()),
        permission='read',
        user_group_ids=user_group_ids,
        db=db,
    )

    filtered_models = []
    for model in models.get('data', []):
        model_info = model_infos.get(model['id'])
        if model_info:
            if user.id == model_info.user_id or model_info.id in accessible_model_ids:
                filtered_models.append(model)
    return filtered_models


@cached(
    ttl=MODELS_CACHE_TTL,
    # key_builder (not key) is the per-call hook in aiocache 0.12; `key=` is a
    # static key, so a `key=lambda` collapsed every caller to one shared entry.
    key_builder=lambda _func, request, user=None: f'openai_all_models_{user.id}' if user else 'openai_all_models',
)
async def get_all_models(request: Request, user: UserModel) -> dict[str, list]:
    log.info('get_all_models()')

    personal_mode, runtime_connections = await get_openai_runtime_connections(user)
    if not runtime_connections:
        if not personal_mode:
            request.app.state.OPENAI_MODELS = {}
        return {'data': [], 'personal_mode': personal_mode}

    responses = await get_all_models_responses(
        request,
        user=user,
        runtime_connections=runtime_connections,
    )

    def extract_data(response):
        if response and 'data' in response:
            return response['data']
        if isinstance(response, list):
            return response
        return None

    def is_supported_openai_models(model_id):
        return not any(name in model_id for name in _UNSUPPORTED_OPENAI_MODEL_KEYWORDS)

    def get_merged_models(model_lists):
        log.debug(f'merge_models_lists {model_lists}')
        models = {}

        for idx, model_list in enumerate(model_lists):
            if model_list is not None and 'error' not in model_list:
                base_url = runtime_connections[idx]['url']
                hostname = urlparse(base_url).hostname if base_url else None
                api_config = runtime_connections[idx]['config']

                for model in model_list:
                    model_id = model.get('id') or model.get('name')

                    if hostname == 'api.openai.com' and not is_supported_openai_models(model_id):
                        # Skip unwanted OpenAI models
                        continue

                    if model_id and model_id not in models:
                        provider = model.get('provider', '')
                        merged = {
                            **model,
                            'name': model.get('name', model_id),
                            'owned_by': 'openai',
                            'openai': model,
                            'connection_type': model.get('connection_type', 'external'),
                            'provider': provider,
                            'urlIdx': idx,
                        }

                        loaded = get_llamacpp_model_loaded_state(
                            model,
                            provider,
                            manual_model_ids=bool(api_config.get('model_ids')),
                        )
                        if loaded is not None:
                            merged['loaded'] = loaded

                        models[model_id] = merged

        return models

    models = get_merged_models(map(extract_data, responses))
    log.debug(f'models: {models}')

    if not personal_mode:
        request.app.state.OPENAI_MODELS = models
    return {'data': list(models.values()), 'personal_mode': personal_mode}


async def get_user_openai_models(request: Request, user: UserModel) -> dict[str, dict]:
    """Return provider routing from this user's cached model inventory."""
    response = await get_all_models(request, user=user)
    return {
        model['id']: model
        for model in response.get('data', [])
        if isinstance(model, dict) and model.get('id')
    }


@router.get('/models')
@router.get('/models/{url_idx}')
async def get_models(request: Request, url_idx: int | None = None, user=Depends(get_verified_user)):
    models = {
        'data': [],
    }

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        if not await Config.get('openai.enable'):
            raise HTTPException(status_code=503, detail='OpenAI API is disabled')
        url, key, api_config = await get_openai_connection(url_idx, user=user, use_personal=False)

        r = None
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=_MODEL_LIST_TIMEOUT,
        ) as session:
            try:
                headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

                if api_config.get('azure') or api_config.get('provider') == 'azure':
                    models = {
                        'data': api_config.get('model_ids', []) or [],
                        'object': 'list',
                    }
                elif is_anthropic_url(url):
                    models = await get_anthropic_models(url, key, user=user)
                    if models is None:
                        raise Exception('Failed to connect to Anthropic API')
                else:
                    async with session.get(
                        f'{url}/models',
                        headers=headers,
                        cookies=cookies,
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    ) as r:
                        if r.status != 200:
                            error_detail = f'HTTP Error: {r.status}'
                            try:
                                res = await r.json(loads=JSONCodec.loads)
                                if 'error' in res:
                                    error_detail = f'External Error: {res["error"]}'
                            except Exception:
                                pass
                            raise Exception(error_detail)

                        response_data = await r.json(loads=JSONCodec.loads)

                        if 'api.openai.com' in url:
                            response_data['data'] = [
                                model
                                for model in response_data.get('data', [])
                                if not any(name in model['id'] for name in _UNSUPPORTED_OPENAI_MODEL_KEYWORDS)
                            ]

                        models = response_data
            except aiohttp.ClientError as e:
                # ClientError covers all aiohttp requests issues
                log.exception(f'Client error: {str(e)}')
                raise HTTPException(status_code=500, detail='Interact Ai: Server Connection Error')
            except Exception as e:
                log.exception(f'Unexpected error: {e}')
                error_detail = f'Unexpected error: {str(e)}'
                raise HTTPException(status_code=500, detail=error_detail)

    if user.role == 'user' and not BYPASS_MODEL_ACCESS_CONTROL:
        models['data'] = await get_filtered_models(models, user)

    return models


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str

    config: dict | None = None


@router.post('/verify')
async def verify_connection(
    request: Request,
    form_data: ConnectionVerificationForm,
    user=Depends(get_admin_user),
):
    url = form_data.url
    key = form_data.key

    api_config = form_data.config or {}

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=_MODEL_LIST_TIMEOUT,
    ) as session:
        try:
            headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

            if api_config.get('azure') or api_config.get('provider') == 'azure':
                # Only set api-key header if not using Azure Entra ID authentication
                auth_type = api_config.get('auth_type', 'bearer')
                if auth_type not in ('azure_ad', 'microsoft_entra_id'):
                    headers['api-key'] = key

                # Azure v1 format: base URL already ends with /openai/v1,
                # use standard /models endpoint without api-version.
                is_azure_v1 = bool(re.search(r'/openai/v1(?:/|$)', url))

                if is_azure_v1:
                    verify_url = f'{url.rstrip("/")}/models'
                else:
                    api_version = api_config.get('api_version', '') or '2023-03-15-preview'
                    verify_url = f'{url}/openai/models?api-version={api_version}'

                async with session.get(
                    url=verify_url,
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json(loads=JSONCodec.loads)
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(status_code=r.status, content=response_data)
                        else:
                            return PlainTextResponse(status_code=r.status, content=response_data)

                    return response_data
            elif is_anthropic_url(url):
                result = await get_anthropic_models(url, key)
                if result is None:
                    raise HTTPException(status_code=500, detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR)
                if 'error' in result:
                    raise HTTPException(status_code=500, detail=result['error'])
                return result
            else:
                async with session.get(
                    f'{url}/models',
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json(loads=JSONCodec.loads)
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(status_code=r.status, content=response_data)
                        else:
                            return PlainTextResponse(status_code=r.status, content=response_data)

                    return response_data

        except aiohttp.ClientError as e:
            # ClientError covers all aiohttp requests issues
            log.exception(f'Client error: {str(e)}')
            raise HTTPException(status_code=500, detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR)
        except Exception as e:
            log.exception(f'Unexpected error: {e}')
            raise HTTPException(status_code=500, detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR)


def get_azure_allowed_params(api_version: str) -> set[str]:
    allowed_params = {
        'messages',
        'temperature',
        'role',
        'content',
        'contentPart',
        'contentPartImage',
        'enhancements',
        'dataSources',
        'n',
        'stream',
        'stop',
        'max_tokens',
        'presence_penalty',
        'frequency_penalty',
        'logit_bias',
        'user',
        'function_call',
        'functions',
        'tools',
        'tool_choice',
        'top_p',
        'log_probs',
        'top_logprobs',
        'response_format',
        'seed',
        'max_completion_tokens',
        'reasoning_effort',
    }

    try:
        if api_version >= '2024-09-01-preview':
            allowed_params.add('stream_options')
    except ValueError:
        log.debug(f'Invalid API version {api_version} for Azure OpenAI. Defaulting to allowed parameters.')

    return allowed_params


def is_openai_new_model(model: str) -> bool:
    model_lower = model.lower()
    # o-series models (o1, o3, o4, o5, ...)
    if re.match(r'^o\d+', model_lower):
        return True
    # gpt-N where N >= 5 (gpt-5, gpt-5.2, gpt-6, ...)
    m = re.match(r'^gpt-(\d+)', model_lower)
    if m and int(m.group(1)) >= 5:
        return True
    return False


def _sanitize_model_for_url(model: str) -> str:
    """Sanitize a model name before interpolating it into a URL path.

    Rejects path traversal attempts (../, /, \\) and percent-encodes
    the name so it is safe to use as a single URL path segment
    (e.g. Azure deployment name).
    """
    if not model or '..' in model or '/' in model or '\\' in model:
        raise HTTPException(
            status_code=400,
            detail='Invalid model name: must not be empty or contain path separators or traversal sequences',
        )
    return quote(model, safe='')


def convert_to_azure_payload(url, payload: dict, api_version: str):
    model = payload.get('model', '')

    # Filter allowed parameters based on Azure OpenAI API
    allowed_params = get_azure_allowed_params(api_version)

    # Special handling for o-series models
    if is_openai_new_model(model):
        # Convert max_tokens to max_completion_tokens for o-series models
        if 'max_tokens' in payload:
            payload['max_completion_tokens'] = payload['max_tokens']
            del payload['max_tokens']

        # Remove temperature if not 1 for o-series models
        if 'temperature' in payload and payload['temperature'] != 1:
            log.debug(
                f'Removing temperature parameter for o-series model {model} as only default value (1) is supported'
            )
            del payload['temperature']

    # Filter out unsupported parameters
    payload = {k: v for k, v in payload.items() if k in allowed_params}

    # Sanitize model name to prevent path traversal in the deployment URL
    model = _sanitize_model_for_url(model)

    url = f'{url}/openai/deployments/{model}'
    return url, payload


# Fields accepted by the Responses API for each input item type.
RESPONSES_ALLOWED_FIELDS: dict[str, set[str]] = {
    'message': {'type', 'role', 'content'},
    'function_call': {'type', 'call_id', 'name', 'arguments', 'id'},
    'function_call_output': {'type', 'call_id', 'output'},
}


def _normalize_stored_item(item: dict) -> dict:
    """Strip local-only fields from a stored output item before replaying it.

    Interact Ai stores extra bookkeeping fields (``id``, ``status``,
    ``started_at``, ``ended_at``, ``duration``, ``_tag_type``,
    ``attributes``, ``summary``, etc.) that the Responses API does
    not accept.  This helper returns a copy containing only the
    fields the API understands.
    """
    item_type = item.get('type', '')
    allowed = RESPONSES_ALLOWED_FIELDS.get(item_type)
    if allowed is None:
        # Unknown type — pass through as-is (e.g. reasoning, extension items).
        return item
    return {k: v for k, v in item.items() if k in allowed}


def convert_to_responses_payload(payload: dict) -> dict:
    """
    Convert Chat Completions payload to Responses API format.

    Chat Completions: { messages: [{role, content}], ... }
    Responses API: { input: [{type: "message", role, content: [...]}], instructions: "system" }
    """
    messages = payload.pop('messages', [])

    system_content = ''
    input_items = []

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        # Check for stored output items (from previous Responses API turn)
        stored_output = msg.get('output')
        if stored_output and isinstance(stored_output, list):
            input_items.extend(_normalize_stored_item(item) for item in stored_output)
            continue

        if role == 'system':
            if isinstance(content, str):
                system_content = content
            elif isinstance(content, list):
                system_content = '\n'.join(p.get('text', '') for p in content if p.get('type') == 'text')
            continue

        # Handle assistant messages with tool_calls (from convert_output_to_messages)
        if role == 'assistant' and msg.get('tool_calls'):
            # Add text content as message if present
            if content:
                text = (
                    content
                    if isinstance(content, str)
                    else '\n'.join(p.get('text', '') for p in content if p.get('type') == 'text')
                )
                if text.strip():
                    input_items.append(
                        {
                            'type': 'message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': text}],
                        }
                    )
            # Convert each tool_call to a function_call input item
            for tool_call in msg['tool_calls']:
                func = tool_call.get('function', {})
                input_items.append(
                    {
                        'type': 'function_call',
                        'call_id': tool_call.get('id', ''),
                        'name': func.get('name', ''),
                        'arguments': func.get('arguments', '{}'),
                    }
                )
            continue

        # Handle tool result messages
        if role == 'tool':
            input_items.append(
                {
                    'type': 'function_call_output',
                    'call_id': msg.get('tool_call_id', ''),
                    'output': msg.get('content', ''),
                }
            )
            continue

        # Convert content format
        text_type = 'output_text' if role == 'assistant' else 'input_text'

        if isinstance(content, str):
            content_parts = [{'type': text_type, 'text': content}]
        elif isinstance(content, list):
            content_parts = []
            for part in content:
                if part.get('type') == 'text':
                    content_parts.append({'type': text_type, 'text': part.get('text', '')})
                elif part.get('type') == 'image_url':
                    url_data = part.get('image_url', {})
                    url = url_data.get('url', '') if isinstance(url_data, dict) else url_data
                    content_parts.append({'type': 'input_image', 'image_url': url})
        else:
            content_parts = [{'type': text_type, 'text': str(content)}]

        input_items.append({'type': 'message', 'role': role, 'content': content_parts})

    responses_payload = {**payload, 'input': input_items}

    # Forward previous_response_id when the middleware has set it
    # (only used when ENABLE_RESPONSES_API_STATEFUL is enabled).
    previous_response_id = responses_payload.pop('previous_response_id', None)
    if previous_response_id:
        responses_payload['previous_response_id'] = previous_response_id

    if system_content:
        responses_payload['instructions'] = system_content

    if 'max_tokens' in responses_payload:
        responses_payload['max_output_tokens'] = responses_payload.pop('max_tokens')

    if 'max_completion_tokens' in responses_payload:
        responses_payload['max_output_tokens'] = responses_payload.pop('max_completion_tokens')

    # Remove Chat Completions-only parameters not supported by the Responses API
    for unsupported_key in (
        'stream_options',
        'logit_bias',
        'frequency_penalty',
        'presence_penalty',
        'stop',
    ):
        responses_payload.pop(unsupported_key, None)

    # Convert Chat Completions tools format to Responses API format
    # Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    # Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    if 'tools' in responses_payload and isinstance(responses_payload['tools'], list):
        converted_tools = []
        for tool in responses_payload['tools']:
            if isinstance(tool, dict) and 'function' in tool:
                func = tool['function']
                converted_tool = {'type': tool.get('type', 'function')}
                if isinstance(func, dict):
                    converted_tool['name'] = func.get('name', '')
                    if 'description' in func:
                        converted_tool['description'] = func['description']
                    if 'parameters' in func:
                        converted_tool['parameters'] = func['parameters']
                    if 'strict' in func:
                        converted_tool['strict'] = func['strict']
                converted_tools.append(converted_tool)
            else:
                # Already in correct format or unknown format, pass through
                converted_tools.append(tool)
        responses_payload['tools'] = converted_tools

    return responses_payload


def convert_responses_result(response: dict) -> dict:
    """
    Convert non-streaming Responses API result to Chat Completions format.

    Extracts text from message output items so all downstream consumers
    (frontend tasks, get_content_from_response) work without modification.
    """
    output_items = response.get('output', [])

    content = ''
    for item in output_items:
        if item.get('type') == 'message':
            for part in item.get('content', []):
                if part.get('type') == 'output_text':
                    content += part.get('text', '')

    return {
        'id': response.get('id', ''),
        'object': 'chat.completion',
        'model': response.get('model', ''),
        'choices': [
            {
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': content,
                },
                'finish_reason': 'stop',
            }
        ],
        'usage': response.get('usage', {}),
    }


@router.post('/chat/completions')
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
):
    require_metered_inference_entrypoint(request)
    if not await Config.get('openai.enable') and not await UserProviderCredentials.has_for_user(user.id):
        raise HTTPException(status_code=503, detail='OpenAI API is disabled')

    # NOTE: We intentionally do NOT use Depends(get_async_session) here.
    # Database operations (get_model_by_id, AccessGrants.has_access) manage their own short-lived sessions.
    # This prevents holding a connection during the entire LLM call (30-60+ seconds),
    # which would exhaust the connection pool under concurrent load.

    # bypass_filter and bypass_system_prompt are read from request.state to prevent
    # external clients from setting them via query parameter. Only internal
    # server-side callers (e.g. utils/chat.py) should set
    # request.state.bypass_filter / request.state.bypass_system_prompt = True.
    bypass_filter = getattr(request.state, 'bypass_filter', False)
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True
    bypass_system_prompt = getattr(request.state, 'bypass_system_prompt', False)

    idx = 0

    payload = {**form_data}
    metadata = payload.pop('metadata', None)

    model_id = form_data.get('model')
    model_info = await Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            base_model_id = (
                request.base_model_id if hasattr(request, 'base_model_id') else model_info.base_model_id
            )  # Use request's base_model_id if available
            payload['model'] = base_model_id
            model_id = base_model_id

        params = model_info.params.model_dump()

        if params:
            system = params.pop('system', None)

            payload = apply_model_params_to_body_openai(params, payload)
            if not bypass_system_prompt:
                payload = await apply_system_prompt_to_body(system, payload, metadata, user)

        await check_model_access(user, model_info, bypass_filter)

    models = await get_user_openai_models(request, user)
    model = models.get(model_id)

    if model:
        idx = model['urlIdx']
    else:
        raise HTTPException(
            status_code=404,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )
    if not model_info and model.get('personal_owner_id') != user.id:
        await check_model_access(user, None, bypass_filter)

    url, key, api_config = await get_openai_connection(idx, user=user)

    prefix_id = api_config.get('prefix_id', None)
    payload['model'] = strip_provider_model_prefix(payload['model'], prefix_id)

    # Add user info to the payload if the model is a pipeline
    if 'pipeline' in model and model.get('pipeline'):
        payload['user'] = {
            'name': user.name,
            'id': user.id,
            'email': user.email,
            'role': user.role,
        }

    # Check if model is a reasoning model that needs special handling
    if is_openai_new_model(payload['model']):
        payload = openai_reasoning_model_handler(payload)
    elif 'api.openai.com' not in url:
        # Remove "max_completion_tokens" from the payload for backward compatibility
        if 'max_completion_tokens' in payload:
            payload['max_tokens'] = payload['max_completion_tokens']
            del payload['max_completion_tokens']

    if 'max_tokens' in payload and 'max_completion_tokens' in payload:
        del payload['max_tokens']

    # Convert the modified body back to JSON
    if 'logit_bias' in payload and payload['logit_bias']:
        logit_bias = convert_logit_bias_input_to_json(payload['logit_bias'])

        if logit_bias:
            payload['logit_bias'] = json.loads(logit_bias)

    headers, cookies = await get_headers_and_cookies(request, url, key, api_config, metadata, user=user)

    is_responses = api_config.get('api_type') == 'responses'

    if not is_responses:
        payload = apply_chat_completion_tool_compat(payload)

    if api_config.get('azure') or api_config.get('provider') == 'azure':
        # Only set api-key header if not using Azure Entra ID authentication
        auth_type = api_config.get('auth_type', 'bearer')
        if auth_type not in ('azure_ad', 'microsoft_entra_id'):
            headers['api-key'] = key

        # Azure v1 format: base URL already ends with /openai/v1,
        # model stays in the payload, no deployment URL rewriting.
        is_azure_v1 = bool(re.search(r'/openai/v1(?:/|$)', url))

        if is_azure_v1:
            if is_responses:
                payload = convert_to_responses_payload(payload)
                request_url = f'{url.rstrip("/")}/responses'
            else:
                request_url = f'{url.rstrip("/")}/chat/completions'
        else:
            api_version = api_config.get('api_version', '2023-03-15-preview')
            request_url, payload = convert_to_azure_payload(url, payload, api_version)
            headers['api-version'] = api_version

            if is_responses:
                payload = convert_to_responses_payload(payload)
                request_url = f'{request_url}/responses?api-version={api_version}'
            else:
                request_url = f'{request_url}/chat/completions?api-version={api_version}'
    else:
        if is_responses:
            payload = convert_to_responses_payload(payload)
            request_url = f'{url}/responses'
        else:
            request_url = f'{url}/chat/completions'
    requested_model = payload.get('model')
    # For Chat Completions, strip image parts from multimodal tool messages
    # (Chat Completions doesn't support images in tool content).
    if not is_responses and 'messages' in payload:
        for message in payload['messages']:
            if message.get('role') == 'tool' and isinstance(message.get('content'), list):
                message['content'] = ''.join(
                    part.get('text', '') for part in message['content'] if part.get('type') in ('input_text', 'text')
                )

    is_streaming_request = bool(payload.get('stream', False))
    if not is_streaming_request:
        payload.pop('stream_options', None)

    payload = json.dumps(payload)

    r = None
    streaming = False
    response = None
    session = None
    owns_session = False

    try:
        session, owns_session = await get_upstream_session(api_config)

        r = await request_with_rate_limit_retry(session,
            method='POST',
            url=request_url,
            data=payload,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=get_client_timeout(stream=is_streaming_request),
        )

        # Check if response is SSE
        if 'text/event-stream' in r.headers.get('Content-Type', ''):
            # If the provider returned an error status with SSE content-type,
            # read the body and return a proper error response instead of
            # streaming the error back (which hides the error from logs).
            if r.status >= 400:
                error_body = await r.text()
                log.error(
                    'Provider returned HTTP %d with SSE content-type: %s',
                    r.status,
                    error_body[:1000],
                )
                try:
                    error_json = json.loads(error_body)
                    await publish_model_provider_request_failed(
                        request,
                        actor=user,
                        provider='openai-compatible',
                        base_url=url,
                        api_key=key,
                        status=r.status,
                        requested_model=requested_model,
                        upstream_error=error_json,
                    )
                    return JSONResponse(status_code=r.status, content=error_json)
                except json.JSONDecodeError:
                    await publish_model_provider_request_failed(
                        request,
                        actor=user,
                        provider='openai-compatible',
                        base_url=url,
                        api_key=key,
                        status=r.status,
                        requested_model=requested_model,
                        upstream_error=error_body,
                    )
                    return JSONResponse(
                        status_code=r.status,
                        content={'error': {'message': error_body, 'code': r.status}},
                    )

            streaming = True
            return StreamingResponse(
                stream_wrapper(
                    r,
                    session=session if owns_session else None,
                    content_handler=stream_chunks_handler,
                ),
                status_code=r.status,
                headers=_clean_proxy_headers(r.headers),
            )
        else:
            try:
                response = await r.json(loads=JSONCodec.loads)
            except Exception as e:
                log.error(e)
                response = await r.text()

            if r.status >= 400:
                await publish_model_provider_request_failed(
                    request,
                    actor=user,
                    provider='openai-compatible',
                    base_url=url,
                    api_key=key,
                    status=r.status,
                    requested_model=requested_model,
                    upstream_error=response,
                )
                if isinstance(response, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response)
                else:
                    return PlainTextResponse(status_code=r.status, content=response)

            # Convert Responses API result to simple format
            if is_responses and isinstance(response, dict):
                response = convert_responses_result(response)

            return response
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=r.status if r else 500,
            detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR,
        )
    finally:
        if not streaming:
            await cleanup_response(r, session if owns_session else None)


async def embeddings(request: Request, form_data: dict, user):
    """
    Calls the embeddings endpoint for OpenAI-compatible providers.

    Args:
        request (Request): The FastAPI request context.
        form_data (dict): OpenAI-compatible embeddings payload.
        user (UserModel): The authenticated user.

    Returns:
        dict: OpenAI-compatible embeddings response.
    """
    idx = 0
    # Find correct backend url/key based on model
    model_id = form_data.get('model')
    models = await get_user_openai_models(request, user)
    if model_id not in models:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND())
    idx = models[model_id]['urlIdx']

    url, key, api_config = await get_openai_connection(idx, user=user)
    prefix_id = api_config.get('prefix_id')
    form_data = {
        **form_data,
        'model': strip_provider_model_prefix(form_data['model'], prefix_id),
    }
    body = json.dumps(form_data)

    r = None
    streaming = False
    session = None
    owns_session = False

    headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

    if api_config.get('azure') or api_config.get('provider') == 'azure':
        # Only set api-key header if not using Azure Entra ID authentication
        auth_type = api_config.get('auth_type', 'bearer')
        if auth_type not in ('azure_ad', 'microsoft_entra_id'):
            headers['api-key'] = key

        # Azure v1 format: base URL already ends with /openai/v1,
        # model stays in the payload, no deployment URL rewriting.
        is_azure_v1 = bool(re.search(r'/openai/v1(?:/|$)', url))

        if is_azure_v1:
            embeddings_url = f'{url.rstrip("/")}/embeddings'
        else:
            api_version = api_config.get('api_version', '2023-03-15-preview')
            model = _sanitize_model_for_url(form_data.get('model', ''))
            embeddings_url = f'{url}/openai/deployments/{model}/embeddings?api-version={api_version}'
            headers['api-version'] = api_version
    else:
        embeddings_url = f'{url}/embeddings'
    requested_model = model_id

    try:
        session, owns_session = await get_upstream_session(api_config)
        r = await session.request(
            method='POST',
            url=embeddings_url,
            data=body,
            headers=headers,
            cookies=cookies,
            timeout=get_client_timeout(),
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        if 'text/event-stream' in r.headers.get('Content-Type', ''):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session=session if owns_session else None, passthrough=True),
                status_code=r.status,
                headers=_clean_proxy_headers(r.headers),
            )
        else:
            try:
                response_data = await r.json(loads=JSONCodec.loads)
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                await publish_model_provider_request_failed(
                    request,
                    actor=user,
                    provider='openai-compatible',
                    base_url=url,
                    api_key=key,
                    status=r.status,
                    requested_model=requested_model,
                    upstream_error=response_data,
                )
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(status_code=r.status, content=response_data)

            return response_data
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR,
        )
    finally:
        if not streaming:
            await cleanup_response(r, session if owns_session else None)


class ResponsesForm(BaseModel):
    model_config = ConfigDict(extra='allow')

    model: str
    input: list | str | None = None
    instructions: str | None = None
    stream: bool | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    tools: list | None = None
    tool_choice: str | dict | None = None
    text: dict | None = None
    truncation: str | None = None
    metadata: dict | None = None
    store: bool | None = None
    reasoning: dict | None = None
    previous_response_id: str | None = None


@router.post('/responses')
async def responses(
    request: Request,
    form_data: ResponsesForm,
    user=Depends(get_verified_user),
):
    """
    Forward requests to the OpenAI Responses API endpoint.
    Routes to the correct upstream backend based on the model field.
    """
    require_metered_inference_entrypoint(request)
    payload = form_data.model_dump(exclude_none=True)
    is_streaming_request = bool(payload.get('stream', False))

    idx = 0
    model_id = form_data.model

    models = await get_user_openai_models(request, user)
    model = models.get(model_id)
    if not model:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND())
    model_info = await Models.get_model_by_id(model_id)
    if model_info:
        await check_model_access(user, model_info, BYPASS_MODEL_ACCESS_CONTROL)
    elif model.get('personal_owner_id') != user.id:
        await check_model_access(user, None, BYPASS_MODEL_ACCESS_CONTROL)
    idx = model['urlIdx']

    url, key, api_config = await get_openai_connection(idx, user=user)
    payload['model'] = strip_provider_model_prefix(payload['model'], api_config.get('prefix_id'))
    body = json.dumps(payload)

    r = None
    streaming = False
    session = None
    owns_session = False

    try:
        headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

        if api_config.get('azure') or api_config.get('provider') == 'azure':
            auth_type = api_config.get('auth_type', 'bearer')
            if auth_type not in ('azure_ad', 'microsoft_entra_id'):
                headers['api-key'] = key

            is_azure_v1 = bool(re.search(r'/openai/v1(?:/|$)', url))

            if is_azure_v1:
                request_url = f'{url.rstrip("/")}/responses'
            else:
                api_version = api_config.get('api_version', '2023-03-15-preview')
                headers['api-version'] = api_version
                model = _sanitize_model_for_url(payload.get('model', ''))
                request_url = f'{url}/openai/deployments/{model}/responses?api-version={api_version}'
        else:
            request_url = f'{url}/responses'

        session, owns_session = await get_upstream_session(api_config)
        r = await session.request(
            method='POST',
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=get_client_timeout(stream=is_streaming_request),
        )

        # Check if response is SSE
        if 'text/event-stream' in r.headers.get('Content-Type', ''):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session=session if owns_session else None, passthrough=True),
                status_code=r.status,
                headers=_clean_proxy_headers(r.headers),
            )
        else:
            try:
                response_data = await r.json(loads=JSONCodec.loads)
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                await publish_model_provider_request_failed(
                    request,
                    actor=user,
                    provider='openai-compatible',
                    base_url=url,
                    api_key=key,
                    status=r.status,
                    requested_model=payload.get('model'),
                    upstream_error=response_data,
                )
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(status_code=r.status, content=response_data)

            return response_data

    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR,
        )
    finally:
        if not streaming:
            await cleanup_response(r, session if owns_session else None)


@router.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    """
    Deprecated: proxy all requests to OpenAI API.
    Disabled by default. Set ENABLE_OPENAI_API_PASSTHROUGH=True to enable.
    """

    if not ENABLE_OPENAI_API_PASSTHROUGH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Direct API passthrough is disabled. Set ENABLE_OPENAI_API_PASSTHROUGH=True to enable.',
        )

    body = await request.body()

    # Parse JSON body to resolve model-based routing
    payload = None
    if body:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            payload = None
    is_streaming_request = bool(payload.get('stream', False)) if isinstance(payload, dict) else False

    idx = 0
    model_id = payload.get('model') if isinstance(payload, dict) else None
    if model_id:
        models = await get_user_openai_models(request, user)
        if model_id not in models:
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND())
        idx = models[model_id]['urlIdx']

    url, key, api_config = await get_openai_connection(idx, user=user)
    base_url = url
    if isinstance(payload, dict) and model_id:
        payload['model'] = strip_provider_model_prefix(model_id, api_config.get('prefix_id'))
        body = json.dumps(payload).encode()

    r = None
    streaming = False
    session = None
    owns_session = False

    try:
        headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)

        if api_config.get('azure') or api_config.get('provider') == 'azure':
            # Only set api-key header if not using Azure Entra ID authentication
            auth_type = api_config.get('auth_type', 'bearer')
            if auth_type not in ('azure_ad', 'microsoft_entra_id'):
                headers['api-key'] = key

            is_azure_v1 = bool(re.search(r'/openai/v1(?:/|$)', url))

            if is_azure_v1:
                qs = request.url.query
                request_url = f'{url.rstrip("/")}/{path}' + (f'?{qs}' if qs else '')
            else:
                api_version = api_config.get('api_version', '2023-03-15-preview')
                headers['api-version'] = api_version

                payload = json.loads(body)
                url, payload = convert_to_azure_payload(url, payload, api_version)
                body = json.dumps(payload).encode()

                request_url = f'{url}/{path}?api-version={api_version}'
        else:
            request_url = f'{url}/{path}'

        session, owns_session = await get_upstream_session(api_config)
        r = await session.request(
            method=request.method,
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=get_client_timeout(stream=is_streaming_request),
        )

        # Check if response is SSE
        if 'text/event-stream' in r.headers.get('Content-Type', ''):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session=session if owns_session else None, passthrough=True),
                status_code=r.status,
                headers=_clean_proxy_headers(r.headers),
            )
        else:
            try:
                response_data = await r.json(loads=JSONCodec.loads)
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                await publish_model_provider_request_failed(
                    request,
                    actor=user,
                    provider='openai-compatible',
                    base_url=base_url,
                    api_key=key,
                    status=r.status,
                    requested_model=model_id,
                    upstream_error=response_data,
                )
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(status_code=r.status, content=response_data)

            return response_data

    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail='Interact Ai: Server Connection Error',
        )
    finally:
        if not streaming:
            await cleanup_response(r, session if owns_session else None)
