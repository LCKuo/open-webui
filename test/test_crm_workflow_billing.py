import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-only-webui-secret-key-at-least-32-characters')

from open_webui.routers import workflows


def _arguments():
    return {
        'service_user': SimpleNamespace(id='webui-user-1', email='owner@example.com'),
        'workflow': SimpleNamespace(id='workflow-1', name='Prospecting'),
        'run': SimpleNamespace(id='run-1'),
        'run_form': SimpleNamespace(
            input={'message': 'Find prospects'},
            model_id='model-1',
            workflow_version_id='version-1',
            trigger_type='crm_agent',
        ),
        'crm_claims': {
            'company_user_id': 'company-1',
            'crm_instance_id': 'instance-1',
            'jti': 'token-jti-1',
        },
    }


@pytest.mark.asyncio
async def test_crm_workflow_execution_requires_billing(monkeypatch):
    monkeypatch.setattr(workflows, 'is_billing_enabled', lambda: False)

    with pytest.raises(HTTPException) as error:
        await workflows._authorize_crm_workflow_billing(**_arguments())

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_crm_billing_is_bound_to_signed_company(monkeypatch):
    captured = {}

    class FakeBillingClient:
        async def authorize(self, user, form_data, metadata):
            captured['user'] = user
            captured['form_data'] = form_data
            captured['metadata'] = metadata
            return SimpleNamespace(company_user_id='company-1')

        async def cancel(self, authorization, reason):
            raise AssertionError(f'unexpected cancellation: {reason}')

    async def fake_billing_form_data(workflow_request, form_data):
        captured['workflow_request'] = workflow_request
        return {**form_data, '_billing_multiplier': 1}

    monkeypatch.setattr(workflows, 'is_billing_enabled', lambda: True)
    monkeypatch.setattr(workflows, 'InteractBillingClient', FakeBillingClient)
    monkeypatch.setattr(workflows, 'workflow_billing_form_data', fake_billing_form_data)

    _, authorization, _, metadata = await workflows._authorize_crm_workflow_billing(
        **_arguments()
    )

    assert authorization.company_user_id == 'company-1'
    assert captured['workflow_request']['id'] == 'workflow-1'
    assert metadata['chat_id'] == 'crm:instance-1'
    assert metadata['message_id'].startswith('token-jti-1:')


@pytest.mark.asyncio
async def test_crm_billing_company_mismatch_cancels_reservation(monkeypatch):
    cancelled = {}

    class FakeBillingClient:
        async def authorize(self, user, form_data, metadata):
            return SimpleNamespace(company_user_id='company-2')

        async def cancel(self, authorization, reason):
            cancelled['reason'] = reason

    async def fake_billing_form_data(workflow_request, form_data):
        return form_data

    monkeypatch.setattr(workflows, 'is_billing_enabled', lambda: True)
    monkeypatch.setattr(workflows, 'InteractBillingClient', FakeBillingClient)
    monkeypatch.setattr(workflows, 'workflow_billing_form_data', fake_billing_form_data)

    with pytest.raises(HTTPException) as error:
        await workflows._authorize_crm_workflow_billing(**_arguments())

    assert error.value.status_code == 403
    assert cancelled['reason'] == 'crm-company-context-mismatch'


@pytest.mark.asyncio
async def test_failed_crm_workflow_commits_usage_already_consumed():
    captured = {}

    class FakeBillingClient:
        async def commit(self, *args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs

        async def cancel(self, authorization, reason):
            raise AssertionError(f'unexpected cancellation: {reason}')

    error = RuntimeError('contract validation failed')
    error.model_calls = 2
    error.execution_usage = {
        'prompt_tokens': 20,
        'completion_tokens': 4,
        'total_tokens': 24,
    }

    await workflows._settle_failed_workflow_billing(
        FakeBillingClient(),
        SimpleNamespace(reservation_id='reservation-1'),
        SimpleNamespace(id='webui-user-1'),
        {'model': 'model-1', 'messages': []},
        {'message_id': 'message-1'},
        error,
        'before-model-use',
    )

    assert captured['args'][4] == error.execution_usage
    assert captured['kwargs']['status_value'] == 'failed'


@pytest.mark.asyncio
async def test_failed_crm_workflow_without_model_use_cancels_reservation():
    captured = {}

    class FakeBillingClient:
        async def commit(self, *args, **kwargs):
            raise AssertionError('commit should not run')

        async def cancel(self, authorization, reason):
            captured['reason'] = reason

    await workflows._settle_failed_workflow_billing(
        FakeBillingClient(),
        SimpleNamespace(reservation_id='reservation-1'),
        SimpleNamespace(id='webui-user-1'),
        {'model': 'model-1', 'messages': []},
        {'message_id': 'message-1'},
        RuntimeError('validation failed'),
        'before-model-use',
    )

    assert captured['reason'] == 'before-model-use'
