from open_webui.utils.interact_billing import (
    BillingAuthorization,
    InteractBillingClient,
    _base_url,
    current_user_question,
    estimate_reserved_tokens,
    image_usage_estimate,
    usage_token_counts,
)
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_billing_base_url_uses_environment_override(monkeypatch):
    monkeypatch.setenv("INTERACT_BILLING_BASE_URL", "http://localhost:3000/")
    monkeypatch.delenv("OPEN_WEBUI_BILLING_BASE_URL", raising=False)

    assert _base_url() == "http://localhost:3000"


def test_billing_base_url_supports_legacy_environment_name(monkeypatch):
    monkeypatch.delenv("INTERACT_BILLING_BASE_URL", raising=False)
    monkeypatch.setenv("OPEN_WEBUI_BILLING_BASE_URL", "https://billing.example.com/")

    assert _base_url() == "https://billing.example.com"


def test_internal_context_summary_hides_transcript_from_usage_log():
    assert current_user_question(
        {"messages": [{"role": "user", "content": "full private transcript"}]},
        {"interact_channel": {"operation": "context-summary"}},
    ) == "[Internal channel context summary]"


def test_reserved_tokens_use_weighted_output_credits():
    input_tokens, max_output_tokens, reserved_tokens = estimate_reserved_tokens(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 100,
        }
    )

    assert reserved_tokens == input_tokens + max_output_tokens * 5


def test_openai_reasoning_tokens_are_not_double_counted():
    input_tokens, output_tokens, compute_tokens, billable_tokens = usage_token_counts(
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 500,
            "completion_tokens_details": {"reasoning_tokens": 200},
        },
        fallback_input_tokens=1,
    )

    assert (input_tokens, output_tokens, compute_tokens) == (1_000, 300, 200)
    assert billable_tokens == 1_000 + 300 * 5 + 200 * 5


def test_missing_usage_uses_estimated_input_and_output():
    assert usage_token_counts(
        None,
        fallback_input_tokens=800,
        fallback_output_tokens=120,
    ) == (800, 120, 0, 1_400)


def test_anthropic_cache_tokens_are_added_to_input():
    assert usage_token_counts(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 10,
        },
        fallback_input_tokens=1,
    ) == (600, 10, 0, 650)


def test_image_estimate_uses_weighted_compute_credits():
    usage = image_usage_estimate("prompt", 512, 512, 1)

    assert usage["billable_tokens"] == (
        usage["input_tokens"] + usage["output_tokens"] * 5 + usage["compute_tokens"] * 5
    )


class CaptureBillingClient(InteractBillingClient):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def _request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if path == "/api/integrations/open-webui/users/resolve":
            return {
                "company_user": {
                    "id": "company-1",
                    "email": "owner@example.com",
                },
                "company_member": {
                    "id": "member-1",
                    "email": "member@example.com",
                    "role": "member",
                    "status": "active",
                },
                "model_entitlement": {
                    "activeModelLimit": 3,
                    "ownerUserIds": ["webui-user"],
                },
            }
        if path == "/api/integrations/open-webui/usage/authorize":
            return {
                "allowed": True,
                "reservation_id": "reservation-1",
                "reserved_tokens": payload["estimated_billable_tokens"],
            }
        if path == "/api/integrations/open-webui/usage/commit":
            return {
                "ok": True,
                "charged_tokens": payload["usage"]["billable_tokens"],
                "remaining_tokens": 10,
            }
        raise AssertionError(f"Unexpected path: {path}")


@pytest.mark.asyncio
async def test_resolve_identity_preserves_company_member():
    client = CaptureBillingClient()
    identity = await client.resolve_identity(SimpleNamespace(id="webui-user", email="member@example.com"))

    assert identity.company_user["id"] == "company-1"
    assert identity.company_member["id"] == "member-1"
    assert identity.model_entitlement["activeModelLimit"] == 3


@pytest.mark.asyncio
async def test_authorize_sends_company_member_context():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = await client.authorize(
        user,
        {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 10,
        },
        {"chat_id": "chat", "message_id": "message", "session_id": "session"},
    )

    authorize_payload = client.requests[-1][2]
    assert authorize_payload["company_user_id"] == "company-1"
    assert authorize_payload["company_member_id"] == "member-1"
    assert authorize_payload["company_member_email"] == "member@example.com"
    assert authorize_payload["metadata"]["companyMemberRole"] == "member"
    assert authorization.company_member_id == "member-1"
    assert authorization.company_member_email == "member@example.com"


@pytest.mark.asyncio
async def test_api_key_authorize_marks_external_api_usage():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = await client.authorize(
        user,
        {
            "model": "workspace-agent",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 10,
        },
        {
            "auth_type": "api_key",
            "api_key_id": "key-1",
        },
    )

    authorize_payload = client.requests[-1][2]
    assert authorize_payload["usage_channel"] == "external_api"
    assert authorize_payload["metadata"]["apiKeyId"] == "key-1"
    assert authorization.usage_channel == "external_api"


