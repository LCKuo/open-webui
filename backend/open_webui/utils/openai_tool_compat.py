"""Compatibility rules for OpenAI Chat Completions tool calls."""

import re
from typing import Any

GPT_56_MODEL = re.compile(r'^gpt-5\.6(?:-|$)', re.IGNORECASE)


def apply_chat_completion_tool_compat(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply provider-required settings without changing non-tool requests."""
    model_id = str(payload.get('model') or '')
    if payload.get('tools') and GPT_56_MODEL.match(model_id):
        payload['reasoning_effort'] = 'none'
    return payload


def managed_agent_reasoning_effort(model_id: str) -> str | None:
    """Return the stored agent setting required by the current transport."""
    upstream_model_id = model_id.split('.', 1)[1] if model_id.startswith('own-') and '.' in model_id else model_id
    if GPT_56_MODEL.match(upstream_model_id):
        return 'none'
    if 'kimi-k3' in upstream_model_id.lower():
        return 'low'
    return None
