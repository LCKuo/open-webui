import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import xml.etree.ElementTree as ET
from typing import Any, Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiohttp
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from open_webui.models.auths import Auths
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.interact_channels import (
    InteractChannelModel,
    InteractChannels,
)
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash
from open_webui.utils.automations import (
    _resolve_model_features,
    _resolve_model_filter_ids,
    _resolve_model_tool_ids,
)
from open_webui.utils.groups import apply_default_group_assignment
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from open_webui.utils.misc import get_message_list, validate_email_format
from open_webui.utils.models import get_all_models
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)

router = APIRouter()
CHANNEL_CACHE_TTL_SECONDS = 15
MAX_WEBHOOK_BODY_BYTES = 1_000_000
MAX_CHANNEL_HISTORY_MESSAGES = 40
_channel_cache: dict[tuple[str, str], tuple[float, InteractChannelModel]] = {}


def _service_token() -> str:
    return (
        os.environ.get('INTERACT_CHANNEL_SERVICE_TOKEN')
        or os.environ.get('INTERACT_BILLING_SERVICE_TOKEN')
        or os.environ.get('OPEN_WEBUI_BILLING_SERVICE_TOKEN')
        or ''
    ).strip()


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ''
    scheme, _, value = authorization.partition(' ')
    return value.strip() if scheme.lower() == 'bearer' else ''


def _require_service_token(
    authorization: str | None,
    x_interact_service_token: str | None,
) -> None:
    expected = _service_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Interact channel service token is not configured.',
        )

    supplied = (x_interact_service_token or '').strip() or _bearer_token(authorization)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid service token.')


class ChannelChatRequest(BaseModel):
    companyEmail: str = Field(..., min_length=3)
    channelType: Literal['line', 'wechat', 'telegram']
    channelIdentifier: str = Field(..., min_length=1)
    externalUserId: str = Field(..., min_length=1)
    platformEventId: str | None = Field(default=None, min_length=1, max_length=200)
    modelId: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=100000)
    system: str | None = None
    fallbackModelId: str | None = None
    metadata: dict[str, Any] | None = None
    maxTokens: int | None = Field(default=None, ge=1, le=32768)


class ProvisionAccountRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    companyName: str = Field(..., min_length=1, max_length=120)
    contactName: str = Field(..., min_length=1, max_length=80)


class ChannelHealthRequest(BaseModel):
    companyEmail: str = Field(..., min_length=3, max_length=320)


class ChannelSyncRequest(BaseModel):
    companyEmail: str = Field(..., min_length=3, max_length=320)
    channelType: Literal['line', 'wechat', 'telegram']
    channelIdentifier: str = Field(..., min_length=1, max_length=160)
    name: str = Field(..., min_length=1, max_length=80)
    enabled: bool = False
    replyMode: Literal['ai', 'fixed', 'handoff', 'silent'] = 'ai'
    accessMode: Literal['public', 'member', 'allowlist'] = 'public'
    channelSecret: str | None = Field(default=None, max_length=1000)
    channelAccessToken: str | None = Field(default=None, max_length=4000)
    modelId: str | None = Field(default=None, max_length=160)
    fallbackModelId: str | None = Field(default=None, max_length=160)
    fallbackMessage: str | None = Field(default=None, max_length=1000)
    rateLimitPerMinute: int = Field(default=5, ge=1, le=120)
    dailyUserLimit: int = Field(default=50, ge=1, le=10000)
    dailyBotTokenLimit: int = Field(default=100000, ge=1000, le=10000000)


def _stable_channel_chat_id(user_id: str, payload: ChannelChatRequest) -> str:
    stable_key = ':'.join(
        [
            'interact-channel',
            user_id,
            payload.channelType,
            payload.channelIdentifier,
            payload.externalUserId,
            payload.modelId,
        ]
    )
    return f'interact-channel-{uuid5(NAMESPACE_URL, stable_key)}'


def _stable_message_id(chat_id: str, platform_event_id: str, role: str) -> str:
    return str(uuid5(NAMESPACE_URL, f'{chat_id}:{platform_event_id}:{role}'))


