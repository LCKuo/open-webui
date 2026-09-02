import open_webui.utils.interact_billing as interact_billing
from open_webui.utils.interact_billing import (
    BillingAuthorization,
    InteractBillingClient,
    _base_url,
    current_user_question,
    estimate_prompt_tokens,
    estimate_reserved_tokens,
    image_usage_estimate,
    pop_trusted_interact_channel,
    require_metered_inference_entrypoint,
    response_usage_and_answer,
    stream_chunk_billing_data,
    usage_token_counts,
)
from open_webui.utils.response import merge_usage, record_auxiliary_usage
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


def test_browser_cannot_spoof_channel_billing_identity():
    request = SimpleNamespace(state=SimpleNamespace())
    form_data = {
        "interact_channel": {
            "identitySource": "line-binding",
            "companyUserId": "someone-elses-company",
        }
    }

    assert pop_trusted_interact_channel(request, form_data) is None
    assert "interact_channel" not in form_data


def test_validated_channel_runtime_keeps_channel_billing_identity():
    request = SimpleNamespace(state=SimpleNamespace(interact_channel_runtime=True))
    channel = {"source": "channel", "companyUserId": "company-1"}

    assert pop_trusted_interact_channel(request, {"interact_channel": channel}) == channel


def test_provider_proxy_inference_is_blocked_when_billing_is_enabled(monkeypatch):
    monkeypatch.setenv("INTERACT_BILLING_BASE_URL", "https://billing.example.com")
    monkeypatch.setenv("INTERACT_BILLING_SERVICE_TOKEN", "service-token")
    request = SimpleNamespace(url=SimpleNamespace(path="/openai/chat/completions"))

    with pytest.raises(HTTPException) as error:
        require_metered_inference_entrypoint(request)

    assert error.value.status_code == 404


def test_internal_metered_inference_path_remains_available(monkeypatch):
    monkeypatch.setenv("INTERACT_BILLING_BASE_URL", "https://billing.example.com")
    monkeypatch.setenv("INTERACT_BILLING_SERVICE_TOKEN", "service-token")
    request = SimpleNamespace(url=SimpleNamespace(path="/api/v1/chat/completions"))

    require_metered_inference_entrypoint(request)


def test_reserved_tokens_use_weighted_output_credits():
    input_tokens, max_output_tokens, reserved_tokens = estimate_reserved_tokens(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 100,
        }
    )

    assert reserved_tokens == input_tokens + max_output_tokens * 6


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
    assert billable_tokens == 1_000 + 300 * 6 + 200 * 6


def test_missing_usage_uses_estimated_input_and_output():
    assert usage_token_counts(
        None,
        fallback_input_tokens=800,
        fallback_output_tokens=120,
    ) == (800, 120, 0, 1_520)


def test_stateless_completion_response_preserves_provider_usage_for_billing():
    usage, answer = response_usage_and_answer(
        {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            "usage": {
                "prompt_tokens": 73,
                "completion_tokens": 16,
                "total_tokens": 89,
            },
        }
    )

    assert usage == {
        "prompt_tokens": 73,
        "completion_tokens": 16,
        "total_tokens": 89,
    }
    assert answer == "OK"


