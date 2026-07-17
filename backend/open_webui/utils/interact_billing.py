import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

import aiohttp
from fastapi import HTTPException, status

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://interact-vision.com.tw"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_OUTPUT_TOKENS = 2048
DEFAULT_IMAGE_BASE_TOKENS = 1800
OUTPUT_CREDIT_MULTIPLIER = 5
COMPUTE_CREDIT_MULTIPLIER = 5


def _base_url() -> str:
    return DEFAULT_BASE_URL


def _service_token() -> str:
    return (
        os.environ.get("INTERACT_BILLING_SERVICE_TOKEN")
        or os.environ.get("OPEN_WEBUI_BILLING_SERVICE_TOKEN")
        or ""
    ).strip()


def is_billing_enabled() -> bool:
    return bool(_base_url() and _service_token())


def estimate_text_tokens(value: Any) -> int:
    if value is None:
        return 0

    if isinstance(value, list):
        return sum(estimate_text_tokens(item) for item in value)

    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image"}:
            return 180
        return sum(estimate_text_tokens(item) for item in value.values())

    text = str(value)
    ascii_count = sum(1 for char in text if ord(char) <= 0x7F)
    non_ascii_count = len(text) - ascii_count
    return max(0, round(ascii_count / 4 + non_ascii_count * 1.15))


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages or []:
        total += 4
        total += estimate_text_tokens(message.get("content"))
        if message.get("name"):
            total += 1
    return max(1, total)


def estimate_reserved_tokens(form_data: dict[str, Any]) -> tuple[int, int, int]:
    input_tokens = estimate_prompt_tokens(form_data.get("messages", []))
    max_output_tokens = (
        form_data.get("max_completion_tokens")
        or form_data.get("max_tokens")
        or (form_data.get("params") or {}).get("max_tokens")
        or int(os.environ.get("INTERACT_BILLING_DEFAULT_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
    )
    try:
        max_output_tokens = max(1, int(max_output_tokens))
    except (TypeError, ValueError):
        max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS

    try:
        reservation_multiplier = max(1, min(8, int(form_data.get("_billing_multiplier") or 1)))
    except (TypeError, ValueError):
        reservation_multiplier = 1
    input_tokens *= reservation_multiplier
    max_output_tokens *= reservation_multiplier

    return (
        input_tokens,
        max_output_tokens,
        input_tokens + max_output_tokens * OUTPUT_CREDIT_MULTIPLIER,
    )


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _nested_int(data: dict[str, Any], container: str, key: str) -> int:
    nested = data.get(container)
    return _non_negative_int(nested.get(key)) if isinstance(nested, dict) else 0


def usage_token_counts(
    usage: Optional[dict[str, Any]],
    fallback_input_tokens: int,
    fallback_output_tokens: int = 0,
) -> tuple[int, int, int, int]:
    usage = usage or {}
    input_tokens = _non_negative_int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or fallback_input_tokens
        or 0
    )
    # Anthropic cache token fields are additional to input_tokens. OpenAI's
    # cached_tokens is a subset of prompt_tokens and must not be added.
    input_tokens += _non_negative_int(usage.get("cache_creation_input_tokens"))
    input_tokens += _non_negative_int(usage.get("cache_read_input_tokens"))

    output_tokens = _non_negative_int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or fallback_output_tokens
    )
    compute_tokens = _non_negative_int(
        usage.get("compute_tokens")
        or usage.get("thoughts_token_count")
        or usage.get("thoughtsTokenCount")
        or usage.get("reasoning_tokens")
    )

    # OpenAI-compatible reasoning_tokens is included in completion_tokens.
    # Split it out so output + compute does not charge the same tokens twice.
    reasoning_tokens = (
        _nested_int(usage, "completion_tokens_details", "reasoning_tokens")
        or _nested_int(usage, "output_tokens_details", "reasoning_tokens")
    )
    if compute_tokens == 0 and reasoning_tokens > 0:
        compute_tokens = reasoning_tokens
        output_tokens = max(0, output_tokens - reasoning_tokens)

    billable_tokens = (
        input_tokens
        + output_tokens * OUTPUT_CREDIT_MULTIPLIER
        + compute_tokens * COMPUTE_CREDIT_MULTIPLIER
    )
    return input_tokens, output_tokens, compute_tokens, max(1, billable_tokens)


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def current_user_question(form_data: dict[str, Any], metadata: dict[str, Any]) -> str:
    channel_metadata = metadata.get("interact_channel") or {}
    if channel_metadata.get("operation") == "context-summary":
        return "[Internal channel context summary]"

    user_message = metadata.get("user_message") or {}
    question = content_text(user_message.get("content"))
    if question:
        return question
    for message in reversed(form_data.get("messages") or []):
        if message.get("role") == "user":
            return content_text(message.get("content"))
    return ""


