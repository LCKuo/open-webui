import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import time
from email.utils import parseaddr
from typing import Any, Optional

import aiohttp
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from open_webui.models.interact_email import (
    EmailConnectorModel,
    EmailConnectorUpsertForm,
    EmailDailyLimitExceeded,
    EmailDeliveryModel,
    InteractEmail,
    InteractEmailConnector,
    _encrypt,
)
from open_webui.models.workflows import Workflows
from open_webui.utils.auth import get_verified_user
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from open_webui.utils.workflows import workflow_acl
from pydantic import BaseModel, Field, field_validator

router = APIRouter()
RESEND_API_BASE = 'https://api.resend.com'
EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
EMAIL_IN_TEXT_PATTERN = re.compile(r'(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])')
POLICY_VALUE_SEPARATOR = re.compile(r'[\r\n,\uFF0C\u3001;\uFF1B]+')
DELIVERY_EVENT_STATUS = {
    'email.sent': 'sent',
    'email.delivered': 'delivered',
    'email.delivery_delayed': 'delayed',
    'email.bounced': 'bounced',
    'email.failed': 'failed',
    'email.complained': 'complained',
    'email.opened': 'opened',
    'email.clicked': 'clicked',
}


class EmailConnectorTestForm(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=320)


class ServiceEmailConnectorUpsertForm(EmailConnectorUpsertForm):
    companyUserId: str = Field(..., min_length=1, max_length=200)
    companyEmail: str = Field(..., min_length=3, max_length=320)
    actorId: Optional[str] = None
    controlPlaneRevision: int = Field(default=1, ge=1)


class EmailSendRequest(BaseModel):
    connector_id: str
    to: list[str] = Field(..., min_length=1, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(..., min_length=1, max_length=998)
    text: Optional[str] = Field(default=None, max_length=2_000_000)
    html: Optional[str] = Field(default=None, max_length=2_000_000)
    reply_to: Optional[str] = Field(default=None, max_length=320)
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    channel_id: Optional[str] = None
    idempotency_key: str = Field(..., min_length=8, max_length=256)
    payload_hash: str = Field(..., min_length=32, max_length=128)

    @field_validator('subject')
    @classmethod
    def validate_subject(cls, value: str) -> str:
        clean = value.strip()
        if '\r' in clean or '\n' in clean:
            raise ValueError('Email subject cannot contain line breaks.')
        return clean

    @field_validator('reply_to')
    @classmethod
    def validate_reply_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower()
        if '\r' in clean or '\n' in clean:
            raise ValueError('Reply-To cannot contain line breaks.')
        return clean or None


def _service_token() -> str:
    return (
        os.environ.get('INTERACT_CHANNEL_SERVICE_TOKEN')
        or os.environ.get('INTERACT_BILLING_SERVICE_TOKEN')
        or os.environ.get('OPEN_WEBUI_BILLING_SERVICE_TOKEN')
        or ''
    ).strip()


def _require_service_token(authorization: str | None, x_interact_service_token: str | None) -> None:
    expected = _service_token()
    supplied = (x_interact_service_token or '').strip()
    if not supplied and authorization:
        scheme, _, value = authorization.partition(' ')
        supplied = value.strip() if scheme.lower() == 'bearer' else ''
    if not expected:
        raise HTTPException(status_code=503, detail='Interact service token is not configured.')
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail='Invalid service token.')


async def _company_context(user) -> dict[str, Any]:
    if not is_billing_enabled():
        raise HTTPException(status_code=503, detail='Interact company identity is not configured.')
    identity = await InteractBillingClient().resolve_identity(user)
    company_user = identity.company_user or {}
    company_member = identity.company_member or {}
    company_user_id = str(company_user.get('id') or '').strip()
    if not company_user_id:
        raise HTTPException(status_code=403, detail='Unable to resolve company account.')
    return {
        'company_user_id': company_user_id,
        'company_email': company_user.get('email') or user.email,
        'company_member_id': company_member.get('id'),
        'company_member_role': company_member.get('role') or 'owner',
        'group_ids': company_member.get('groupIds') or company_member.get('group_ids') or [],
    }