def test_anthropic_response_preserves_provider_usage_and_answer():
    usage, answer = response_usage_and_answer(
        {
            "content": [{"type": "text", "text": "Anthropic answer"}],
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
    )

    assert usage == {"input_tokens": 12, "output_tokens": 4}
    assert answer == "Anthropic answer"


def test_streaming_completion_chunk_preserves_provider_usage_for_billing():
    usage, answer_delta, final_answer = stream_chunk_billing_data(
        'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":73,"completion_tokens":16,"total_tokens":89}}\n\n'
    )

    assert usage == {
        "prompt_tokens": 73,
        "completion_tokens": 16,
        "total_tokens": 89,
    }
    assert answer_delta == "OK"
    assert final_answer == ""


def test_anthropic_stream_chunks_preserve_usage_and_text():
    start_usage, _, _ = stream_chunk_billing_data(
        'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":0}}}\n\n'
    )
    end_usage, answer_delta, _ = stream_chunk_billing_data(
        'data: {"type":"content_block.delta","delta":{"type":"text_delta","text":"hello"}}\n\n'
        'data: {"type":"message_delta","usage":{"output_tokens":4}}\n\n'
    )

    assert start_usage == {"input_tokens": 12, "output_tokens": 0}
    assert end_usage == {"output_tokens": 4}
    assert answer_delta == "hello"


def test_streaming_done_event_uses_canonical_final_answer():
    usage, answer_delta, final_answer = stream_chunk_billing_data(
        'data: {"done":true,"content":"final answer","usage":{"input_tokens":5,"output_tokens":2}}\n\n'
    )

    assert usage == {"input_tokens": 5, "output_tokens": 2}
    assert answer_delta == ""
    assert final_answer == "final answer"


def test_anthropic_cache_tokens_are_added_to_input():
    assert usage_token_counts(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 10,
        },
        fallback_input_tokens=1,
    ) == (600, 10, 0, 660)


def test_gemini_usage_includes_thoughts_and_tool_prompt_tokens():
    assert usage_token_counts(
        {
            "promptTokenCount": 27,
            "candidatesTokenCount": 45,
            "thoughtsTokenCount": 31,
            "toolUsePromptTokenCount": 10_309,
            "totalTokenCount": 10_412,
        },
        fallback_input_tokens=1,
    ) == (10_336, 45, 31, 10_336 + 45 * 6 + 31 * 6)


def test_unknown_provider_token_class_is_not_dropped():
    assert usage_token_counts(
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 150,
        },
        fallback_input_tokens=1,
    ) == (100, 10, 40, 400)


def test_top_level_reasoning_tokens_use_provider_total_to_avoid_double_counting():
    assert usage_token_counts(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "reasoning_tokens": 20,
            "total_tokens": 150,
        },
        fallback_input_tokens=1,
    ) == (100, 30, 20, 400)


def test_auxiliary_usage_is_merged_into_parent_request():
    request = SimpleNamespace(
        state=SimpleNamespace(
            interact_billing_parent_active=True,
            interact_billing_aux_usage={},
        )
    )

    record_auxiliary_usage(
        request,
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    )
    record_auxiliary_usage(
        request,
        {"usage": {"promptTokenCount": 30, "candidatesTokenCount": 5}},
    )

    assert request.state.interact_billing_aux_usage["input_tokens"] == 130
    assert request.state.interact_billing_aux_usage["output_tokens"] == 25
    assert request.state.interact_billing_aux_usage["total_tokens"] == 155


def test_merge_usage_preserves_mixed_measurement():
    merged = merge_usage(
        {"input_tokens": 10, "output_tokens": 2, "measurement": "estimated"},
        {"input_tokens": 20, "output_tokens": 3},
    )

    assert merged["measurement"] == "mixed"


