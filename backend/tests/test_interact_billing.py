from open_webui.utils.interact_billing import (
    BillingAuthorization,
    InteractBillingClient,
    current_user_question,
    estimate_reserved_tokens,
    image_usage_estimate,
    usage_token_counts,
)
from types import SimpleNamespace

import pytest


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