def _require_company_admin(context: dict[str, Any]) -> None:
    if str(context.get('company_member_role') or '').lower() not in {'owner', 'admin'}:
        raise HTTPException(status_code=403, detail='Only company owners and admins can manage email connectors.')


async def _validate_allowed_workflows(company_user_id: str, workflow_ids: list[str]) -> None:
    for workflow_id in workflow_ids:
        workflow = await Workflows.get_by_id(workflow_id)
        acl = workflow_acl(workflow) if workflow else {}
        if (
            not workflow
            or workflow.status != 'published'
            or str(acl.get('company_user_id') or '').strip() != company_user_id
        ):
            raise HTTPException(
                status_code=400,
                detail=f'Allowed workflow is unpublished, missing, or belongs to another company: {workflow_id}',
            )


def _domain(address: str) -> str:
    return address.rsplit('@', 1)[-1].lower()


def _clean_addresses(values: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        _, parsed = parseaddr(str(value or '').strip())
        address = parsed.lower()
        if not EMAIL_PATTERN.fullmatch(address):
            raise HTTPException(status_code=400, detail=f'Invalid email address: {value}')
        if address not in cleaned:
            cleaned.append(address)
    return cleaned


def _clean_policy_values(values: Any) -> set[str]:
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    cleaned: set[str] = set()
    for value in raw_values:
        for item in POLICY_VALUE_SEPARATOR.split(str(value or '')):
            normalized = item.strip().lower()
            if normalized:
                cleaned.add(normalized)
    return cleaned


def _safe_provider_message(value: Any) -> str:
    return EMAIL_IN_TEXT_PATTERN.sub('[email redacted]', str(value or ''))[:1000]


def _resend_error_response(status: int, code: Any, message: Any) -> tuple[int, str]:
    safe_message = _safe_provider_message(message)
    normalized = safe_message.lower()
    domain_match = re.search(
        r'\bthe\s+([a-z0-9.-]+)\s+domain\s+is\s+not\s+verified\b',
        safe_message,
        re.IGNORECASE,
    )
    if domain_match:
        domain = domain_match.group(1).lower()
        return (
            422,
            f'寄件網域 {domain} 尚未在 Resend 驗證。請到 Resend Domains 新增網域、完成 DNS 驗證後再測試。',
        )
    normalized_code = str(code or '').strip().lower()
    if status in {401, 403} and (
        'api key' in normalized or normalized_code in {'invalid_api_key', 'restricted_api_key'}
    ):
        return 422, 'Resend API Key 無效或權限不足。請確認 Key 仍有效，且具有寄信權限。'
    if status == 429:
        return 429, 'Resend 暫時限制寄送頻率，請稍後再試。'
    if 400 <= status < 500:
        return 422, f'Resend 拒絕寄送：{safe_message or f"HTTP {status}"}'
    return 503, 'Resend 服務目前暫時無法完成寄送，請稍後再試。'


def ensure_email_connector_allowed(
    connector: InteractEmailConnector,
    context: dict[str, Any],
    workflow_id: str | None,
    channel_id: str | None,
    allow_disabled: bool = False,
) -> None:
    if connector.company_user_id != context.get('company_user_id'):
        raise HTTPException(status_code=403, detail='Email connector belongs to another company.')
    if getattr(connector, 'control_plane_status', 'active') != 'active':
        raise HTTPException(
            status_code=409,
            detail='Email connector is quarantined pending company portal reconciliation.',
        )
    if not allow_disabled and (not connector.enabled or connector.status not in {'ready', 'error'}):
        raise HTTPException(status_code=409, detail='Email connector is disabled or incomplete.')
    if not allow_disabled and connector.allowed_workflow_ids and workflow_id not in connector.allowed_workflow_ids:
        raise HTTPException(status_code=403, detail='This workflow is not allowed to use the email connector.')
    if not allow_disabled and connector.allowed_channel_ids and channel_id not in connector.allowed_channel_ids:
        raise HTTPException(status_code=403, detail='This channel is not allowed to use the email connector.')
    if context.get('service_principal'):
        return
    role = str(context.get('company_member_role') or '').lower()
    member_id = context.get('company_member_id')
    groups = set(context.get('group_ids') or [])
    if allow_disabled and role in {'owner', 'admin'}:
        return
    if connector.access_mode == 'company_admins' and role not in {'owner', 'admin'}:
        raise HTTPException(status_code=403, detail='Only company admins can use this email connector.')
    if connector.access_mode == 'selected_members' and member_id not in connector.allowed_member_ids:
        raise HTTPException(status_code=403, detail='Your company member is not allowed to use this email connector.')
    if connector.access_mode == 'selected_groups' and not groups.intersection(connector.allowed_group_ids or []):
        raise HTTPException(status_code=403, detail='Your company group is not allowed to use this email connector.')


def _apply_recipient_policy(
    connector: InteractEmailConnector, to: list[str], cc: list[str]
) -> tuple[list[str], list[str]]:
    recipient_policy = connector.recipient_policy or {}
    cc_policy = connector.cc_policy or {}
    blocked_domains = _clean_policy_values(recipient_policy.get('blocked_domains') or [])
    allowed_domains = _clean_policy_values(recipient_policy.get('allowed_domains') or [])
    for address in to:
        domain = _domain(address)
        if domain in blocked_domains or (allowed_domains and domain not in allowed_domains):
            raise HTTPException(status_code=403, detail=f'Recipient domain is not allowed: {domain}')
    if cc and not cc_policy.get('allow_runtime_cc', True):
        raise HTTPException(status_code=403, detail='Runtime CC recipients are disabled by company policy.')
    default_cc = _clean_addresses(cc_policy.get('default_cc') or [])
    cc = _clean_addresses([*cc, *default_cc])
    allowed_cc_domains = _clean_policy_values(cc_policy.get('allowed_domains') or [])
    allowed_cc_addresses = _clean_policy_values(cc_policy.get('allowed_addresses') or [])
    for address in cc:
        if allowed_cc_domains or allowed_cc_addresses:
            if address not in allowed_cc_addresses and _domain(address) not in allowed_cc_domains:
                raise HTTPException(status_code=403, detail=f'CC recipient is not allowed: {address}')
    max_cc = max(0, min(int(cc_policy.get('max_cc') or 10), connector.max_recipients_per_send))
    if len(cc) > max_cc:
        raise HTTPException(status_code=400, detail=f'CC recipient count exceeds the limit of {max_cc}.')
    if len(to) + len(cc) > connector.max_recipients_per_send:
        raise HTTPException(status_code=400, detail='Recipient count exceeds the connector limit.')
    return to, cc


async def send_resend_email(
    connector: InteractEmailConnector,
    context: dict[str, Any],
    payload: EmailSendRequest,
    allow_disabled: bool = False,
) -> EmailDeliveryModel:
    ensure_email_connector_allowed(
        connector,
        context,
        payload.workflow_id,
        payload.channel_id,
        allow_disabled=allow_disabled,
    )
    api_key = InteractEmail.decrypt_connector_api_key(connector)
    if not api_key:
        raise HTTPException(status_code=409, detail='Email connector API key is missing.')
    to = _clean_addresses(payload.to)
    cc = _clean_addresses(payload.cc)
    to, cc = _apply_recipient_policy(connector, to, cc)
    requested_reply_to = payload.reply_to
    if not requested_reply_to and context.get('service_principal'):
        requested_reply_to = connector.company_email
    requested_reply_to = requested_reply_to or connector.reply_to
    reply_to_values = _clean_addresses([requested_reply_to]) if requested_reply_to else []
    effective_reply_to = reply_to_values[0] if reply_to_values else None
    if not payload.text and not payload.html:
        raise HTTPException(status_code=400, detail='Email requires text or HTML content.')
    existing_delivery = await InteractEmail.get_delivery_by_idempotency_key(payload.idempotency_key)
    if existing_delivery:
        if existing_delivery.payload_hash != payload.payload_hash:
            raise HTTPException(status_code=409, detail='Idempotency key was already used for different email content.')
        if existing_delivery.company_user_id != connector.company_user_id:
            raise HTTPException(status_code=409, detail='Idempotency key conflicts with another company request.')
        if existing_delivery.status in {'sent', 'delivered', 'opened', 'clicked'}:
            return existing_delivery
        delivery = await InteractEmail.update_delivery(
            existing_delivery.id,
            status='sending',
            error_code=None,
            error_message=None,
        )
    else:
        daily_limit = connector.daily_send_limit
        if is_billing_enabled():
            entitlements = await InteractBillingClient().semantic_entitlements(connector.company_user_id)
            if not entitlements.get('operational'):
                raise HTTPException(status_code=403, detail='Company email entitlement is not operational.')
            plan_limit = int((entitlements.get('limits') or {}).get('dailyEmails') or 0)
            daily_limit = min(daily_limit, plan_limit)
        if daily_limit <= 0:
            raise HTTPException(status_code=429, detail='Email connector daily send limit has been reached.')

    body = {
        'from': f'{connector.from_name} <{connector.from_address}>' if connector.from_name else connector.from_address,
        'to': to,
        'cc': cc or None,
        'subject': payload.subject,
        'text': payload.text,
        'html': payload.html,
        'reply_to': effective_reply_to,
    }
    body = {key: value for key, value in body.items() if value not in (None, [], '')}
    if not existing_delivery:
        try:
            delivery, created = await InteractEmail.create_delivery(
                daily_limit=daily_limit,
                company_user_id=connector.company_user_id,
                connector_id=connector.id,
                workflow_id=payload.workflow_id,
                workflow_run_id=payload.workflow_run_id,
                requested_by=str(context.get('user_id') or context.get('company_member_id') or connector.company_user_id),
                channel_id=payload.channel_id,
                from_address=connector.from_address,
                reply_to=effective_reply_to,
                idempotency_key=payload.idempotency_key,
                status='sending',
                recipient_count=len(to) + len(cc),
                recipient_domains=sorted({_domain(address) for address in [*to, *cc]}),
                to_encrypted=_encrypt(json.dumps(to, ensure_ascii=False)),
                cc_encrypted=_encrypt(json.dumps(cc, ensure_ascii=False)) if cc else None,
                subject_encrypted=_encrypt(payload.subject),
                content_encrypted=_encrypt(json.dumps({'text': payload.text, 'html': payload.html}, ensure_ascii=False)),
                payload_hash=payload.payload_hash,
            )
        except EmailDailyLimitExceeded as exc:
            raise HTTPException(status_code=429, detail='Email connector daily send limit has been reached.') from exc
        if not created:
            if delivery.payload_hash != payload.payload_hash or delivery.company_user_id != connector.company_user_id:
                raise HTTPException(status_code=409, detail='Idempotency key was already used for a different request.')
            if delivery.status in {'sent', 'delivered', 'opened', 'clicked'}:
                return delivery
            delivery = await InteractEmail.update_delivery(
                delivery.id,
                status='sending',
                error_code=None,
                error_message=None,
            )
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(
                f'{RESEND_API_BASE}/emails',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'Idempotency-Key': payload.idempotency_key,
                },
                json=body,
            ) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {}
                if response.status < 200 or response.status >= 300:
                    message = _safe_provider_message(data.get('message') or raw or f'Resend HTTP {response.status}')
                    client_status, client_message = _resend_error_response(
                        response.status,
                        data.get('name'),
                        message,
                    )
                    await InteractEmail.update_delivery(
                        delivery.id,
                        status='failed',
                        error_code=str(data.get('name') or f'HTTP-{response.status}'),
                        error_message=message,
                    )
                    raise HTTPException(status_code=client_status, detail=client_message)
                provider_message_id = str(data.get('id') or '').strip()
                if not provider_message_id:
                    await InteractEmail.update_delivery(
                        delivery.id,
                        status='failed',
                        error_code='PROVIDER-MISSING-ID',
                        error_message='Resend accepted the request without returning a message id.',
                    )
                    raise HTTPException(
                        status_code=502,
                        detail='寄信服務未回傳投遞 ID，系統未將這封信標記為已寄出。',
                    )
                return await InteractEmail.update_delivery(
                    delivery.id,
                    status='sent',
                    provider_message_id=provider_message_id,
                    sent_at=int(time.time_ns()),
                    error_code=None,
                    error_message=None,
                )
    except HTTPException:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        await InteractEmail.update_delivery(
            delivery.id,
            status='failed',
            error_code='PROVIDER-UNAVAILABLE',
            error_message=_safe_provider_message(exc),
        )
        raise HTTPException(
            status_code=503,
            detail='Resend 服務目前暫時無法連線，請稍後再試。',
        ) from exc