def test_image_estimate_uses_weighted_compute_credits():
    usage = image_usage_estimate("prompt", 512, 512, 1)

    assert usage["billable_tokens"] == (
        usage["input_tokens"] + usage["output_tokens"] * 6 + usage["compute_tokens"] * 6
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
async def test_reservation_multiplier_does_not_inflate_missing_usage_fallback():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    messages = [{"role": "user", "content": "hello"}]

    authorization = await client.authorize(
        user,
        {
            "model": "model",
            "messages": messages,
            "max_completion_tokens": 10,
            "_billing_multiplier": 3,
        },
        {"chat_id": "chat", "message_id": "message"},
    )

    authorize_payload = client.requests[-1][2]
    base_input_tokens = estimate_prompt_tokens(messages)
    assert authorize_payload["estimated_input_tokens"] == base_input_tokens * 3
    assert authorize_payload["max_output_tokens"] == 30
    assert authorization.estimated_input_tokens == base_input_tokens
    assert authorization.max_output_tokens == 10


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
async def test_authorize_crm_employee_bills_company_without_website_member():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-company", email="company@example.com")
    metadata = {
        "chat_id": "chat",
        "message_id": "message",
        "interact_channel": {
            "identitySource": "line-binding",
            "identitySubject": "product",
            "companyUserId": "company-1",
            "companyMemberId": None,
            "companyMemberEmail": "employee@example.com",
            "companyMemberRole": "member",
            "productKey": "crm",
            "productInstanceId": "crm-instance-a",
            "productUserId": "7",
            "productTeamCodes": ["am"],
        },
    }

    authorization = await client.authorize(
        user,
        {
            "model": "am-agent",
            "messages": [{"role": "user", "content": "新增跟進紀錄"}],
            "max_completion_tokens": 10,
        },
        metadata,
    )

    authorize_payload = client.requests[-1][2]
    assert authorize_payload["company_user_id"] == "company-1"
    assert "company_member_id" not in authorize_payload
    assert authorize_payload["metadata"]["productActor"] == {
        "identitySource": "product",
        "productKey": "crm",
        "productInstanceId": "crm-instance-a",
        "productUserId": "7",
        "productTeamCodes": ["am"],
        "email": "employee@example.com",
        "role": "member",
    }
    assert authorization.company_member_id is None


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
async def test_embedding_commit_never_invents_output_or_compute_tokens():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = BillingAuthorization(
        request_id="embedding-1",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=12,
        max_output_tokens=1,
        reserved_tokens=18,
    )

    await client.commit(
        user,
        authorization,
        {"model": "embedding-model", "messages": [{"role": "user", "content": "hello"}]},
        {"billing_operation": "embedding"},
        None,
        "[Embedding generated]",
    )

    commit_payload = client.requests[-1][2]
    assert commit_payload["tool_key"] == "ai-embedding"
    assert commit_payload["usage"] == {
        "input_tokens": 12,
        "output_tokens": 0,
        "compute_tokens": 0,
        "billable_tokens": 12,
    }


@pytest.mark.asyncio
async def test_failed_image_delivery_keeps_provider_usage_and_failed_status():
    client = CaptureBillingClient()
    user = SimpleNamespace(id="webui-user", email="member@example.com")
    authorization = BillingAuthorization(
        request_id="image-1",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=1,
        max_output_tokens=1,
        reserved_tokens=10_821,
    )

    await client.commit_image(
        user,
        authorization,
        "industrial product image",
        "image-model",
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "compute_tokens": 10_814,
            "billable_tokens": 10_821,
        },
        1,
        "image-generation",
        "failed",
    )

    commit_payload = client.requests[-1][2]
    assert commit_payload["tool_key"] == "ai-image"
    assert commit_payload["status"] == "failed"
    assert commit_payload["usage"]["billable_tokens"] == 10_821


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


@pytest.mark.asyncio
async def test_failed_commit_is_persisted_for_deferred_retry(monkeypatch):
    queued = []

    class FailingBillingClient(InteractBillingClient):
        async def _request(self, method, path, payload=None):
            raise RuntimeError("website temporarily unavailable")

    async def capture_settlement(path, payload, error):
        queued.append((path, payload, str(error)))

    monkeypatch.setattr(interact_billing, "queue_billing_settlement", capture_settlement)
    client = FailingBillingClient()
    authorization = BillingAuthorization(
        request_id="request-deferred",
        company_user_id="company-1",
        reservation_id="reservation-1",
        estimated_input_tokens=5,
        max_output_tokens=10,
        reserved_tokens=65,
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await client.commit(
            SimpleNamespace(id="webui-user", email="member@example.com"),
            authorization,
            {"model": "model", "messages": [{"role": "user", "content": "hello"}]},
            {"message_id": "message-1"},
            {"prompt_tokens": 5, "completion_tokens": 2},
            "answer",
        )

    assert len(queued) == 1
    assert queued[0][0] == "/api/integrations/open-webui/usage/commit"
    assert queued[0][1]["request_id"] == "request-deferred"
    assert queued[0][1]["usage"] == {
        "input_tokens": 5,
        "output_tokens": 2,
        "compute_tokens": 0,
        "billable_tokens": 17,
    }
