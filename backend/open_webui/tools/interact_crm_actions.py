from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import uuid4

import aiohttp
from fastapi import Request


def _text(value: Any) -> str:
    return str(value or '').strip()


def _metadata_value(metadata: dict[str, Any], *keys: str) -> Any:
    channel = metadata.get('interact_channel') if isinstance(metadata.get('interact_channel'), dict) else {}
    for key in keys:
        if metadata.get(key) is not None:
            return metadata.get(key)
        if channel.get(key) is not None:
            return channel.get(key)
    return None


def _service_config() -> tuple[str, str]:
    base_url = (
        os.environ.get('INTERACT_BILLING_BASE_URL')
        or os.environ.get('OPEN_WEBUI_BILLING_BASE_URL')
        or 'https://interact-vision.com.tw'
    ).strip().rstrip('/')
    token = (
        os.environ.get('INTERACT_CHANNEL_SERVICE_TOKEN')
        or os.environ.get('INTERACT_BILLING_SERVICE_TOKEN')
        or os.environ.get('OPEN_WEBUI_BILLING_SERVICE_TOKEN')
        or ''
    ).strip()
    if not token:
        raise RuntimeError('InteractCloudService action service is not configured.')
    return base_url, token


def _embedded_action_requires_original_form(action: str) -> bool:
    return action in {
        'am.follow_up.create',
        'am.follow_up.update',
    }


async def _execute(
    action: str,
    payload: dict[str, Any],
    expected_role: str,
    __request__: Request,
    __user__: dict | None,
    __metadata__: dict | None,
) -> str:
    metadata = __metadata__ or {}
    user = __user__ or {}
    channel_id = _text(_metadata_value(metadata, 'channelId', 'channel_id'))
    model_id = _text(_metadata_value(metadata, 'modelId', 'model_id', 'model'))
    company_user_id = (
        _text(user.get('companyUserId'))
        or _text(user.get('company_user_id'))
        or _text(_metadata_value(metadata, 'companyUserId', 'company_user_id'))
    )
    source = _text(_metadata_value(metadata, 'source', 'channelSource', 'channel_source')).lower()
    if (
        source not in {'channel', 'crm_embedded'}
        or (source == 'channel' and not channel_id)
        or not model_id
        or not company_user_id
    ):
        return json.dumps({
            'ok': False,
            'error': 'This CRM action requires a verified enterprise Channel or CRM product session.',
        }, ensure_ascii=False)
    if source == 'crm_embedded' and _embedded_action_requires_original_form(action):
        return json.dumps({
            'ok': False,
            'error': (
                'Embedded CRM assistants must prepare a review draft. '
                'The employee must save it with the original CRM form.'
            ),
        }, ensure_ascii=False)
    external_user_id = _text(_metadata_value(metadata, 'externalUserId', 'external_user_id'))
    external_ref = (
        hashlib.sha256(f'{channel_id}:{external_user_id}'.encode()).hexdigest()[-16:]
        if external_user_id
        else None
    )
    base_url, service_token = _service_config()
    body = {
        'companyUserId': company_user_id,
        'channelId': channel_id or None,
        'modelId': model_id,
        'action': action,
        'requestId': str(uuid4()),
        'requester': {
            'email': _text(_metadata_value(metadata, 'companyMemberEmail', 'memberEmail')) or None,
            'memberRole': _text(_metadata_value(metadata, 'companyMemberRole', 'memberRole')) or None,
            'memberId': _text(_metadata_value(metadata, 'companyMemberId', 'memberId')) or None,
            'externalUserRef': external_ref,
            'identitySource': _text(_metadata_value(metadata, 'identitySubject', 'identitySource')) or None,
            'productKey': _text(_metadata_value(metadata, 'productKey')) or None,
            'productInstanceId': _text(_metadata_value(metadata, 'productInstanceId')) or None,
            'productUserId': _text(_metadata_value(metadata, 'productUserId')) or None,
            'productTeamCodes': [
                str(item)
                for item in (_metadata_value(metadata, 'productTeamCodes') or [])
                if str(item).strip()
            ],
        },
        'payload': payload,
    }
    timeout = aiohttp.ClientTimeout(total=45)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f'{base_url}/api/integrations/agent-actions',
                json=body,
                headers={
                    'X-Interact-Service-Token': service_token,
                    'Accept': 'application/json',
                },
            ) as response:
                text = await response.text()
                try:
                    result = json.loads(text)
                except json.JSONDecodeError:
                    result = {'ok': False, 'error': f'CRM action service returned HTTP {response.status}.'}
                if response.status >= 400 and not result.get('error'):
                    result['error'] = f'CRM action service returned HTTP {response.status}.'
                result.setdefault('productRole', expected_role)
                return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as error:
        return json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False)