async def _ensure_channel_chat(user_id: str, chat_id: str, payload: ChannelChatRequest):
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
    if chat:
        return chat

    title = f'{payload.channelType}:{payload.channelIdentifier}'
    return await Chats.insert_new_chat(
        chat_id,
        user_id,
        ChatForm(
            chat={
                'id': chat_id,
                'title': title,
                'models': [payload.modelId],
                'history': {
                    'currentId': None,
                    'messages': {},
                },
                'messages': [],
                'files': [],
                'tags': ['external-channel', payload.channelType],
                'meta': {
                    'source': 'interact-channel',
                    'channelType': payload.channelType,
                    'channelIdentifier': payload.channelIdentifier,
                    'externalUserId': payload.externalUserId,
                },
                'timestamp': int(time.time() * 1000),
            },
        ),
    )


def _message_content_from_response(data: dict[str, Any] | None) -> str:
    if not data:
        return ''
    choices = data.get('choices') or []
    if choices:
        message = choices[0].get('message') or {}
        content = message.get('content')
        if isinstance(content, str):
            return content
    content = data.get('content')
    return content if isinstance(content, str) else ''


async def _drain_streaming_response(response) -> None:
    if isinstance(response, StreamingResponse):
        async for _ in response.body_iterator:
            pass


def _response_data(response) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return response
    if isinstance(response, JSONResponse) and isinstance(response.body, bytes):
        try:
            return json.loads(response.body.decode('utf-8', 'replace'))
        except json.JSONDecodeError:
            return None
    return None


def _usage_tokens(usage: dict[str, Any] | None) -> int:
    usage = usage or {}
    total = (
        usage.get('billable_tokens')
        or usage.get('total_tokens')
        or (
            (usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
            + (usage.get('output_tokens') or usage.get('completion_tokens') or 0)
        )
    )
    return max(0, int(total or 0))


async def _cached_channel(
    channel_type: str,
    channel_identifier: str,
) -> InteractChannelModel | None:
    key = (channel_type, channel_identifier)
    cache_entry = _channel_cache.get(key)
    if cache_entry:
        cached_at, cached = cache_entry
        if cached.enabled and time.monotonic() - cached_at < CHANNEL_CACHE_TTL_SECONDS:
            return cached
    channel = await InteractChannels.get_enabled(channel_type, channel_identifier)
    if channel:
        _channel_cache[key] = (time.monotonic(), channel)
    else:
        _channel_cache.pop(key, None)
    return channel


def _fallback_text(channel: InteractChannelModel) -> str:
    return channel.fallback_message or '目前 AI 助手暫停服務，我們已收到您的訊息，稍後會由專人回覆。'


def _rate_limit_text(channel: InteractChannelModel, reason: str | None) -> str:
    if reason == 'daily-token':
        return channel.fallback_message or '今日 AI 使用額度已達上限，請稍後再試或聯絡服務人員。'
    if reason == 'daily-user':
        return '您今日的詢問次數已達上限，請明日再試。'
    return '訊息傳送速度過快，請稍候一分鐘後再試。'


def _taipei_day_start() -> int:
    now = dt.datetime.now().astimezone()
    offset = 8 * 60 * 60
    taipei_seconds = int(now.timestamp()) + offset
    return taipei_seconds - (taipei_seconds % 86400) - offset


def _estimated_reservation_tokens(message: str) -> int:
    return 2048 + max(1, (len(message) + 1) // 2)


async def _run_channel_message(
    request: Request,
    channel: InteractChannelModel,
    external_user_id: str,
    platform_event_id: str,
    message: str,
) -> dict[str, Any]:
    if channel.reply_mode != 'ai':
        return {'ok': True, 'content': _fallback_text(channel), 'usage': {}}
    if not channel.model_id:
        return {
            'ok': False,
            'content': _fallback_text(channel),
            'usage': {},
            'reason': 'missing-model',
        }

    try:
        return await channel_chat(
            request,
            ChannelChatRequest(
                companyEmail=channel.company_email,
                channelType=channel.channel_type,
                channelIdentifier=channel.channel_identifier,
                externalUserId=external_user_id,
                platformEventId=platform_event_id,
                modelId=channel.model_id,
                fallbackModelId=channel.fallback_model_id,
                message=message,
                metadata={
                    'channelId': channel.id,
                    'channelName': channel.name,
                },
            ),
            x_interact_service_token=_service_token(),
        )
    except Exception as error:
        log.exception('Interact platform message failed')
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        return {
            'ok': False,
            'content': _fallback_text(channel),
            'usage': {},
            'reason': str(detail)[:1000],
        }


async def _claim_and_respond(
    request: Request,
    channel: InteractChannelModel,
    platform_event_id: str,
    external_user_id: str,
    message: str,
) -> tuple[Any, str | None]:
    reserved_tokens = _estimated_reservation_tokens(message) if channel.reply_mode == 'ai' else 0
    claim = await InteractChannels.claim_event(
        channel,
        platform_event_id,
        external_user_id,
        reserved_tokens,
        _taipei_day_start(),
    )
    if claim.duplicate:
        return claim, claim.response_text
    if not claim.allowed:
        content = _rate_limit_text(channel, claim.reason)
        await InteractChannels.set_response(claim.event_id, content, 0, claim.reason)
        return claim, content

    result = await _run_channel_message(
        request,
        channel,
        external_user_id,
        platform_event_id,
        message,
    )
    content = str(result.get('content') or _fallback_text(channel)).strip()
    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
    await InteractChannels.set_response(
        claim.event_id,
        content,
        _usage_tokens(usage) or reserved_tokens,
        result.get('reason'),
    )
    return claim, content


async def _mark_delivery(claim, error: Exception | None = None) -> None:
    await InteractChannels.mark_delivery(
        claim.event_id,
        str(error) if error else None,
    )


async def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> None:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status < 200 or response.status >= 300:
                detail = (await response.text())[:300]
                raise RuntimeError(f'Platform reply failed ({response.status}): {detail}')


async def _read_webhook_body(request: Request) -> bytes:
    content_length = request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail='Webhook payload is too large.')
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid Content-Length header.')
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail='Webhook payload is too large.')
    return body