def billing_workflow_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    workflow = metadata.get("workflow")
    if not isinstance(workflow, dict):
        return {}

    result: dict[str, str] = {}
    for source_key, target_key in (
        ("id", "id"),
        ("name", "name"),
        ("versionId", "versionId"),
        ("runId", "runId"),
        ("trigger", "trigger"),
    ):
        value = workflow.get(source_key)
        if isinstance(value, str) and value.strip():
            result[target_key] = value.strip()[:200]
    return result


@dataclass
class BillingIdentity:
    company_user: dict[str, Any]
    company_member: Optional[dict[str, Any]] = None


@dataclass
class BillingAuthorization:
    request_id: str
    company_user_id: str
    reservation_id: str
    estimated_input_tokens: int
    max_output_tokens: int
    reserved_tokens: int
    company_member_id: Optional[str] = None
    company_member_email: Optional[str] = None


def image_usage_estimate(prompt: str, width: Optional[int], height: Optional[int], count: Optional[int]) -> dict[str, int]:
    input_tokens = max(1, estimate_text_tokens(prompt))
    image_count = max(1, int(count or 1))
    base_tokens = max(1, int(os.environ.get("INTERACT_BILLING_IMAGE_BASE_TOKENS", DEFAULT_IMAGE_BASE_TOKENS)))
    pixel_scale = 1
    if width and height:
        pixel_scale = max(1, round((width * height) / (512 * 512)))
    compute_tokens = base_tokens * image_count * pixel_scale

    return {
        "input_tokens": input_tokens,
        "output_tokens": image_count,
        "compute_tokens": compute_tokens,
        "billable_tokens": (
            input_tokens
            + image_count * OUTPUT_CREDIT_MULTIPLIER
            + compute_tokens * COMPUTE_CREDIT_MULTIPLIER
        ),
    }