@router.get('/email-connectors', response_model=list[EmailConnectorModel])
async def list_email_connectors(user=Depends(get_verified_user)):
    context = await _company_context(user)
    connectors = await InteractEmail.list_connectors(
        str(context['company_user_id']),
        include_quarantined=False,
    )
    role = str(context.get('company_member_role') or '').lower()
    if role in {'owner', 'admin'}:
        return connectors
    member_id = context.get('company_member_id')
    groups = set(context.get('group_ids') or [])
    return [
        connector
        for connector in connectors
        if connector.enabled
        and (
            connector.access_mode == 'all_company_members'
            or (connector.access_mode == 'selected_members' and member_id in connector.allowed_member_ids)
            or (connector.access_mode == 'selected_groups' and groups.intersection(connector.allowed_group_ids or []))
        )
    ]


@router.post('/email-connectors', response_model=EmailConnectorModel)
async def save_email_connector(form: EmailConnectorUpsertForm, user=Depends(get_verified_user)):
    context = await _company_context(user)
    _require_company_admin(context)
    existing = await InteractEmail.get_connector(str(form.id or '')) if form.id else None
    if not existing or existing.company_user_id != str(context['company_user_id']):
        raise HTTPException(
            status_code=409, detail='Create the connector governance record on the company portal first.'
        )
    managed_form = EmailConnectorUpsertForm(
        id=existing.id,
        name=existing.name,
        provider='resend',
        enabled=existing.enabled,
        api_key=form.api_key,
        webhook_secret=form.webhook_secret,
        from_name=existing.from_name,
        from_address=existing.from_address,
        reply_to=existing.reply_to,
        verified_domain=existing.verified_domain,
        access_mode=existing.access_mode,
        allowed_member_ids=existing.allowed_member_ids or [],
        allowed_group_ids=existing.allowed_group_ids or [],
        allowed_workflow_ids=existing.allowed_workflow_ids or [],
        allowed_channel_ids=existing.allowed_channel_ids or [],
        cc_policy=existing.cc_policy or {},
        recipient_policy=existing.recipient_policy or {},
        daily_send_limit=existing.daily_send_limit,
        max_recipients_per_send=existing.max_recipients_per_send,
    )
    return await InteractEmail.upsert_connector(
        str(context['company_user_id']),
        str(context['company_email']),
        user.id,
        managed_form,
    )


