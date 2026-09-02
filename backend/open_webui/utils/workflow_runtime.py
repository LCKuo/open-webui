import ast
import hashlib
import json
import operator
import re
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

WorkflowModelRunner = Callable[[str, str | None, str | None, list[dict[str, Any]]], Awaitable[dict[str, Any]]]
WorkflowNodeRunner = Callable[[str, dict[str, Any], Any, dict[str, Any]], Awaitable[Any]]

RUNTIME_INPUT_TYPES = {
    'input',
    'chat_input',
    'channel_input',
    'webhook_trigger',
    'schedule_trigger',
    'file_upload',
    'form_input',
    'user_input',
}
RUNTIME_MODEL_TYPES = {
    'agent',
    'supervisor_agent',
    'worker_agent',
    'planner',
    'evaluator',
    'chat_model',
    'vision_model',
}
RUNTIME_OUTPUT_TYPES = {
    'output',
    'chat_output',
    'channel_reply',
    'media_output',
    'file_output',
    'handoff',
    'webhook_response',
    'notification',
}
RUNTIME_PASSTHROUGH_TYPES = {
    'conversation_memory',
    'summary_memory',
    'entity_memory',
    'context_builder',
    'merge',
    'trace',
    'usage_meter',
    'error_handler',
    'fallback',
}
SUPPORTED_RUNTIME_NODE_TYPES = (
    RUNTIME_INPUT_TYPES
    | RUNTIME_MODEL_TYPES
    | RUNTIME_OUTPUT_TYPES
    | RUNTIME_PASSTHROUGH_TYPES
    | {
        'system_prompt',
        'prompt_template',
        'calculator',
        'transform_json',
        'json_parse',
        'extract_fields',
        'structured_extract',
        'web_search',
        'fetch_url',
        'prospect_contact_enrichment',
        'knowledge_query',
        'database_query',
        'semantic_query',
        'customer_contact_lookup',
        'condition',
        'user_choice',
        'approval_gate',
        'campaign_approval_gate',
        'email_compose',
        'email_campaign_compose',
        'email_send',
        'email_campaign_send',
        'email_delivery_status',
        'campaign_delivery_summary',
        'email_result',
    }
)

MEDIA_TYPES = {'image', 'audio', 'video', 'file'}
OUTPUT_TYPES = MEDIA_TYPES | {'text', 'card', 'handoff', 'json'}


class WorkflowRuntimeError(RuntimeError):
    pass


