from __future__ import annotations

import hashlib
import json
from typing import Any

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.notes import Notes

CHANNEL_KNOWLEDGE_SCOPE_VERSION = 'company-attached-v1'
SUPPORTED_KNOWLEDGE_TYPES = {'collection', 'file', 'note'}


class KnowledgeScopeViolation(ValueError):
    pass


def normalized_knowledge_refs(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    refs: set[tuple[str, str]] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '').strip()
        item_id = str(item.get('id') or '').strip()
        if item_type and item_id:
            refs.add((item_type, item_id))
    return [{'type': item_type, 'id': item_id} for item_type, item_id in sorted(refs)]


def channel_knowledge_scope_fingerprint(model_id: str, items: list[dict[str, Any]] | None) -> str:
    payload = {
        'version': CHANNEL_KNOWLEDGE_SCOPE_VERSION,
        'model_id': model_id,
        'knowledge': normalized_knowledge_refs(items),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('utf-8')
    ).hexdigest()[:24]


async def validate_company_knowledge_scope(
    items: list[dict[str, Any]] | None,
    *,
    owner_user_id: str,
) -> list[dict[str, Any]]:
    """Require every attached resource to be owned by the channel company's WebUI account."""
    validated: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            raise KnowledgeScopeViolation('Invalid knowledge attachment.')

        item_type = str(item.get('type') or '').strip()
        item_id = str(item.get('id') or '').strip()
        if item_type not in SUPPORTED_KNOWLEDGE_TYPES or not item_id:
            raise KnowledgeScopeViolation('Unsupported knowledge attachment.')

        if item_type == 'collection':
            resource = await Knowledges.get_knowledge_by_id(item_id)
        elif item_type == 'file':
            resource = await Files.get_file_by_id(item_id)
        else:
            resource = await Notes.get_note_by_id(item_id)

        if resource is None or str(resource.user_id) != owner_user_id:
            raise KnowledgeScopeViolation(
                f'Knowledge resource {item_type}:{item_id} is outside the company scope.'
            )
        validated.append(item)

    return validated