class InteractBillingClient:
    def __init__(self):
        self.base_url = _base_url()
        self.token = _service_token()
        self.timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Interact billing is not configured.")

        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True) as session:
            async with session.request(method, url, headers=self._headers(), json=payload) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    detail = data.get("error") or data.get("detail") or f"Billing API returned HTTP {response.status}"
                    raise HTTPException(status_code=response.status, detail=detail)
                return data

    async def resolve_identity(self, user: Any) -> BillingIdentity:
        if not getattr(user, "email", None):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User email is required for billing.")

        data = await self._request(
            "POST",
            "/api/integrations/open-webui/users/resolve",
            {
                "open_webui_user_id": user.id,
                "email": user.email,
            },
        )
        company_user = data["company_user"]
        company_member = data.get("company_member")
        return BillingIdentity(
            company_user=company_user,
            company_member=company_member if isinstance(company_member, dict) else None,
        )

    async def resolve_user(self, user: Any) -> dict[str, Any]:
        return (await self.resolve_identity(user)).company_user

    async def wallet(self, user: Any) -> dict[str, Any]:
        company_user = await self.resolve_user(user)
        data = await self._request(
            "GET",
            f"/api/integrations/open-webui/users/{company_user['id']}/wallet",
        )
        return data["wallet"]

    async def semantic_entitlements(self, company_user_id: str) -> dict[str, Any]:
        clean_company_user_id = str(company_user_id or '').strip()
        if not clean_company_user_id:
            raise HTTPException(status_code=400, detail='Company account is required.')
        return await self._request(
            'GET',
            f'/api/integrations/open-webui/companies/{clean_company_user_id}/semantic-entitlements',
        )

    async def authorize_line_identity(
        self,
        *,
        company_user_id: str,
        company_member_id: str | None,
        member_email: str,
        channel_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            'POST',
            '/api/integrations/open-webui/line-identities/authorize',
            {
                'company_user_id': company_user_id,
                'company_member_id': company_member_id,
                'member_email': member_email,
                'channel_id': channel_id,
            },
        )

    async def authorize(self, user: Any, form_data: dict[str, Any], metadata: dict[str, Any]) -> BillingAuthorization:
        input_tokens, max_output_tokens, reserved_tokens = estimate_reserved_tokens(form_data)
        channel_metadata = metadata.get('interact_channel') or {}
        if (
            isinstance(channel_metadata, dict)
            and channel_metadata.get('identitySource') == 'line-binding'
            and channel_metadata.get('companyUserId')
            and channel_metadata.get('companyMemberEmail')
            and channel_metadata.get('companyMemberRole')
        ):
            company_user = {'id': str(channel_metadata['companyUserId'])}
            company_member_id = str(channel_metadata.get('companyMemberId') or '').strip()
            company_member_role = str(channel_metadata['companyMemberRole']).strip().lower()
            if company_member_id:
                company_member = {
                    'id': company_member_id,
                    'email': str(channel_metadata['companyMemberEmail']),
                    'role': company_member_role,
                }
            elif company_member_role == 'owner':
                # The company owner is represented by CompanyUser, not CompanyMember.
                company_member = None
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='LINE company member binding is incomplete. Please relink the LINE account.',
                )
        else:
            identity = await self.resolve_identity(user)
            company_user = identity.company_user
            company_member = identity.company_member
        metadata["interact_company"] = {
            "companyUserId": company_user.get("id"),
            **(
                {
                    "companyMemberId": company_member.get("id"),
                    "companyMemberEmail": company_member.get("email"),
                    "companyMemberRole": company_member.get("role"),
                }
                if company_member
                else {}
            ),
        }
        request_id = f"{metadata.get('chat_id') or 'direct'}:{metadata.get('message_id') or uuid4()}"
        workflow_metadata = billing_workflow_metadata(metadata)

        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/authorize",
            {
                "request_id": request_id,
                "company_user_id": company_user["id"],
                **(
                    {
                        "company_member_id": company_member.get("id"),
                        "company_member_email": company_member.get("email"),
                    }
                    if company_member
                    else {}
                ),
                "open_webui_user_id": user.id,
                "chat_id": metadata.get("chat_id"),
                "model": form_data.get("model"),
                "estimated_input_tokens": input_tokens,
                "max_output_tokens": max_output_tokens,
                "estimated_compute_tokens": 0,
                "estimated_billable_tokens": reserved_tokens,
                "metadata": {
                    "openWebuiEmail": user.email,
                    **(
                        {
                            "companyMemberId": company_member.get("id"),
                            "companyMemberEmail": company_member.get("email"),
                            "companyMemberRole": company_member.get("role"),
                        }
                        if company_member
                        else {}
                    ),
                    "openWebuiMessageId": metadata.get("message_id"),
                    "openWebuiSessionId": metadata.get("session_id"),
                    **({"source": "channel", "channel": channel_metadata} if channel_metadata else {}),
                    **({"workflow": workflow_metadata} if workflow_metadata else {}),
                },
            },
        )

        if not data.get("allowed"):
            reason = data.get("reason") or "BILLING_NOT_ALLOWED"
            if reason == "INSUFFICIENT_TOKENS":
                detail = "Token balance is insufficient. Please top up from the company portal."
            elif reason == "ACCOUNT_INACTIVE":
                detail = "Company portal account is inactive."
            else:
                detail = f"Billing authorization failed: {reason}"
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)

        return BillingAuthorization(
            request_id=request_id,
            company_user_id=company_user["id"],
            reservation_id=data["reservation_id"],
            estimated_input_tokens=input_tokens,
            max_output_tokens=max_output_tokens,
            reserved_tokens=data["reserved_tokens"],
            company_member_id=company_member.get("id") if company_member else None,
            company_member_email=company_member.get("email") if company_member else None,
        )

    async def authorize_image(
        self,
        user: Any,
        prompt: str,
        model: Optional[str],
        width: Optional[int],
        height: Optional[int],
        count: Optional[int],
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[BillingAuthorization, dict[str, int]]:
        usage = image_usage_estimate(prompt, width, height, count)
        identity = await self.resolve_identity(user)
        company_user = identity.company_user
        company_member = identity.company_member
        request_id = f"image:{uuid4()}"

        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/authorize",
            {
                "request_id": request_id,
                "company_user_id": company_user["id"],
                **(
                    {
                        "company_member_id": company_member.get("id"),
                        "company_member_email": company_member.get("email"),
                    }
                    if company_member
                    else {}
                ),
                "open_webui_user_id": user.id,
                "model": model,
                "estimated_input_tokens": usage["input_tokens"],
                "max_output_tokens": usage["output_tokens"],
                "estimated_compute_tokens": usage["compute_tokens"],
                "estimated_billable_tokens": usage["billable_tokens"],
                "metadata": {
                    **(metadata or {}),
                    "openWebuiEmail": user.email,
                    **(
                        {
                            "companyMemberId": company_member.get("id"),
                            "companyMemberEmail": company_member.get("email"),
                            "companyMemberRole": company_member.get("role"),
                        }
                        if company_member
                        else {}
                    ),
                    "operation": "image-generation",
                    "width": width,
                    "height": height,
                    "imageCount": count or 1,
                },
            },
        )

        if not data.get("allowed"):
            reason = data.get("reason") or "BILLING_NOT_ALLOWED"
            detail = (
                "Token balance is insufficient. Please top up from the company portal."
                if reason == "INSUFFICIENT_TOKENS"
                else f"Billing authorization failed: {reason}"
            )
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)

        return (
            BillingAuthorization(
                request_id=request_id,
                company_user_id=company_user["id"],
                reservation_id=data["reservation_id"],
                estimated_input_tokens=usage["input_tokens"],
                max_output_tokens=usage["output_tokens"],
                reserved_tokens=data["reserved_tokens"],
                company_member_id=company_member.get("id") if company_member else None,
                company_member_email=company_member.get("email") if company_member else None,
            ),
            usage,
        )

    async def commit(
        self,
        user: Any,
        authorization: BillingAuthorization,
        form_data: dict[str, Any],
        metadata: dict[str, Any],
        usage: Optional[dict[str, Any]],
        answer: Any = None,
    ) -> dict[str, Any]:
        channel_metadata = metadata.get("interact_channel") or {}
        workflow_metadata = billing_workflow_metadata(metadata)
        question_text = current_user_question(form_data, metadata)
        answer_text = content_text(answer)
        logged_answer_text = (
            "[Channel context summary updated]"
            if channel_metadata.get("operation") == "context-summary"
            else answer_text
        )
        input_tokens, output_tokens, compute_tokens, billable_tokens = usage_token_counts(
            usage,
            authorization.estimated_input_tokens,
            estimate_text_tokens(answer_text),
        )
        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/commit",
            {
                "request_id": authorization.request_id,
                "reservation_id": authorization.reservation_id,
                "company_user_id": authorization.company_user_id,
                **(
                    {
                        "company_member_id": authorization.company_member_id,
                        "company_member_email": authorization.company_member_email,
                    }
                    if authorization.company_member_id or authorization.company_member_email
                    else {}
                ),
                "open_webui_user_id": user.id,
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
                "model": form_data.get("model"),
                "status": "completed",
                "request_summary": (
                    f"Workflow: {workflow_metadata.get('name') or workflow_metadata.get('id')}"
                    if workflow_metadata
                    else "Open WebUI channel chat completion"
                    if channel_metadata
                    else "Open WebUI chat completion"
                ),
                "provider_request_id": metadata.get("message_id"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "compute_tokens": compute_tokens,
                    "billable_tokens": billable_tokens,
                },
                "parameters": {
                    "openWebuiEmail": user.email,
                    **(
                        {
                            "companyMemberId": authorization.company_member_id,
                            "companyMemberEmail": authorization.company_member_email,
                        }
                        if authorization.company_member_id or authorization.company_member_email
                        else {}
                    ),
                    "reservedTokens": authorization.reserved_tokens,
                    "chargedAt": int(time.time()),
                    "content": {
                        "type": "text-chat",
                        "question": question_text,
                        "answer": logged_answer_text,
                    },
                    **({"source": "channel", "channel": channel_metadata} if channel_metadata else {}),
                    **({"workflow": workflow_metadata} if workflow_metadata else {}),
                },
            },
        )
        return data

    async def commit_image(
        self,
        user: Any,
        authorization: BillingAuthorization,
        prompt: str,
        model: Optional[str],
        usage: dict[str, int],
        image_count: int,
        operation: str = "image-generation",
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/commit",
            {
                "request_id": authorization.request_id,
                "reservation_id": authorization.reservation_id,
                "company_user_id": authorization.company_user_id,
                **(
                    {
                        "company_member_id": authorization.company_member_id,
                        "company_member_email": authorization.company_member_email,
                    }
                    if authorization.company_member_id or authorization.company_member_email
                    else {}
                ),
                "open_webui_user_id": user.id,
                "model": model,
                "tool_key": "ai-image",
                "service_name": "Open WebUI Image Generation",
                "status": "completed",
                "request_summary": prompt[:500],
                "usage": {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": image_count,
                    "compute_tokens": usage["compute_tokens"],
                    "billable_tokens": usage["billable_tokens"],
                },
                "parameters": {
                    "openWebuiEmail": user.email,
                    **(
                        {
                            "companyMemberId": authorization.company_member_id,
                            "companyMemberEmail": authorization.company_member_email,
                        }
                        if authorization.company_member_id or authorization.company_member_email
                        else {}
                    ),
                    "reservedTokens": authorization.reserved_tokens,
                    "imageCount": image_count,
                    "chargedAt": int(time.time()),
                    "content": {
                        "type": operation,
                        "prompt": prompt,
                    },
                },
            },
        )
        return data

    async def cancel(self, authorization: Optional[BillingAuthorization], reason: str = "cancelled") -> None:
        if not authorization:
            return
        try:
            await self._request(
                "POST",
                "/api/integrations/open-webui/usage/cancel",
                {
                    "request_id": authorization.request_id,
                    "reservation_id": authorization.reservation_id,
                    "reason": reason,
                },
            )
        except Exception as e:
            log.warning("Failed to cancel billing reservation: %s", e)
