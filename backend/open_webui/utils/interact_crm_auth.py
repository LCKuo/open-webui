from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
from fastapi import HTTPException, status
from open_webui.utils.auth import ALGORITHM, SESSION_SECRET, create_token

CRM_TOKEN_TYPE = 'interact_crm_access'
CRM_TOKEN_AUDIENCE = 'interact-webui'
CRM_TOKEN_ISSUER = 'interact-webui'
CRM_ALLOWED_SCOPES = {
    'workflow:list',
    'workflow:select',
    'workflow:preflight',
    'workflow:run',
    'workflow:resume',
    'channel:list',
    'channel:manage',
    'agent:list',
    'agent:select',
}


def issue_crm_access_token(
    *,
    company_user_id: str,
    company_email: str,
    webui_user_id: str,
    crm_integration_id: str,
    crm_instance_id: str,
    crm_instance_name: str,
    crm_user_id: str | None,
    scopes: list[str],
    allowed_workflow_ids: list[str],
    ttl_seconds: int,
) -> tuple[str, dict[str, Any]]:
    normalized_scopes = sorted(set(scopes) & CRM_ALLOWED_SCOPES)
    if not normalized_scopes:
        raise HTTPException(status_code=403, detail='No permitted CRM Agent scopes were requested.')

    token = create_token(
        {
            'iss': CRM_TOKEN_ISSUER,
            'aud': CRM_TOKEN_AUDIENCE,
            'sub': f'crm:{crm_instance_id}',
            'token_type': CRM_TOKEN_TYPE,
            'company_user_id': company_user_id,
            'company_email': company_email.strip().lower(),
            'webui_user_id': webui_user_id,
            'crm_integration_id': crm_integration_id,
            'crm_instance_id': crm_instance_id,
            'crm_instance_name': crm_instance_name,
            'crm_user_id': crm_user_id,
            'scopes': normalized_scopes,
            'allowed_workflow_ids': sorted(set(allowed_workflow_ids)),
        },
        expires_delta=dt.timedelta(seconds=ttl_seconds),
    )
    return token, decode_crm_access_token(token)


def decode_crm_access_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            SESSION_SECRET,
            algorithms=[ALGORITHM],
            audience=CRM_TOKEN_AUDIENCE,
            issuer=CRM_TOKEN_ISSUER,
            options={
                'require': [
                    'exp',
                    'iat',
                    'jti',
                    'iss',
                    'aud',
                    'sub',
                    'token_type',
                    'company_user_id',
                    'company_email',
                    'webui_user_id',
                    'crm_integration_id',
                    'crm_instance_id',
                    'scopes',
                ]
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='CRM Agent token is invalid or expired.',
        ) from exc

    if claims.get('token_type') != CRM_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='CRM Agent token type is invalid.',
        )
    scopes = claims.get('scopes')
    if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='CRM Agent token scopes are invalid.',
        )
    allowed_workflow_ids = claims.get('allowed_workflow_ids', [])
    if not isinstance(allowed_workflow_ids, list) or any(
        not isinstance(workflow_id, str) for workflow_id in allowed_workflow_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='CRM Agent token workflow grants are invalid.',
        )
    return claims


def require_crm_scope(claims: dict[str, Any], required_scope: str) -> None:
    if required_scope not in set(claims.get('scopes') or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f'CRM Agent token is missing the {required_scope} scope.',
        )


def assert_crm_company_context(
    claims: dict[str, Any],
    *,
    company_email: str,
    company_user_id: str | None,
    webui_user_id: str,
) -> None:
    if (
        claims.get('company_email') != company_email.strip().lower()
        or claims.get('company_user_id') != str(company_user_id or '')
        or claims.get('webui_user_id') != webui_user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='CRM Agent token does not match the requested company.',
        )


def workflow_allowed_by_crm_token(claims: dict[str, Any], workflow_id: str) -> bool:
    allowed = claims.get('allowed_workflow_ids') or []
    return not allowed or workflow_id in allowed