def _merge_usage_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, raw_value in source.items():
        if isinstance(raw_value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            _merge_usage_counts(current, raw_value)
            continue
        if isinstance(raw_value, bool):
            continue
        try:
            value = max(0, int(raw_value))
        except (TypeError, ValueError):
            continue
        target[key] = int(target.get(key) or 0) + value


class WorkflowPause(RuntimeError):
    def __init__(
        self,
        node_id: str,
        wait_type: str,
        prompt: dict[str, Any],
        state: dict[str, Any],
        payload_hash: str | None = None,
    ):
        super().__init__(f'Workflow is waiting for {wait_type}.')
        self.node_id = node_id
        self.wait_type = wait_type
        self.prompt = prompt
        self.state = state
        self.payload_hash = payload_hash


PROSPECTING_DISCOVERY_CONTRACT_V1 = 'interact.crm.prospecting.discovery.v1'
PROSPECTING_DISCOVERY_CONTRACT = 'interact.crm.prospecting.discovery.v2'
_PROSPECTING_EVIDENCE_TYPES = {'website', 'directory', 'news', 'job_posting', 'trade_show'}
_PROSPECTING_EMAIL_TYPES = {'personal', 'role', 'unknown'}
_PROSPECTING_BUSINESS_ACTIVITY_CATEGORIES = {
    'industry',
    'equipment',
    'process',
    'product',
    'service',
    'application',
}
_PROSPECTING_EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
_PROSPECTING_PROFILE_FIELDS = {
    'industries',
    'productKeywords',
    'needSignals',
    'exclusionSignals',
}


def _contract_text(value: Any, *, maximum: int, nullable: bool = True) -> str | None:
    if value is None:
        return None if nullable else ''
    text = str(value).strip()
    if not text:
        return None if nullable else ''
    return text[:maximum]


def _contract_url(value: Any) -> str | None:
    text = _contract_text(value, maximum=2000)
    if not text:
        return None
    parsed = urlparse(text)
    return text if parsed.scheme in {'http', 'https'} and parsed.hostname else None


def _contract_email(value: Any) -> str | None:
    text = _contract_text(value, maximum=320)
    if not text:
        return None
    normalized = text.lower()
    return normalized if _PROSPECTING_EMAIL_PATTERN.fullmatch(normalized) else None


def _contract_score(value: Any) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def _contract_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _normalize_prospecting_evidence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    url = _contract_url(value.get('url'))
    title = _contract_text(value.get('title'), maximum=300, nullable=False)
    excerpt = _contract_text(value.get('excerpt'), maximum=3000, nullable=False)
    if not url or len(title or '') < 2 or len(excerpt or '') < 5:
        return None
    evidence_type = str(value.get('type') or 'website').strip()
    observed_at = _contract_text(value.get('observedAt'), maximum=10)
    if observed_at and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', observed_at):
        observed_at = None
    return {
        'type': evidence_type if evidence_type in _PROSPECTING_EVIDENCE_TYPES else 'website',
        'title': title,
        'url': url,
        'excerpt': excerpt,
        'supportsNeed': _contract_bool(value.get('supportsNeed'), True),
        'confidence': _contract_score(value.get('confidence', 50)),
        'observedAt': observed_at,
    }


def _normalize_prospecting_contact(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    email = _contract_email(value.get('email'))
    source_url = _contract_url(value.get('sourceUrl'))
    source_excerpt = _contract_text(value.get('sourceExcerpt'), maximum=3000, nullable=False)
    if not email or not source_url or len(source_excerpt or '') < 5:
        return None
    email_type = str(value.get('emailType') or 'unknown').strip()
    return {
        'name': _contract_text(value.get('name'), maximum=500),
        'title': _contract_text(value.get('title'), maximum=500),
        'department': _contract_text(value.get('department'), maximum=500),
        'email': email,
        'emailType': email_type if email_type in _PROSPECTING_EMAIL_TYPES else 'unknown',
        'sourceUrl': source_url,
        'sourceExcerpt': source_excerpt,
        'confidence': _contract_score(value.get('confidence', 50)),
        'verificationStatus': 'verified',
    }


def _normalize_prospecting_business_activity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    category = str(value.get('category') or '').strip()
    label = _contract_text(value.get('label'), maximum=160, nullable=False)
    evidence_url = _contract_url(value.get('evidenceUrl'))
    evidence_excerpt = _contract_text(
        value.get('evidenceExcerpt'), maximum=1000, nullable=False
    )
    if (
        category not in _PROSPECTING_BUSINESS_ACTIVITY_CATEGORIES
        or len(label or '') < 2
        or not evidence_url
        or len(evidence_excerpt or '') < 5
    ):
        return None
    return {
        'category': category,
        'label': label,
        'evidenceUrl': evidence_url,
        'evidenceExcerpt': evidence_excerpt,
        'confidence': _contract_score(value.get('confidence', 50)),
    }


def _normalize_prospecting_entry_point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = _contract_text(value.get('name'), maximum=160, nullable=False)
    rationale = _contract_text(value.get('rationale'), maximum=1000, nullable=False)
    supporting_facts = [
        text
        for item in (value.get('supportingFacts') if isinstance(value.get('supportingFacts'), list) else [])[:5]
        if len(text := (_contract_text(item, maximum=500, nullable=False) or '')) >= 2
    ]
    assumptions = [
        text
        for item in (value.get('assumptions') if isinstance(value.get('assumptions'), list) else [])[:5]
        if len(text := (_contract_text(item, maximum=500, nullable=False) or '')) >= 2
    ]
    evidence_urls: list[str] = []
    for item in (value.get('evidenceUrls') if isinstance(value.get('evidenceUrls'), list) else [])[:5]:
        url = _contract_url(item)
        if url and url not in evidence_urls:
            evidence_urls.append(url)
    if len(name or '') < 2 or len(rationale or '') < 10 or not supporting_facts or not evidence_urls:
        return None
    return {
        'name': name,
        'rationale': rationale,
        'supportingFacts': supporting_facts,
        'assumptions': assumptions,
        'evidenceUrls': evidence_urls,
        'confidence': _contract_score(value.get('confidence', 50)),
    }


def _normalize_prospecting_candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = _contract_text(value.get('name'), maximum=200, nullable=False)
    fit_summary = _contract_text(value.get('fitSummary'), maximum=3000, nullable=False)
    uncertainty = _contract_text(value.get('uncertaintyNotes'), maximum=3000, nullable=False)
    evidence = [
        normalized
        for item in (value.get('evidence') if isinstance(value.get('evidence'), list) else [])[:10]
        if (normalized := _normalize_prospecting_evidence(item)) is not None
    ]
    if len(name or '') < 2 or len(fit_summary or '') < 10 or not evidence:
        return None
    if len(uncertainty or '') < 5:
        uncertainty = '尚待人工確認採購需求、時程與聯絡窗口。'
    contacts = [
        normalized
        for item in (value.get('contacts') if isinstance(value.get('contacts'), list) else [])[:10]
        if (normalized := _normalize_prospecting_contact(item)) is not None
    ]
    business_activities = [
        normalized
        for item in (
            value.get('businessActivities')
            if isinstance(value.get('businessActivities'), list)
            else []
        )[:15]
        if (normalized := _normalize_prospecting_business_activity(item)) is not None
    ]
    suggested_entry_points = [
        normalized
        for item in (
            value.get('suggestedEntryPoints')
            if isinstance(value.get('suggestedEntryPoints'), list)
            else []
        )[:3]
        if (normalized := _normalize_prospecting_entry_point(item)) is not None
    ]
    scores = {
        key: _contract_score(value.get(key))
        for key in (
            'structuralNeedScore',
            'capabilityFitScore',
            'timingScore',
            'evidenceScore',
            'commercialFitScore',
        )
    }
    total_score = round(sum(scores.values()) / len(scores))
    has_supporting_evidence = any(
        item['supportsNeed'] and item['confidence'] >= 40 for item in evidence
    )
    policy_reasons = []
    if scores['structuralNeedScore'] < 40:
        policy_reasons.append('結構性需求證據不足')
    if scores['capabilityFitScore'] < 45:
        policy_reasons.append('能力適配度不足')
    if scores['evidenceScore'] < 35 or not has_supporting_evidence:
        policy_reasons.append('缺少可信的正向來源證據')
    if total_score < 40:
        policy_reasons.append('綜合適配分數過低')
    excluded = _contract_bool(value.get('excluded')) or bool(policy_reasons)
    exclusion_reason = _contract_text(value.get('exclusionReason'), maximum=500)
    if policy_reasons and not exclusion_reason:
        exclusion_reason = '；'.join(policy_reasons)
    contact_email = _contract_email(value.get('contactEmail'))
    tax_id_digits = re.sub(r'\D', '', str(value.get('taxId') or ''))
    tax_id = tax_id_digits if re.fullmatch(r'\d{8}', tax_id_digits) else None
    return {
        'name': name,
        'taxId': tax_id,
        'industry': _contract_text(value.get('industry'), maximum=500),
        'city': _contract_text(value.get('city'), maximum=500),
        'address': _contract_text(value.get('address'), maximum=500),
        'website': _contract_url(value.get('website')),
        'contactEmail': contact_email,
        'contacts': contacts,
        'businessActivities': business_activities,
        'suggestedEntryPoints': suggested_entry_points,
        'contactEnrichmentStatus': 'verified' if contacts else 'not_found',
        'phone': _contract_text(value.get('phone'), maximum=500),
        **scores,
        'fitSummary': fit_summary,
        'uncertaintyNotes': uncertainty,
        'excluded': excluded,
        'exclusionReason': exclusion_reason,
        'evidence': evidence,
    }


def _normalize_profile_suggestion(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or str(value.get('action') or 'add').strip() != 'add':
        return None
    field = str(value.get('field') or '').strip()
    term = _contract_text(value.get('term'), maximum=80, nullable=False)
    reason = _contract_text(value.get('reason'), maximum=500, nullable=False)
    if field not in _PROSPECTING_PROFILE_FIELDS or len(term or '') < 2 or len(reason or '') < 10:
        return None
    evidence_urls = []
    for item in (value.get('evidenceUrls') if isinstance(value.get('evidenceUrls'), list) else [])[:5]:
        url = _contract_url(item)
        if url and url not in evidence_urls:
            evidence_urls.append(url)
    if not evidence_urls:
        return None
    return {
        'action': 'add',
        'field': field,
        'term': term,
        'reason': reason,
        'confidence': _contract_score(value.get('confidence', 0)),
        'evidenceUrls': evidence_urls,
    }


def _normalize_prospecting_discovery(
    value: Any,
    *,
    include_profile_suggestions: bool = True,
) -> dict[str, Any]:
    if isinstance(value, list):
        value = next(
            (item for item in value if isinstance(item, dict) and isinstance(item.get('candidates'), list)),
            None,
        )
    if not isinstance(value, dict) or not isinstance(value.get('candidates'), list):
        raise WorkflowRuntimeError('Prospecting output must contain a candidates array.')
    raw_candidates = value.get('candidates', [])[:50]
    candidates = [
        normalized
        for item in raw_candidates
        if (normalized := _normalize_prospecting_candidate(item)) is not None
    ]
    if raw_candidates and not candidates:
        raise WorkflowRuntimeError('Prospecting output did not contain any structurally valid candidates.')
    notes: list[str] = []
    raw_notes = value.get('notes') if isinstance(value.get('notes'), list) else []
    for item in raw_notes[:20]:
        note_value = item.get('note') if isinstance(item, dict) else item
        note = _contract_text(note_value, maximum=500)
        if note:
            notes.append(note)
    dropped = len(raw_candidates) - len(candidates)
    if dropped:
        notes.append(f'已略過 {dropped} 筆缺少公司名稱、適配說明或公開來源的無效候選。')
    result = {'version': '1', 'candidates': candidates, 'notes': notes[:20]}
    if include_profile_suggestions:
        suggestions = [
            normalized
            for item in (
                value.get('profileSuggestions')
                if isinstance(value.get('profileSuggestions'), list)
                else []
            )[:5]
            if (normalized := _normalize_profile_suggestion(item)) is not None
        ]
        result['profileSuggestions'] = suggestions
    return result


def _normalize_output_contract(value: Any, contract: str) -> Any:
    if contract in {PROSPECTING_DISCOVERY_CONTRACT_V1, PROSPECTING_DISCOVERY_CONTRACT}:
        return _normalize_prospecting_discovery(
            value,
            include_profile_suggestions=contract == PROSPECTING_DISCOVERY_CONTRACT,
        )
    return value


def node_semantic_type(node: dict[str, Any]) -> str:
    data = node.get('data') if isinstance(node.get('data'), dict) else {}
    return str(data.get('type') or data.get('kind') or node.get('type') or '').strip()


def runtime_unsupported_node_types(graph: dict[str, Any]) -> list[str]:
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    return sorted(
        {
            node_semantic_type(node)
            for node in nodes
            if isinstance(node, dict) and node_semantic_type(node) not in SUPPORTED_RUNTIME_NODE_TYPES
        }
    )


def normalize_workflow_input(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    message = str(source.get('message') or source.get('text') or '')
    parts = []
    media_keys: set[tuple[str, str]] = set()
    for part in source.get('parts') or []:
        if isinstance(part, dict) and part.get('type') in OUTPUT_TYPES | {'location', 'postback'}:
            normalized_part = dict(part)
            media_id = str(
                normalized_part.get('fileId')
                or normalized_part.get('id')
                or normalized_part.get('url')
                or normalized_part.get('platformFileId')
                or ''
            )
            if media_id:
                media_keys.add((str(normalized_part.get('type') or ''), media_id))
            parts.append(normalized_part)

    for file_item in source.get('files') or []:
        if not isinstance(file_item, dict):
            continue
        mime_type = str(file_item.get('content_type') or file_item.get('mimeType') or '')
        part_type = (
            'image'
            if mime_type.startswith('image/') or file_item.get('type') == 'image'
            else 'audio'
            if mime_type.startswith('audio/')
            else 'video'
            if mime_type.startswith('video/')
            else 'file'
        )
        normalized_file = {
            'type': part_type,
            'fileId': file_item.get('id') or file_item.get('fileId'),
            'url': file_item.get('url'),
            'filename': file_item.get('name') or file_item.get('filename'),
            'mimeType': mime_type or None,
            'size': file_item.get('size'),
        }
        media_id = str(normalized_file.get('fileId') or normalized_file.get('url') or '')
        if media_id and (part_type, media_id) in media_keys:
            continue
        if media_id:
            media_keys.add((part_type, media_id))
        parts.append(normalized_file)

    if message and not any(part.get('type') == 'text' for part in parts):
        parts.insert(0, {'type': 'text', 'text': message})

    return {
        'message': message,
        'parts': parts,
        'data': source.get('data') if isinstance(source.get('data'), dict) else {},
        'context': source.get('context') if isinstance(source.get('context'), dict) else {},
    }


def _node_config(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get('data') if isinstance(node.get('data'), dict) else {}
    config = data.get('config')
    return config if isinstance(config, dict) else {}


def _value_parts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        if value.get('type') in OUTPUT_TYPES:
            return [value]
        if isinstance(value.get('parts'), list):
            return [part for part in value['parts'] if isinstance(part, dict)]
        if isinstance(value.get('outputs'), list):
            return [part for part in value['outputs'] if isinstance(part, dict)]
        if 'text' in value:
            return [{'type': 'text', 'text': str(value.get('text') or '')}]
        return [{'type': 'json', 'value': value}]
    if isinstance(value, list):
        return [part for item in value for part in _value_parts(item)]
    return [{'type': 'text', 'text': str(value)}]


def _value_text(value: Any) -> str:
    texts = [str(part.get('text') or '') for part in _value_parts(value) if part.get('type') == 'text']
    if texts:
        return '\n'.join(text for text in texts if text)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _parse_json_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get('type') == 'text':
        value = value.get('text')
    elif isinstance(value, (dict, list)):
        return value
    text = _value_text(value).strip()
    if not text:
        raise WorkflowRuntimeError('JSON parser received an empty value.')
    if len(text) > 1_000_000:
        raise WorkflowRuntimeError('JSON parser input exceeds the 1 MB limit.')
    fenced = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        object_start = text.find('{')
        object_end = text.rfind('}')
        array_start = text.find('[')
        array_end = text.rfind(']')
        candidates = []
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1])
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1])
        for candidate in sorted(candidates, key=len, reverse=True):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise WorkflowRuntimeError('Model output is not valid JSON.')


def _render_template(template: str, workflow_input: dict[str, Any], incoming: Any) -> str:
    values = {
        'message': workflow_input.get('message', ''),
        'input': _value_text(incoming),
        **(workflow_input.get('data') or {}),
        **(workflow_input.get('context') or {}),
    }

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = values.get(key, match.group(0))
        return _value_text(value) if isinstance(value, (dict, list)) else str(value)

    return re.sub(r'\{\{\s*([^{}]+?)\s*\}\}', replace, template)


def _extract_path(value: Any, path: str) -> Any:
    current = value
    for part in str(path or '').split('.'):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _condition_matches(incoming: Any, config: dict[str, Any]) -> bool:
    left = _extract_path(incoming, str(config.get('field') or '')) if config.get('field') else incoming
    right = config.get('value')
    operator_name = str(config.get('operator') or 'truthy')
    if operator_name == 'truthy':
        return bool(left)
    if operator_name == 'falsy':
        return not left
    if operator_name == 'exists':
        return left is not None
    if operator_name == 'not_exists':
        return left is None
    if operator_name == 'eq':
        return left == right
    if operator_name == 'neq':
        return left != right
    if operator_name == 'contains':
        return str(right).lower() in str(left or '').lower()
    if operator_name == 'starts_with':
        return str(left or '').lower().startswith(str(right).lower())
    if operator_name in {'gt', 'gte', 'lt', 'lte'}:
        try:
            left_number = float(left)
            right_number = float(right)
        except (TypeError, ValueError):
            return False
        return {
            'gt': left_number > right_number,
            'gte': left_number >= right_number,
            'lt': left_number < right_number,
            'lte': left_number <= right_number,
        }[operator_name]
    raise WorkflowRuntimeError(f'Unsupported condition operator: {operator_name}.')


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_calculate(expression: str) -> int | float:
    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](evaluate(node.operand))
        raise WorkflowRuntimeError('Calculator expression contains an unsupported operation.')

    if len(expression) > 500:
        raise WorkflowRuntimeError('Calculator expression is too long.')
    return evaluate(ast.parse(expression, mode='eval'))