async def _send_line_reply(
    channel: InteractChannelModel,
    reply_token: str | None,
    text: str,
) -> None:
    if not channel.channel_access_token or not reply_token:
        raise RuntimeError('LINE access token or reply token is missing.')
    await _http_post_json(
        'https://api.line.me/v2/bot/message/reply',
        {
            'replyToken': reply_token,
            'messages': [{'type': 'text', 'text': text[:5000]}],
        },
        {
            'Authorization': f'Bearer {channel.channel_access_token}',
            'Content-Type': 'application/json',
        },
    )


async def _send_telegram_reply(
    channel: InteractChannelModel,
    chat_id: str | int | None,
    text: str,
) -> None:
    if not channel.channel_secret or chat_id is None:
        raise RuntimeError('Telegram bot token or chat ID is missing.')
    await _http_post_json(
        f'https://api.telegram.org/bot{channel.channel_secret}/sendMessage',
        {'chat_id': chat_id, 'text': text[:4096]},
    )


def _safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def _verify_wechat(channel: InteractChannelModel, request: Request) -> bool:
    token = channel.channel_access_token or channel.channel_secret
    signature = request.query_params.get('signature')
    timestamp = request.query_params.get('timestamp')
    nonce = request.query_params.get('nonce')
    if not token or not signature or not timestamp or not nonce:
        return False
    expected = hashlib.sha1(''.join(sorted([token, timestamp, nonce])).encode()).hexdigest()
    return _safe_equal(expected, signature)


def _xml_value(root: ET.Element, name: str) -> str:
    node = root.find(name)
    return node.text if node is not None and node.text else ''


def _wechat_response(to_user: str, from_user: str, text: str) -> str:
    safe_text = text[:2048].replace(']]>', ']]]]><![CDATA[>')
    return (
        '<xml>'
        f'<ToUserName><![CDATA[{from_user}]]></ToUserName>'
        f'<FromUserName><![CDATA[{to_user}]]></FromUserName>'
        f'<CreateTime>{int(time.time())}</CreateTime>'
        '<MsgType><![CDATA[text]]></MsgType>'
        f'<Content><![CDATA[{safe_text}]]></Content>'
        '</xml>'
    )