@router.delete('/email-connectors/{connector_id}')
async def delete_email_connector(connector_id: str, user=Depends(get_verified_user)):
    context = await _company_context(user)
    _require_company_admin(context)
    connector = await InteractEmail.get_connector(connector_id)
    if not connector or connector.company_user_id != str(context['company_user_id']):
        raise HTTPException(status_code=404, detail='Email connector not found.')
    raise HTTPException(status_code=409, detail='Delete email connectors from the company portal governance page.')


@router.post('/email-connectors/{connector_id}/test')
async def test_email_connector(
    connector_id: str,
    form: EmailConnectorTestForm,
    user=Depends(get_verified_user),
):
    context = await _company_context(user)
    _require_company_admin(context)
    context['user_id'] = user.id
    connector = await InteractEmail.get_connector(connector_id)
    if not connector or connector.company_user_id != context['company_user_id']:
        raise HTTPException(status_code=404, detail='Email connector not found.')
    subject = 'Interact AI 寄信連線測試'
    text = '這封信確認企業寄信 Connector 已可正常寄送。'
    payload_hash = hashlib.sha256(f'{form.recipient}|{subject}|{text}'.encode()).hexdigest()
    try:
        delivery = await send_resend_email(
            connector,
            context,
            EmailSendRequest(
                connector_id=connector.id,
                to=[form.recipient],
                subject=subject,
                text=text,
                idempotency_key=f'test-{connector.id}-{time.time_ns()}',
                payload_hash=payload_hash,
            ),
            allow_disabled=True,
        )
    except HTTPException as exc:
        await InteractEmail.set_connector_health(connector.id, connector.company_user_id, False, str(exc.detail))
        raise
    await InteractEmail.set_connector_health(connector.id, connector.company_user_id, True)
    return {'ok': True, 'delivery': delivery.model_dump()}


