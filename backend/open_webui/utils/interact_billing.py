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


def _base_url() -> str:
    return (os.environ.get("INTERACT_BILLING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


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

    return input_tokens, max_output_tokens, input_tokens + max_output_tokens


def usage_token_counts(usage: Optional[dict[str, Any]], fallback_input_tokens: int) -> tuple[int, int, int, int]:
    usage = usage or {}
    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or fallback_input_tokens
        or 0
    )
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    compute_tokens = int(usage.get("compute_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens + compute_tokens)
    billable_tokens = int(usage.get("billable_tokens") or total_tokens)
    return max(0, input_tokens), max(0, output_tokens), max(0, compute_tokens), max(1, billable_tokens)


@dataclass
class BillingAuthorization:
    request_id: str
    company_user_id: str
    reservation_id: str
    estimated_input_tokens: int
    max_output_tokens: int
    reserved_tokens: int


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
        "billable_tokens": input_tokens + compute_tokens,
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

    async def resolve_user(self, user: Any) -> dict[str, Any]:
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
        return data["company_user"]

    async def wallet(self, user: Any) -> dict[str, Any]:
        company_user = await self.resolve_user(user)
        data = await self._request(
            "GET",
            f"/api/integrations/open-webui/users/{company_user['id']}/wallet",
        )
        return data["wallet"]

    async def authorize(self, user: Any, form_data: dict[str, Any], metadata: dict[str, Any]) -> BillingAuthorization:
        input_tokens, max_output_tokens, reserved_tokens = estimate_reserved_tokens(form_data)
        company_user = await self.resolve_user(user)
        request_id = f"{metadata.get('chat_id') or 'direct'}:{metadata.get('message_id') or uuid4()}"

        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/authorize",
            {
                "request_id": request_id,
                "company_user_id": company_user["id"],
                "open_webui_user_id": user.id,
                "chat_id": metadata.get("chat_id"),
                "model": form_data.get("model"),
                "estimated_input_tokens": input_tokens,
                "max_output_tokens": max_output_tokens,
                "estimated_billable_tokens": reserved_tokens,
                "metadata": {
                    "openWebuiEmail": user.email,
                    "openWebuiMessageId": metadata.get("message_id"),
                    "openWebuiSessionId": metadata.get("session_id"),
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
        company_user = await self.resolve_user(user)
        request_id = f"image:{uuid4()}"

        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/authorize",
            {
                "request_id": request_id,
                "company_user_id": company_user["id"],
                "open_webui_user_id": user.id,
                "model": model,
                "estimated_input_tokens": usage["input_tokens"],
                "max_output_tokens": usage["output_tokens"],
                "estimated_billable_tokens": usage["billable_tokens"],
                "metadata": {
                    **(metadata or {}),
                    "openWebuiEmail": user.email,
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
    ) -> dict[str, Any]:
        input_tokens, output_tokens, compute_tokens, billable_tokens = usage_token_counts(
            usage,
            authorization.estimated_input_tokens,
        )
        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/commit",
            {
                "request_id": authorization.request_id,
                "reservation_id": authorization.reservation_id,
                "company_user_id": authorization.company_user_id,
                "open_webui_user_id": user.id,
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
                "model": form_data.get("model"),
                "status": "completed",
                "request_summary": "Open WebUI chat completion",
                "provider_request_id": metadata.get("message_id"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "compute_tokens": compute_tokens,
                    "billable_tokens": billable_tokens,
                },
                "parameters": {
                    "openWebuiEmail": user.email,
                    "reservedTokens": authorization.reserved_tokens,
                    "chargedAt": int(time.time()),
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
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/api/integrations/open-webui/usage/commit",
            {
                "request_id": authorization.request_id,
                "reservation_id": authorization.reservation_id,
                "company_user_id": authorization.company_user_id,
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
                    "reservedTokens": authorization.reserved_tokens,
                    "imageCount": image_count,
                    "chargedAt": int(time.time()),
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
