from __future__ import annotations

import json
from typing import Any


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def append_channel_for_selected_model(
    *,
    access_mode: str,
    allowed_model_ids: Any,
    allowed_channel_ids: Any,
    model_id: str,
    channel_id: str,
) -> list[str] | None:
    """Return an updated grant only when an existing model-scoped ACL allows it."""
    if access_mode != 'selected_channels':
        return None
    if model_id not in _scope_list(allowed_model_ids):
        return None
    channel_ids = _scope_list(allowed_channel_ids)
    if channel_id in channel_ids:
        return None
    return [*channel_ids, channel_id]