@router.get('/email-deliveries', response_model=list[EmailDeliveryModel])
async def list_email_deliveries(
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(get_verified_user),
):
    context = await _company_context(user)
    _require_company_admin(context)
    return await InteractEmail.list_deliveries(str(context['company_user_id']), limit)


@router.get('/admin/email-connectors', response_model=list[EmailConnectorModel])
async def service_list_email_connectors(
    company_user_id: str,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    return await InteractEmail.list_connectors(company_user_id, include_quarantined=True)


@router.get('/admin/email-deliveries', response_model=list[EmailDeliveryModel])
async def service_list_email_deliveries(
    company_user_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    return await InteractEmail.list_deliveries(company_user_id, limit)


@router.post('/admin/email-connectors', response_model=EmailConnectorModel)
async def service_save_email_connector(
    form: ServiceEmailConnectorUpsertForm,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    await _validate_allowed_workflows(form.companyUserId, form.allowed_workflow_ids)
    if is_billing_enabled():
        entitlements = await InteractBillingClient().semantic_entitlements(form.companyUserId)
        limit = int((entitlements.get('limits') or {}).get('emailConnectors') or 0)
        current_items = await InteractEmail.list_connectors(form.companyUserId)
        is_new = not any(item.id == form.id for item in current_items)
        active_items = [item for item in current_items if item.control_plane_status == 'active']
        if not entitlements.get('operational') or limit <= 0 or (is_new and len(active_items) >= limit):
            raise HTTPException(status_code=409, detail=f'Email connector plan limit reached ({limit}).')
        if form.daily_send_limit > int((entitlements.get('limits') or {}).get('dailyEmails') or 0):
            raise HTTPException(status_code=400, detail='Daily send limit exceeds the company plan.')
    connector_form = EmailConnectorUpsertForm.model_validate(
        form.model_dump(
            exclude={
                'companyUserId',
                'companyEmail',
                'actorId',
                'controlPlaneRevision',
            }
        )
    )
    try:
        return await InteractEmail.upsert_connector(
            form.companyUserId,
            str(form.companyEmail),
            form.actorId or form.companyUserId,
            connector_form,
            control_plane_revision=form.controlPlaneRevision,
            managed_by='company_portal',
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/admin/email-connectors/{connector_id}/quarantine', response_model=EmailConnectorModel)
async def service_quarantine_email_connector(
    connector_id: str,
    company_user_id: str,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    connector = await InteractEmail.quarantine_connector(connector_id, company_user_id)
    if not connector:
        raise HTTPException(status_code=404, detail='Email connector not found.')
    return connector


@router.delete('/admin/email-connectors/{connector_id}')
async def service_delete_email_connector(
    connector_id: str,
    company_user_id: str,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
):
    _require_service_token(authorization, x_interact_service_token)
    deleted = await InteractEmail.delete_connector(connector_id, company_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail='Email connector not found.')
    return {'ok': True}


def _verify_svix_signature(secret: str, payload: bytes, message_id: str, timestamp: str, signature: str) -> bool:
    if not timestamp.isdigit() or abs(int(time.time()) - int(timestamp)) > 300:
        return False
    raw_secret = secret[6:] if secret.startswith('whsec_') else secret
    try:
        key = base64.b64decode(raw_secret)
    except Exception:
        key = raw_secret.encode()
    signed = f'{message_id}.{timestamp}.{payload.decode("utf-8")}'.encode()
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(candidate.split(',', 1)[-1], expected) for candidate in signature.split() if candidate
    )


async def _handle_resend_webhook(
    request: Request,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    connector_id: str | None = None,
):
    payload = await request.body()
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail='Invalid webhook JSON.') from exc
    data = event.get('data') if isinstance(event.get('data'), dict) else {}
    provider_message_id = str(data.get('email_id') or data.get('id') or '')
    connector = await InteractEmail.get_connector(connector_id) if connector_id else None
    delivery = None
    if not connector:
        delivery = await InteractEmail.get_delivery_by_provider_message_id(provider_message_id)
        if delivery:
            connector = await InteractEmail.get_connector(delivery.connector_id)
    secret = InteractEmail.decrypt_webhook_secret(connector) if connector else None
    if not secret or not all([svix_id, svix_timestamp, svix_signature]):
        raise HTTPException(status_code=401, detail='Webhook signature is not configured.')
    if not _verify_svix_signature(secret, payload, svix_id, svix_timestamp, svix_signature):
        raise HTTPException(status_code=401, detail='Invalid webhook signature.')
    if not delivery:
        delivery = await InteractEmail.get_delivery_by_provider_message_id(provider_message_id)
    if not delivery:
        raise HTTPException(status_code=503, detail='Email delivery is not ready for webhook processing.')
    if connector_id and delivery.connector_id != connector_id:
        raise HTTPException(status_code=403, detail='Webhook delivery belongs to another connector.')
    event_type = str(event.get('type') or 'unknown')
    occurred_at = int(time.time_ns())
    created_at = event.get('created_at')
    if isinstance(created_at, str):
        try:
            parsed = dt.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            occurred_at = int(parsed.timestamp() * 1_000_000_000)
        except ValueError:
            pass
    inserted = await InteractEmail.insert_event(
        delivery.company_user_id,
        delivery.id,
        svix_id,
        provider_message_id or None,
        event_type,
        occurred_at,
        {
            'type': event_type,
            'created_at': event.get('created_at'),
            'data': {'email_id': provider_message_id},
        },
    )
    if inserted and event_type in DELIVERY_EVENT_STATUS:
        await InteractEmail.update_delivery(delivery.id, status=DELIVERY_EVENT_STATUS[event_type])
    return {'ok': True, 'duplicate': not inserted}


@router.post('/email-webhooks/resend')
async def resend_webhook(
    request: Request,
    svix_id: str | None = Header(default=None),
    svix_timestamp: str | None = Header(default=None),
    svix_signature: str | None = Header(default=None),
):
    return await _handle_resend_webhook(request, svix_id, svix_timestamp, svix_signature)


@router.post('/email-webhooks/resend/{connector_id}')
async def resend_connector_webhook(
    connector_id: str,
    request: Request,
    svix_id: str | None = Header(default=None),
    svix_timestamp: str | None = Header(default=None),
    svix_signature: str | None = Header(default=None),
):
    return await _handle_resend_webhook(
        request,
        svix_id,
        svix_timestamp,
        svix_signature,
        connector_id=connector_id,
    )