@router.post('/channel-chat')
async def channel_chat(  # noqa: C901
    request: Request,
    payload: ChannelChatRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)

    user = await Users.get_user_by_email(payload.companyEmail)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Open WebUI user not found.')
    if user.role == 'pending':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Open WebUI user is pending approval.')

    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    model_id = payload.modelId
    if model_id not in request.app.state.MODELS:
        if payload.fallbackModelId and payload.fallbackModelId in request.app.state.MODELS:
            model_id = payload.fallbackModelId
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Configured Open WebUI model was not found.',
            )

    chat_id = _stable_channel_chat_id(user.id, payload)
    chat = await _ensure_channel_chat(user.id, chat_id, payload)
    if not chat:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Failed to create channel chat.')

    parent_id = (chat.chat.get('history') or {}).get('currentId')
    if payload.platformEventId:
        user_message_id = _stable_message_id(chat_id, payload.platformEventId, 'user')
        assistant_message_id = _stable_message_id(chat_id, payload.platformEventId, 'assistant')
        existing_assistant = await Chats.get_message_by_id_and_message_id(chat_id, assistant_message_id)
        if existing_assistant and existing_assistant.get('content'):
            return {
                'ok': True,
                'cached': True,
                'chatId': chat_id,
                'userMessageId': user_message_id,
                'assistantMessageId': assistant_message_id,
                'model': existing_assistant.get('model') or model_id,
                'content': existing_assistant.get('content') or '',
                'usage': existing_assistant.get('usage'),
            }
    else:
        user_message_id = str(uuid4())
        assistant_message_id = str(uuid4())

    stored_messages = get_message_list(
        (chat.chat.get('history') or {}).get('messages', {}),
        parent_id,
    )
    messages = [
        {
            'role': item.get('role'),
            'content': item.get('content'),
        }
        for item in stored_messages[-MAX_CHANNEL_HISTORY_MESSAGES:]
        if item.get('role') in {'user', 'assistant'} and item.get('content') not in (None, '')
    ]
    if payload.system:
        messages.insert(0, {'role': 'system', 'content': payload.system})
    messages.append({'role': 'user', 'content': payload.message})

    form_data = {
        'model': model_id,
        'messages': messages,
        'stream': False,
        'chat_id': chat_id,
        'id': assistant_message_id,
        'parent_id': parent_id,
        'user_message': {
            'id': user_message_id,
            'parentId': parent_id,
            'role': 'user',
            'content': payload.message,
            'childrenIds': [assistant_message_id],
            'timestamp': int(time.time()),
            'models': [model_id],
        },
        'background_tasks': {},
        'interact_channel': {
            'source': 'channel',
            'channelType': payload.channelType,
            'channelId': (payload.metadata or {}).get('channelId'),
        },
    }
    if payload.maxTokens:
        form_data['max_completion_tokens'] = payload.maxTokens

    tool_ids = _resolve_model_tool_ids(request.app, model_id)
    features = _resolve_model_features(request.app, model_id)
    filter_ids = _resolve_model_filter_ids(request.app, model_id)
    if tool_ids:
        form_data['tool_ids'] = tool_ids
    if features:
        form_data['features'] = features
    if filter_ids:
        form_data['filter_ids'] = filter_ids

    try:
        response = await request.app.state.CHAT_COMPLETION_HANDLER(request, form_data, user=user)
        await _drain_streaming_response(response)
    except HTTPException:
        raise
    except Exception as e:
        log.exception('Interact channel chat failed')
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    assistant_message = await Chats.get_message_by_id_and_message_id(chat_id, assistant_message_id)
    content = (assistant_message or {}).get('content') or _message_content_from_response(_response_data(response))
    usage = (assistant_message or {}).get('usage')

    return {
        'ok': True,
        'chatId': chat_id,
        'userMessageId': user_message_id,
        'assistantMessageId': assistant_message_id,
        'model': model_id,
        'content': content or '',
        'usage': usage,
    }