async def interact_crm_follow_up_create(
    company_id: int,
    follow_up_type: str,
    subject: str,
    content: str,
    outcome: str,
    follow_up_at: str,
    next_action: str | None = None,
    contact_id: int | None = None,
    opportunity_id: int | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Create one CRM follow-up after the user clearly asks to record it. Use only after reading
    the authorized company/contact context and restating the company, activity type, summary,
    outcome, time, and next action. This API writes through CRM validation and audit logs; it
    never exposes database write access. Do not call for drafts, guesses, or implied consent.

    :param company_id: Authorized CRM company ID.
    :param follow_up_type: One of call, email, line, meeting, demo, proposal, support.
    :param subject: Short follow-up subject.
    :param content: Factual summary of what the customer said or what happened.
    :param outcome: Confirmed outcome; do not invent one.
    :param follow_up_at: ISO 8601 timestamp with timezone.
    :param next_action: Optional confirmed next action.
    :param contact_id: Optional contact ID belonging to the company.
    :param opportunity_id: Optional opportunity ID belonging to the company.
    """
    return await _execute('am.follow_up.create', {
        'companyId': company_id, 'contactId': contact_id, 'opportunityId': opportunity_id,
        'type': follow_up_type, 'subject': subject, 'content': content, 'outcome': outcome,
        'nextAction': next_action, 'followUpAt': follow_up_at,
    }, 'am', __request__, __user__, __metadata__)


async def interact_crm_follow_up_update(
    follow_up_id: int,
    company_id: int,
    expected_version: str,
    follow_up_type: str,
    subject: str,
    content: str,
    outcome: str,
    follow_up_at: str,
    next_action: str | None = None,
    contact_id: int | None = None,
    opportunity_id: int | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Update one existing CRM follow-up only after the user explicitly identifies the record and
    confirms the replacement values. First read crm_app.ai_follow_ups and pass record_version as
    expected_version. A concurrent human edit is rejected instead of overwritten.

    :param follow_up_id: Existing follow-up ID.
    :param company_id: Company ID that owns the follow-up.
    :param expected_version: Current record_version read from crm_app.ai_follow_ups.
    :param follow_up_type: One of call, email, line, meeting, demo, proposal, support.
    :param subject: Replacement subject.
    :param content: Replacement factual summary.
    :param outcome: Replacement confirmed outcome.
    :param follow_up_at: ISO 8601 timestamp with timezone.
    """
    return await _execute('am.follow_up.update', {
        'followUpId': follow_up_id, 'companyId': company_id,
        'expectedVersion': expected_version, 'contactId': contact_id,
        'opportunityId': opportunity_id, 'type': follow_up_type, 'subject': subject,
        'content': content, 'outcome': outcome, 'nextAction': next_action,
        'followUpAt': follow_up_at,
    }, 'am', __request__, __user__, __metadata__)


async def interact_crm_bd_discovery_start(
    target_segment_id: int | None = None,
    requested_count: int | None = None,
    max_rounds: int | None = None,
    region: str | None = None,
    search_notes: str | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Queue a real CRM public-web prospect discovery run immediately. When the user does not
    provide overrides, omit every argument: CRM selects the next eligible active target segment
    from run history and uses its safe defaults. The worker searches public sources, verifies
    evidence, deduplicates candidates, applies exclusions, and leaves results for human review.
    Never ask the user to choose a segment, region, count, rounds, or exclusions that CRM can
    derive. Ask only when the user explicitly requests a one-off override.

    :param target_segment_id: Optional active CRM target segment ID override.
    :param requested_count: Optional net-new candidate goal override, 5 to 30.
    :param max_rounds: Optional search-round override, 1 to 5.
    :param region: Optional geographic search-area override.
    :param search_notes: Optional non-sensitive focus for this run.
    """
    payload: dict[str, Any] = {}
    if target_segment_id is not None:
        payload['targetSegmentId'] = target_segment_id
    if requested_count is not None:
        payload['requestedCount'] = requested_count
    if max_rounds is not None:
        payload['maxRounds'] = max_rounds
    if region:
        payload['region'] = region
    if search_notes:
        payload['searchNotes'] = search_notes
    return await _execute(
        'bd.discovery.start', payload, 'bd', __request__, __user__, __metadata__
    )


async def interact_crm_bd_candidates_list(
    status: str = 'pending',
    limit: int = 20,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    List CRM prospect candidates that the verified BD employee may review. Call this whenever
    the user asks for pending, approved, contacted, converted, or all prospect candidates.
    Results include score, public evidence URLs, contact verification status, public contact
    details when permitted, and uncertainty notes. Never claim that candidate-list access is
    unavailable before calling this tool.

    :param status: One of pending, qualified, contacted, converted, or all.
    :param limit: Maximum rows to return, 1 to 50.
    """
    return await _execute('bd.candidates.list', {
        'status': status, 'limit': limit,
    }, 'bd', __request__, __user__, __metadata__)


async def interact_crm_bd_profile_suggestion_create(
    target_segment_id: int,
    candidate_ids: list[int],
    suggestions: list[dict[str, Any]],
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Propose evidence-backed changes to a BD target profile from human-approved candidates.
    The API verifies candidate approval and evidence URLs, then creates a pending review item.
    It never applies profile changes itself. A CRM manager must approve before future searches
    use the suggestion.

    :param target_segment_id: Active target segment to improve.
    :param candidate_ids: CRM candidate IDs already marked qualified or converted by a human.
    :param suggestions: Items with action=add, field, term, reason, confidence, evidenceUrls.
    """
    return await _execute('bd.profile_suggestion.create', {
        'targetSegmentId': target_segment_id, 'candidateIds': candidate_ids,
        'suggestions': suggestions,
    }, 'bd', __request__, __user__, __metadata__)