def _topological_order(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    nodes = [node for node in graph.get('nodes', []) if isinstance(node, dict)]
    by_id = {str(node.get('id')): node for node in nodes}
    incoming = {node_id: [] for node_id in by_id}
    outgoing = {node_id: [] for node_id in by_id}
    for edge in graph.get('edges', []):
        if not isinstance(edge, dict):
            continue
        source = str(edge.get('source') or '')
        target = str(edge.get('target') or '')
        if source in by_id and target in by_id:
            incoming[target].append(source)
            outgoing[source].append(target)

    queue = deque(node_id for node_id, sources in incoming.items() if not sources)
    ordered_ids = []
    remaining = {node_id: len(sources) for node_id, sources in incoming.items()}
    while queue:
        node_id = queue.popleft()
        ordered_ids.append(node_id)
        for target in outgoing[node_id]:
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if len(ordered_ids) != len(nodes):
        raise WorkflowRuntimeError('Workflow graph contains a cycle.')
    return [by_id[node_id] for node_id in ordered_ids], incoming


def _configured_output(config: dict[str, Any], incoming: Any) -> list[dict[str, Any]]:
    output_type = str(config.get('output_type') or '').strip()
    if not output_type:
        return _value_parts(incoming)
    if output_type not in OUTPUT_TYPES:
        raise WorkflowRuntimeError(f'Unsupported workflow output type: {output_type}.')
    if output_type == 'text':
        return [{'type': 'text', 'text': str(config.get('text') or _value_text(incoming))}]
    if output_type == 'json':
        return [{'type': 'json', 'value': config.get('value', incoming)}]
    result = {'type': output_type}
    result.update(
        {
            key: value
            for key, value in config.items()
            if key in {'url', 'fileId', 'filename', 'mimeType', 'alt', 'title', 'thumbnailUrl', 'body', 'actions'}
            and value not in (None, '')
        }
    )
    return [result]


async def execute_workflow_graph(
    graph: dict[str, Any],
    payload: dict[str, Any] | None,
    model_runner: WorkflowModelRunner | None = None,
    node_runner: WorkflowNodeRunner | None = None,
    default_model_id: str | None = None,
    resume_state: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    edges = graph.get('edges') if isinstance(graph.get('edges'), list) else []
    if len(nodes) > 200 or len(edges) > 500:
        raise WorkflowRuntimeError('Workflow exceeds the runtime graph size limit.')
    if sum(1 for node in nodes if isinstance(node, dict) and node_semantic_type(node) in RUNTIME_MODEL_TYPES) > 8:
        raise WorkflowRuntimeError('Workflow exceeds the limit of 8 model execution nodes.')
    unsupported = runtime_unsupported_node_types(graph)
    if unsupported:
        raise WorkflowRuntimeError('Workflow contains nodes without runtime handlers: ' + ', '.join(unsupported))

    workflow_input = normalize_workflow_input(payload)
    ordered_nodes, incoming_sources = _topological_order(graph)
    by_id = {str(node.get('id')): node for node in ordered_nodes}
    incoming_map: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in by_id}
    outgoing_map: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in by_id}
    for index, raw_edge in enumerate(edges):
        if not isinstance(raw_edge, dict):
            continue
        source = str(raw_edge.get('source') or '')
        target = str(raw_edge.get('target') or '')
        if source not in by_id or target not in by_id:
            continue
        edge = {**raw_edge, '_runtime_id': str(raw_edge.get('id') or f'edge-{index}-{source}-{target}')}
        incoming_map[target].append(edge)
        outgoing_map[source].append(edge)
    state = resume_state if isinstance(resume_state, dict) else {}
    results: dict[str, Any] = dict(state.get('results') or {})
    active_edges: dict[str, bool] = {str(key): bool(value) for key, value in (state.get('active_edges') or {}).items()}
    outputs: list[dict[str, Any]] = []
    node_results = list(state.get('node_results') or [])
    total_usage = deepcopy(
        state.get('usage') or {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    )
    execution_usage: dict[str, Any] = {}
    model_calls = 0
    used_model_ids = list(state.get('model_ids') or [])

    def checkpoint_state() -> dict[str, Any]:
        return {
            'results': results,
            'active_edges': active_edges,
            'node_results': node_results,
            'usage': total_usage,
            'execution_usage': execution_usage,
            'model_calls': model_calls,
            'model_ids': used_model_ids,
            'input': workflow_input,
        }

    for node in ordered_nodes:
        node_id = str(node.get('id'))
        node_type = node_semantic_type(node)
        config = _node_config(node)
        if node_id in results:
            continue
        node_edges = incoming_map[node_id]
        incoming_values = [
            results[str(edge.get('source'))]
            for edge in node_edges
            if str(edge.get('source')) in results and active_edges.get(edge['_runtime_id'], True)
        ]
        if node_edges and not incoming_values:
            for edge in outgoing_map[node_id]:
                active_edges[edge['_runtime_id']] = False
            node_results.append({'node_id': node_id, 'node_type': node_type, 'status': 'skipped'})
            continue
        incoming = incoming_values[0] if len(incoming_values) == 1 else incoming_values

        if node_type in RUNTIME_INPUT_TYPES:
            value = workflow_input
        elif node_type == 'system_prompt':
            value = {
                'system': _render_template(str(config.get('text') or ''), workflow_input, incoming),
                'value': incoming,
            }
        elif node_type == 'prompt_template':
            template = str(config.get('template') or '{{input}}')
            value = {'type': 'text', 'text': _render_template(template, workflow_input, incoming)}
        elif node_type in RUNTIME_MODEL_TYPES:
            if model_runner is None:
                raise WorkflowRuntimeError(f'Node {node_id} requires a model runtime.')
            model_id = str(config.get('model_id') or default_model_id or '').strip() or None
            inherited_system = incoming.get('system') if isinstance(incoming, dict) else None
            prompt_input = incoming.get('value') if isinstance(incoming, dict) and 'value' in incoming else incoming
            system_prompt = str(config.get('system_prompt') or inherited_system or '').strip() or None
            prompt = _value_text(prompt_input) or workflow_input['message']
            output_contract = str(config.get('output_contract') or '').strip()
            max_attempts = max(1, min(3, int(config.get('max_attempts') or 1)))
            last_contract_error: WorkflowRuntimeError | None = None
            value = {'type': 'text', 'text': ''}
            for attempt in range(max_attempts):
                retry_prompt = prompt
                if attempt:
                    retry_prompt += (
                        '\n\n前次回應為空白或不符合指定 JSON 契約。'
                        '請重新輸出單一 JSON 物件，不要加入 Markdown、說明文字或額外欄位。'
                    )
                model_result = await model_runner(
                    retry_prompt,
                    system_prompt,
                    model_id,
                    workflow_input['parts'],
                )
                usage = model_result.get('usage') if isinstance(model_result.get('usage'), dict) else {}
                _merge_usage_counts(total_usage, usage)
                _merge_usage_counts(execution_usage, usage)
                model_calls += 1
                resolved_model_id = str(model_result.get('model_id') or model_id or '')
                if resolved_model_id and resolved_model_id not in used_model_ids:
                    used_model_ids.append(resolved_model_id)
                text = str(model_result.get('text') or '').strip()
                diagnostic = str(model_result.get('diagnostic') or '').strip()
                if not output_contract:
                    value = {'type': 'text', 'text': text}
                    break
                if not text:
                    last_contract_error = WorkflowRuntimeError(
                        'Model provider returned no public output text.'
                        + (f' Response shape: {diagnostic}' if diagnostic else '')
                    )
                    continue
                try:
                    parsed = _parse_json_value({'type': 'text', 'text': text})
                    normalized = _normalize_output_contract(parsed, output_contract)
                    value = {
                        'type': 'text',
                        'text': json.dumps(normalized, ensure_ascii=False),
                    }
                    last_contract_error = None
                    break
                except WorkflowRuntimeError as exc:
                    last_contract_error = exc
            if last_contract_error is not None:
                raise WorkflowRuntimeError(
                    f'Model output failed the required contract after {max_attempts} attempts: '
                    f'{last_contract_error}'
                ) from last_contract_error
        elif node_type == 'calculator':
            expression = _render_template(str(config.get('expression') or '{{input}}'), workflow_input, incoming)
            value = {'type': 'text', 'text': str(_safe_calculate(expression))}
        elif node_type == 'transform_json':
            value = config.get('value', incoming)
        elif node_type == 'json_parse':
            value = _normalize_output_contract(
                _parse_json_value(incoming),
                str(config.get('output_contract') or '').strip(),
            )
        elif node_type == 'extract_fields':
            text = _value_text(incoming)
            fields = config.get('fields') if isinstance(config.get('fields'), list) else []
            value = {field: None for field in fields}
            value['_text'] = text
        elif node_type == 'condition':
            matched = _condition_matches(incoming, config)
            value = incoming
            for edge in outgoing_map[node_id]:
                handle = str(edge.get('sourceHandle') or edge.get('source_handle') or '').lower()
                if handle in {'false', 'no', 'unmatched'}:
                    edge_is_active = not matched
                else:
                    edge_is_active = matched
                active_edges[edge['_runtime_id']] = edge_is_active
            value = {'matched': matched, 'value': incoming}
        elif node_type == 'user_choice':
            choices = config.get('choices') if isinstance(config.get('choices'), list) else []
            dynamic_path = str(config.get('choices_from_path') or '').strip()
            dynamic_items = _extract_path(incoming, dynamic_path) if dynamic_path else None
            if isinstance(dynamic_items, list):
                label_path = str(config.get('choice_label_path') or 'label')
                value_path = str(config.get('choice_value_path') or 'value')
                choices = [
                    {
                        'label': str(_extract_path(item, label_path) or _extract_path(item, value_path) or ''),
                        'value': _extract_path(item, value_path),
                        'data': item,
                    }
                    for item in dynamic_items[:20]
                    if isinstance(item, dict) and _extract_path(item, value_path) is not None
                ]
            if resume and str(resume.get('node_id') or '') == node_id:
                if str(resume.get('decision') or '') in {'cancelled', 'rejected'}:
                    raise WorkflowRuntimeError('Workflow was cancelled by the user.')
                selected = resume.get('value')
                allowed = {
                    str(item.get('value'))
                    for item in choices
                    if isinstance(item, dict) and item.get('value') is not None
                }
                if allowed and str(selected) not in allowed:
                    raise WorkflowRuntimeError('Selected workflow option is no longer available.')
                selected_option = next(
                    (item for item in choices if isinstance(item, dict) and str(item.get('value')) == str(selected)),
                    None,
                )
                value = {
                    'choice': selected,
                    'selected': selected_option.get('data') if isinstance(selected_option, dict) else None,
                    'value': incoming,
                }
            else:
                prompt = {
                    'title': str(config.get('title') or '請選擇下一步'),
                    'message': _render_template(str(config.get('message') or ''), workflow_input, incoming),
                    'choices': choices,
                }
                raise WorkflowPause(node_id, 'input', prompt, checkpoint_state())
        elif node_type in {'approval_gate', 'campaign_approval_gate'}:
            payload_hash = _stable_hash(incoming)
            if resume and str(resume.get('node_id') or '') == node_id:
                if str(resume.get('decision') or '') != 'approved':
                    raise WorkflowRuntimeError('Workflow approval was rejected.')
                if str(resume.get('payload_hash') or '') != payload_hash:
                    raise WorkflowRuntimeError('Approval payload changed and must be reviewed again.')
                value = {
                    'value': incoming,
                    '_approval': {
                        'approved': True,
                        'payload_hash': payload_hash,
                        'approved_by': resume.get('actor_id'),
                        'approved_at': resume.get('decided_at'),
                    },
                }
            else:
                fields = config.get('preview_fields') if isinstance(config.get('preview_fields'), list) else []
                preview = {
                    str(field.get('label') or field.get('path')): _extract_path(incoming, str(field.get('path') or ''))
                    for field in fields
                    if isinstance(field, dict) and field.get('path')
                }
                if not preview:
                    preview = incoming if isinstance(incoming, dict) else {'內容': _value_text(incoming)}
                prompt = {
                    'title': str(config.get('title') or '確認後執行'),
                    'message': str(config.get('message') or '請確認收件人與內容。核准後才會執行。'),
                    'preview': preview,
                    'confirm_label': str(config.get('confirm_label') or '確認寄送'),
                    'cancel_label': str(config.get('cancel_label') or '取消'),
                }
                raise WorkflowPause(node_id, 'approval', prompt, checkpoint_state(), payload_hash)
        elif node_type in {
            'web_search',
            'fetch_url',
            'prospect_contact_enrichment',
            'knowledge_query',
            'database_query',
            'semantic_query',
            'structured_extract',
            'customer_contact_lookup',
            'email_compose',
            'email_campaign_compose',
            'email_send',
            'email_campaign_send',
            'email_delivery_status',
        }:
            if node_runner is None:
                raise WorkflowRuntimeError(f'Node {node_id} requires a secure tool runtime.')
            value = await node_runner(
                node_type,
                {**config, '_runtime_node_id': node_id},
                incoming,
                workflow_input,
            )
        elif node_type == 'email_result':
            delivery = incoming.get('value') if isinstance(incoming, dict) and 'value' in incoming else incoming
            status_value = delivery.get('status') if isinstance(delivery, dict) else None
            value = {
                'type': 'text',
                'text': str(config.get('success_text') or '郵件已送出。')
                if status_value in {'sent', 'delivered', 'opened', 'clicked'}
                else str(config.get('pending_text') or '郵件已交由寄送服務處理。'),
                'delivery': delivery,
            }
        elif node_type == 'campaign_delivery_summary':
            delivery = incoming.get('value') if isinstance(incoming, dict) and 'value' in incoming else incoming
            status_value = delivery.get('status') if isinstance(delivery, dict) else None
            value = {
                'type': 'campaign_delivery',
                'ok': status_value in {'sent', 'delivered', 'opened', 'clicked'},
                'status': status_value or 'unknown',
                'delivery': delivery,
            }
        elif node_type in RUNTIME_PASSTHROUGH_TYPES:
            value = incoming
        elif node_type in RUNTIME_OUTPUT_TYPES:
            if node_type == 'handoff':
                node_outputs = [{'type': 'handoff', 'reason': str(config.get('reason') or _value_text(incoming))}]
            elif node_type == 'webhook_response':
                node_outputs = [{'type': 'json', 'value': incoming}]
            elif node_type == 'media_output':
                node_outputs = [
                    part for part in _configured_output(config, incoming) if part.get('type') in MEDIA_TYPES
                ]
            elif node_type == 'file_output':
                node_outputs = [part for part in _configured_output(config, incoming) if part.get('type') == 'file']
            else:
                node_outputs = _configured_output(config, incoming)
            outputs.extend(node_outputs)
            value = {'outputs': node_outputs}
        else:
            raise WorkflowRuntimeError(f'No runtime handler for node type {node_type}.')

        results[node_id] = value
        node_results.append({'node_id': node_id, 'node_type': node_type, 'status': 'success'})

    if not outputs and ordered_nodes:
        sink_ids = {str(node.get('id')) for node in ordered_nodes}
        for sources in incoming_sources.values():
            sink_ids.difference_update(sources)
        nodes_by_id = {str(node.get('id')): node for node in ordered_nodes}
        for sink_id in sink_ids:
            if node_semantic_type(nodes_by_id[sink_id]) == 'user_input':
                continue
            outputs.extend(_value_parts(results.get(sink_id)))

    return {
        'outputs': outputs,
        'nodes': node_results,
        'usage': total_usage,
        'execution_usage': execution_usage,
        'model_calls': model_calls,
        'model_ids': used_model_ids,
        'input': workflow_input,
        'status': 'success',
    }


def workflow_outputs_to_response_items(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for output in outputs:
        output_type = output.get('type')
        if output_type == 'text':
            items.append(
                {
                    'type': 'message',
                    'id': f'wf_msg_{uuid4().hex}',
                    'status': 'completed',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': str(output.get('text') or '')}],
                }
            )
        else:
            items.append(
                {
                    'type': 'open_webui:workflow_output',
                    'id': f'wf_out_{uuid4().hex}',
                    'status': 'completed',
                    'workflow_output': output,
                }
            )
    return items


def workflow_outputs_text(outputs: list[dict[str, Any]]) -> str:
    text_parts = []
    for output in outputs:
        output_type = output.get('type')
        if output_type == 'text':
            text_parts.append(str(output.get('text') or ''))
        elif output_type in MEDIA_TYPES:
            label = output.get('title') or output.get('filename') or output.get('alt') or output_type
            url = output.get('url')
            text_parts.append(f'{label}: {url}' if url else str(label))
        elif output_type == 'card':
            text_parts.append('\n'.join(filter(None, [str(output.get('title') or ''), str(output.get('body') or '')])))
        elif output_type == 'handoff':
            text_parts.append(str(output.get('reason') or 'Transferred to a human operator.'))
        elif output_type == 'json':
            text_parts.append(json.dumps(output.get('value'), ensure_ascii=False))
    return '\n'.join(part for part in text_parts if part)