@router.put('/channels/{channel_id}')
async def sync_channel(
    channel_id: str,
    payload: ChannelSyncRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    user = await Users.get_user_by_email(payload.companyEmail.strip().lower())
    if not user:
        raise HTTPException(status_code=404, detail='Open WebUI user not found.')
    if user.role == 'pending':
        raise HTTPException(status_code=403, detail='Open WebUI user is pending approval.')

    try:
        channel = await InteractChannels.upsert(
            {
                'id': channel_id,
                'company_email': payload.companyEmail,
                'channel_type': payload.channelType,
                'channel_identifier': payload.channelIdentifier,
                'name': payload.name,
                'enabled': payload.enabled,
                'reply_mode': payload.replyMode,
                'access_mode': payload.accessMode,
                'channel_secret': payload.channelSecret,
                'channel_access_token': payload.channelAccessToken,
                'model_id': payload.modelId,
                'fallback_model_id': payload.fallbackModelId,
                'fallback_message': payload.fallbackMessage,
                'rate_limit_per_minute': payload.rateLimitPerMinute,
                'daily_user_limit': payload.dailyUserLimit,
                'daily_bot_token_limit': payload.dailyBotTokenLimit,
            }
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail='The platform channel identifier is already bound.',
        )

    for key, (_, cached) in list(_channel_cache.items()):
        if cached.id == channel_id:
            _channel_cache.pop(key, None)
    if channel.enabled:
        _channel_cache[(channel.channel_type, channel.channel_identifier)] = (
            time.monotonic(),
            channel,
        )
    return {
        'ok': True,
        'channelId': channel.id,
        'webhookPath': (
            f'/api/v1/interact/webhooks/{channel.channel_type}/{quote(channel.channel_identifier, safe="")}'
        ),
        'enabled': channel.enabled,
        'updatedAt': channel.updated_at,
    }


@router.delete('/channels/{channel_id}')
async def delete_channel(
    channel_id: str,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    deleted = await InteractChannels.delete(channel_id)
    for key, (_, cached) in list(_channel_cache.items()):
        if cached.id == channel_id:
            _channel_cache.pop(key, None)
    return {'ok': True, 'deleted': deleted}


@router.get('/webhooks/{channel_type}/{channel_identifier}')
async def verify_platform_webhook(
    request: Request,
    channel_type: Literal['line', 'wechat', 'telegram'],
    channel_identifier: str,
):
    channel = await _cached_channel(channel_type, channel_identifier)
    if not channel:
        raise HTTPException(status_code=404, detail='Channel not found or disabled.')
    if channel_type == 'wechat':
        if not _verify_wechat(channel, request):
            raise HTTPException(status_code=401, detail='Invalid WeChat signature.')
        return PlainTextResponse(request.query_params.get('echostr') or '')
    return {
        'ok': True,
        'channelType': channel_type,
        'channelIdentifier': channel_identifier,
        'enabled': True,
    }


@router.post('/webhooks/{channel_type}/{channel_identifier}')
async def platform_webhook(  # noqa: C901
    request: Request,
    channel_type: Literal['line', 'wechat', 'telegram'],
    channel_identifier: str,
):
    channel = await _cached_channel(channel_type, channel_identifier)
    if not channel:
        raise HTTPException(status_code=404, detail='Channel not found or disabled.')

    if channel_type == 'line':
        raw_body = await _read_webhook_body(request)
        signature = request.headers.get('x-line-signature') or ''
        if not channel.channel_secret or not signature:
            raise HTTPException(status_code=401, detail='Invalid LINE signature.')
        expected = base64.b64encode(
            hmac.new(
                channel.channel_secret.encode(),
                raw_body,
                hashlib.sha256,
            ).digest()
        ).decode()
        if not _safe_equal(expected, signature):
            raise HTTPException(status_code=401, detail='Invalid LINE signature.')

        try:
            payload = json.loads(raw_body.decode('utf-8') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail='Invalid LINE payload.')
        results = []
        for index, event in enumerate(payload.get('events') or []):
            message = event.get('message') or {}
            if channel.reply_mode == 'silent' or message.get('type') != 'text':
                results.append({'ok': True, 'skipped': True})
                continue
            source = event.get('source') or {}
            external_user_id = source.get('userId') or source.get('groupId') or source.get('roomId') or 'unknown'
            platform_event_id = (
                event.get('webhookEventId') or hashlib.sha256(raw_body + str(index).encode()).hexdigest()
            )
            claim, content = await _claim_and_respond(
                request,
                channel,
                platform_event_id,
                external_user_id,
                message.get('text') or '',
            )
            if claim.duplicate and not claim.retry_delivery:
                results.append({'ok': True, 'duplicate': True, 'skipped': True})
                continue
            try:
                await _send_line_reply(channel, event.get('replyToken'), content or '')
                await _mark_delivery(claim)
                results.append(
                    {
                        'ok': True,
                        'duplicate': claim.duplicate,
                        'rateLimited': claim.reason is not None,
                    }
                )
            except Exception as error:
                await _mark_delivery(claim, error)
                results.append({'ok': False, 'deliveryFailed': True})
        failed = any(not result.get('ok') for result in results)
        return JSONResponse(
            {
                'ok': not failed,
                'routed': True,
                'channelId': channel.id,
                'eventCount': len(results),
                'results': results,
            },
            status_code=502 if failed else 200,
        )

    if channel_type == 'telegram':
        expected = channel.channel_access_token or ''
        supplied = request.headers.get('x-telegram-bot-api-secret-token') or ''
        if not expected or not supplied or not _safe_equal(expected, supplied):
            raise HTTPException(status_code=401, detail='Invalid Telegram webhook secret.')
        try:
            payload = json.loads((await _read_webhook_body(request)).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail='Invalid Telegram payload.')
        message_data = payload.get('message') or {}
        message = message_data.get('text')
        if channel.reply_mode == 'silent' or not message:
            return {'ok': True, 'routed': True, 'channelId': channel.id, 'skipped': True}
        chat_id = (message_data.get('chat') or {}).get('id')
        external_user_id = str((message_data.get('from') or {}).get('id') or chat_id or 'unknown')
        platform_event_id = str(
            payload.get('update_id') or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        )
        claim, content = await _claim_and_respond(
            request,
            channel,
            platform_event_id,
            external_user_id,
            message,
        )
        if claim.duplicate and not claim.retry_delivery:
            return {'ok': True, 'duplicate': True, 'skipped': True}
        try:
            await _send_telegram_reply(channel, chat_id, content or '')
            await _mark_delivery(claim)
        except Exception as error:
            await _mark_delivery(claim, error)
            raise HTTPException(status_code=502, detail='Telegram delivery failed.')
        return {
            'ok': True,
            'routed': True,
            'channelId': channel.id,
            'duplicate': claim.duplicate,
            'rateLimited': claim.reason is not None,
        }

    if not _verify_wechat(channel, request):
        raise HTTPException(status_code=401, detail='Invalid WeChat signature.')
    raw_body = await _read_webhook_body(request)
    try:
        root = ET.fromstring(raw_body)
    except ET.ParseError:
        raise HTTPException(status_code=400, detail='Invalid WeChat payload.')
    to_user = _xml_value(root, 'ToUserName')
    from_user = _xml_value(root, 'FromUserName')
    message = _xml_value(root, 'Content')
    if channel.reply_mode == 'silent' or not message:
        return PlainTextResponse('success')
    platform_event_id = _xml_value(root, 'MsgId') or hashlib.sha256(raw_body).hexdigest()
    claim, content = await _claim_and_respond(
        request,
        channel,
        platform_event_id,
        from_user or 'unknown',
        message,
    )
    if claim.duplicate and not content:
        return PlainTextResponse('success')
    await _mark_delivery(claim)
    return Response(
        _wechat_response(to_user, from_user, content or ''),
        media_type='application/xml',
    )


@router.post('/health')
async def channel_health(
    payload: ChannelHealthRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    user = await Users.get_user_by_email(payload.companyEmail)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Open WebUI user not found.')
    if user.role == 'pending':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Open WebUI user is pending approval.')
    if not is_billing_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Interact billing is not configured.',
        )

    await InteractBillingClient().resolve_user(user)
    return {
        'ok': True,
        'service': 'interact-channel',
        'userVerified': True,
        'billingVerified': True,
    }


@router.post('/accounts/provision')
async def provision_account(
    request: Request,
    payload: ProvisionAccountRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)

    email = payload.email.strip().lower()
    display_name = payload.companyName.strip()
    contact_name = payload.contactName.strip()
    if not validate_email_format(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid email address.')
    if not display_name or not contact_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Company and contact names are required.')

    bio = f'聯絡人：{contact_name}'
    existing = await Users.get_user_by_email(email)

    if existing:
        updates = {}
        if existing.role == 'pending':
            updates['role'] = 'user'
        updated = await Users.update_user_by_id(existing.id, updates) if updates else existing
        await apply_default_group_assignment(
            request.app.state.config.DEFAULT_GROUP_ID,
            existing.id,
        )
        return {
            'ok': True,
            'created': False,
            'id': updated.id if updated else existing.id,
            'email': email,
            'name': updated.name if updated else existing.name,
            'role': updated.role if updated else existing.role,
            'temporaryPassword': None,
        }

    temporary_password = secrets.token_urlsafe(24)
    try:
        user = await Auths.insert_new_auth(
            email=email,
            password=get_password_hash(temporary_password),
            name=display_name,
            role='user',
        )
    except Exception:
        user = await Users.get_user_by_email(email)
        if not user:
            raise
        await apply_default_group_assignment(
            request.app.state.config.DEFAULT_GROUP_ID,
            user.id,
        )
        return {
            'ok': True,
            'created': False,
            'id': user.id,
            'email': email,
            'name': user.name,
            'role': user.role,
            'temporaryPassword': None,
        }
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to create Open WebUI account.',
        )

    await Users.update_user_by_id(user.id, {'bio': bio})
    await apply_default_group_assignment(
        request.app.state.config.DEFAULT_GROUP_ID,
        user.id,
    )

    return {
        'ok': True,
        'created': True,
        'id': user.id,
        'email': user.email,
        'name': display_name,
        'role': 'user',
        'temporaryPassword': temporary_password,
    }
