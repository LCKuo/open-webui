from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.utils import interact_model_entitlements as entitlements
from open_webui.utils.interact_billing import BillingIdentity


class FakeBillingClient:
    async def resolve_identity(self, user):
        return BillingIdentity(
            company_user={'id': 'company-1', 'activeModelLimit': 1},
            model_entitlement={
                'activeModelLimit': 1,
                'ownerUserIds': ['owner-1', 'member-1'],
            },
        )


@pytest.mark.asyncio
async def test_model_activation_limit_counts_the_whole_company(monkeypatch):
    captured = {}

    async def count_models(user_ids, db=None):
        captured['user_ids'] = user_ids
        return 1

    monkeypatch.setattr(entitlements, 'is_billing_enabled', lambda: True)
    monkeypatch.setattr(entitlements, 'InteractBillingClient', FakeBillingClient)
    monkeypatch.setattr(
        entitlements.Models,
        'count_active_workspace_models_by_user_ids',
        count_models,
    )

    with pytest.raises(HTTPException) as error:
        async with entitlements.enforce_model_activation_limit(
            user=SimpleNamespace(id='member-1', email='member@example.com', role='user'),
            additions=1,
        ):
            pass

    assert error.value.status_code == 409
    assert '1/1' in error.value.detail
    assert captured['user_ids'] == ['owner-1', 'member-1']


@pytest.mark.asyncio
async def test_inactive_model_mutation_does_not_require_entitlement_service(monkeypatch):
    monkeypatch.setattr(entitlements, 'is_billing_enabled', lambda: False)

    async with entitlements.enforce_model_activation_limit(
        user=SimpleNamespace(id='user-1', role='user'),
        additions=0,
    ) as capacity:
        assert capacity is None


@pytest.mark.asyncio
async def test_webui_admin_is_exempt_from_model_limit(monkeypatch):
    monkeypatch.setattr(entitlements, 'is_billing_enabled', lambda: False)

    async with entitlements.enforce_model_activation_limit(
        user=SimpleNamespace(id='admin-1', role='admin'),
        additions=100,
    ) as capacity:
        assert capacity is None