@pytest.mark.asyncio
async def test_authorize_line_owner_uses_company_identity_without_member_context():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-owner", email="owner@example.com")
    metadata = {
        "chat_id": "chat",
        "message_id": "message",
        "session_id": "session",
        "interact_channel": {
            "identitySource": "line-binding",
            "companyUserId": "company-1",
            "companyMemberId": None,
            "companyMemberEmail": "owner@example.com",
            "companyMemberRole": "owner",
        },
    }

    authorization = await client.authorize(
        user,
        {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 10,
        },
        metadata,
    )

    authorize_payload = client.requests[-1][2]
    assert authorize_payload["company_user_id"] == "company-1"
    assert "company_member_id" not in authorize_payload
    assert "company_member_email" not in authorize_payload
    assert "companyMemberId" not in authorize_payload["metadata"]
    assert metadata["interact_company"] == {"companyUserId": "company-1"}
    assert authorization.company_member_id is None
    assert authorization.company_member_email is None


@pytest.mark.asyncio
async def test_authorize_line_member_keeps_verified_member_context():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-owner", email="owner@example.com")
    metadata = {
        "chat_id": "chat",
        "message_id": "message",
        "interact_channel": {
            "identitySource": "line-binding",
            "companyUserId": "company-1",
            "companyMemberId": "member-1",
            "companyMemberEmail": "member@example.com",
            "companyMemberRole": "member",
        },
    }

    authorization = await client.authorize(
        user,
        {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 10,
        },
        metadata,
    )

    authorize_payload = client.requests[-1][2]
    assert authorize_payload["company_member_id"] == "member-1"
    assert authorize_payload["company_member_email"] == "member@example.com"
    assert authorization.company_member_id == "member-1"


@pytest.mark.asyncio
async def test_authorize_line_member_without_member_id_fails_closed():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-owner", email="owner@example.com")

    with pytest.raises(HTTPException) as error:
        await client.authorize(
            user,
            {
                "model": "model",
                "messages": [{"role": "user", "content": "hello"}],
                "max_completion_tokens": 10,
            },
            {
                "interact_channel": {
                    "identitySource": "line-binding",
                    "companyUserId": "company-1",
                    "companyMemberId": None,
                    "companyMemberEmail": "member@example.com",
                    "companyMemberRole": "member",
                },
            },
        )

    assert getattr(error.value, "status_code", None) == 403
    assert client.requests == []


@pytest.mark.asyncio
async def test_commit_sends_authorized_company_member_context():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = BillingAuthorization(
        request_id="request-1",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=5,
        max_output_tokens=10,
        reserved_tokens=55,
        company_member_id="member-1",
        company_member_email="member@example.com",
    )

    await client.commit(
        user,
        authorization,
        {"model": "model", "messages": [{"role": "user", "content": "hello"}]},
        {"chat_id": "chat", "message_id": "message"},
        {"prompt_tokens": 5, "completion_tokens": 2},
        "answer",
    )

    commit_payload = client.requests[-1][2]
    assert commit_payload["company_user_id"] == "company-1"
    assert commit_payload["company_member_id"] == "member-1"
    assert commit_payload["company_member_email"] == "member@example.com"
    assert commit_payload["parameters"]["companyMemberId"] == "member-1"
    assert commit_payload["parameters"]["companyMemberEmail"] == "member@example.com"


@pytest.mark.asyncio
async def test_external_api_commit_keeps_authorized_usage_channel():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = BillingAuthorization(
        request_id="request-1",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=5,
        max_output_tokens=10,
        reserved_tokens=55,
        usage_channel="external_api",
    )

    await client.commit(
        user,
        authorization,
        {"model": "workspace-agent", "messages": [{"role": "user", "content": "hello"}]},
        {"api_key_id": "key-1"},
        {"prompt_tokens": 5, "completion_tokens": 2},
        "answer",
    )

    commit_payload = client.requests[-1][2]
    assert commit_payload["usage_channel"] == "external_api"
    assert commit_payload["request_summary"] == "External API chat completion"
    assert commit_payload["parameters"]["usageChannel"] == "external_api"
    assert commit_payload["parameters"]["apiKeyId"] == "key-1"


@pytest.mark.asyncio
async def test_commit_sends_only_auditable_workflow_labels():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = BillingAuthorization(
        request_id="request-1",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=5,
        max_output_tokens=10,
        reserved_tokens=55,
    )

    await client.commit(
        user,
        authorization,
        {"model": "model", "messages": [{"role": "user", "content": "hello"}]},
        {
            "chat_id": "chat",
            "message_id": "message",
            "workflow": {
                "id": "workflow-1",
                "name": "客服摘要",
                "versionId": "version-2",
                "runId": "run-3",
                "trigger": "webui_chat.manual",
                "data": {"secret": "must-not-leak"},
            },
        },
        {"prompt_tokens": 5, "completion_tokens": 2},
        "answer",
    )

    commit_payload = client.requests[-1][2]
    assert commit_payload["request_summary"] == "Workflow: 客服摘要"
    assert commit_payload["parameters"]["workflow"] == {
        "id": "workflow-1",
        "name": "客服摘要",
        "versionId": "version-2",
        "runId": "run-3",
        "trigger": "webui_chat.manual",
    }
