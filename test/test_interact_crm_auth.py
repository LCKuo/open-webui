import os

import jwt
import pytest
from fastapi import HTTPException

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-only-webui-secret-key-at-least-32-characters')

from open_webui.utils.auth import ALGORITHM
from open_webui.utils.interact_crm_auth import (
    CRM_TOKEN_AUDIENCE,
    CRM_TOKEN_ISSUER,
    assert_crm_company_context,
    decode_crm_access_token,
    issue_crm_access_token,
    require_crm_scope,
    workflow_allowed_by_crm_token,
)


def _token(*, allowed_workflow_ids: list[str] | None = None):
    return issue_crm_access_token(
        company_user_id='company-1',
        company_email='owner@example.com',
        webui_user_id='webui-user-1',
        crm_integration_id='integration-1',
        crm_instance_id='instance-1',
        crm_instance_name='Manufacturing CRM',
        crm_user_id='42',
        scopes=['workflow:list', 'workflow:run', 'workflow:resume', 'unsupported'],
        allowed_workflow_ids=allowed_workflow_ids or [],
        ttl_seconds=120,
    )


def test_crm_token_binds_company_scope_and_workflow_grants():
    token, issued_claims = _token(allowed_workflow_ids=['workflow-2', 'workflow-1'])
    claims = decode_crm_access_token(token)

    assert claims['jti'] == issued_claims['jti']
    assert claims['company_user_id'] == 'company-1'
    assert claims['company_email'] == 'owner@example.com'
    assert claims['webui_user_id'] == 'webui-user-1'
    assert set(claims['scopes']) == {'workflow:list', 'workflow:run', 'workflow:resume'}
    assert workflow_allowed_by_crm_token(claims, 'workflow-1')
    assert not workflow_allowed_by_crm_token(claims, 'workflow-3')
    require_crm_scope(claims, 'workflow:run')
    require_crm_scope(claims, 'workflow:resume')


def test_missing_resume_scope_is_rejected():
    token, _ = issue_crm_access_token(
        company_user_id='company-1',
        company_email='owner@example.com',
        webui_user_id='webui-user-1',
        crm_integration_id='integration-1',
        crm_instance_id='instance-1',
        crm_instance_name='Manufacturing CRM',
        crm_user_id='42',
        scopes=['workflow:list', 'workflow:run'],
        allowed_workflow_ids=[],
        ttl_seconds=120,
    )
    claims = decode_crm_access_token(token)

    with pytest.raises(HTTPException) as error:
        require_crm_scope(claims, 'workflow:resume')

    assert error.value.status_code == 403


def test_empty_workflow_grant_means_company_acl_decides():
    token, _ = _token()
    claims = decode_crm_access_token(token)

    assert workflow_allowed_by_crm_token(claims, 'any-company-workflow')


def test_company_context_mismatch_is_rejected():
    token, _ = _token()
    claims = decode_crm_access_token(token)

    with pytest.raises(HTTPException) as error:
        assert_crm_company_context(
            claims,
            company_email='other@example.com',
            company_user_id='company-1',
            webui_user_id='webui-user-1',
        )

    assert error.value.status_code == 403


def test_forged_token_is_rejected():
    forged = jwt.encode(
        {
            'iss': CRM_TOKEN_ISSUER,
            'aud': CRM_TOKEN_AUDIENCE,
            'sub': 'crm:instance-1',
            'token_type': 'interact_crm_access',
            'company_user_id': 'company-1',
            'company_email': 'owner@example.com',
            'webui_user_id': 'webui-user-1',
            'crm_integration_id': 'integration-1',
            'crm_instance_id': 'instance-1',
            'scopes': ['workflow:run'],
        },
        'wrong-secret-that-is-long-enough-for-hs256',
        algorithm=ALGORITHM,
    )

    with pytest.raises(HTTPException) as error:
        decode_crm_access_token(forged)

    assert error.value.status_code == 401
