import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from open_webui.utils.workflow_runtime import runtime_unsupported_node_types
from open_webui.utils.workflow_launch import normalize_launch_contract, normalize_launch_meta

INPUT_TYPES = {
    'input',
    'chat_input',
    'channel_input',
    'webhook_trigger',
    'schedule_trigger',
    'file_upload',
    'form_input',
}
OUTPUT_TYPES = {
    'output',
    'chat_output',
    'channel_reply',
    'media_output',
    'file_output',
    'handoff',
    'webhook_response',
    'notification',
}
SENSITIVE_NODE_TYPES = {
    'code_interpreter',
    'crm_tool',
    'database_query',
    'semantic_query',
    'knowledge_query',
    'http_request',
    'mcp_tools',
    'ticket_tool',
    'tool_call',
}

ACL_COMPANY_SCOPES = {'company', 'company_members', 'selected_members', 'selected_groups', 'shared'}
DEFAULT_AGENT_SELECTION_THRESHOLD = 0.64
DEFAULT_AGENT_AMBIGUITY_MARGIN = 0.12


@dataclass
class WorkflowAccessContext:
    user_id: str | None = None
    role: str | None = None
    company_user_id: str | None = None
    company_member_id: str | None = None
    company_member_role: str | None = None
    group_ids: set[str] = field(default_factory=set)
    channel_id: str | None = None
    model_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == 'admin'


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ''


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r'[,\r\n]+', value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [_clean_str(item) for item in value if _clean_str(item)]
    return [_clean_str(value)] if _clean_str(value) else []


def _as_set(value: Any) -> set[str]:
    return set(_as_list(value))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(maximum, max(minimum, float(value)))
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_intent_text(value: Any) -> str:
    text = unicodedata.normalize('NFKC', _clean_str(value)).casefold()
    return ' '.join(''.join(character if character.isalnum() else ' ' for character in text).split())


def _compact_intent_text(value: Any) -> str:
    return _normalize_intent_text(value).replace(' ', '')


def _contains_intent_phrase(message: str, phrase: str) -> bool:
    normalized_phrase = _normalize_intent_text(phrase)
    if not normalized_phrase:
        return False

    if re.search(r'[\u3400-\u9fff]', normalized_phrase):
        return normalized_phrase.replace(' ', '') in message.replace(' ', '')

    return bool(re.search(rf'(?<!\w){re.escape(normalized_phrase)}(?!\w)', message))


def _intent_units(value: str) -> set[str]:
    normalized = _normalize_intent_text(value)
    units = set(re.findall(r'[a-z0-9]+', normalized))
    for sequence in re.findall(r'[\u3400-\u9fff]+', normalized):
        if len(sequence) == 1:
            units.add(sequence)
        else:
            units.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return units


def _intent_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_intent_text(left)
    normalized_right = _normalize_intent_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0

    compact_left = normalized_left.replace(' ', '')
    compact_right = normalized_right.replace(' ', '')
    shorter, longer = sorted((compact_left, compact_right), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return min(0.92, 0.72 + (len(shorter) / len(longer)) * 0.2)

    left_units = _intent_units(normalized_left)
    right_units = _intent_units(normalized_right)
    if not left_units or not right_units:
        return 0.0
    return (2 * len(left_units.intersection(right_units))) / (len(left_units) + len(right_units))


def _policy_value(acl: dict[str, Any], agent: dict[str, Any], meta: dict[str, Any], key: str) -> Any:
    if key in acl:
        return acl.get(key)
    if key in agent:
        return agent.get(key)
    return meta.get(key)


def _workflow_meta(workflow: Any) -> dict[str, Any]:
    meta = getattr(workflow, 'meta', None)
    return meta if isinstance(meta, dict) else {}


def workflow_acl(workflow: Any) -> dict[str, Any]:
    acl = _workflow_meta(workflow).get('acl')
    return acl if isinstance(acl, dict) else {}


def normalize_workflow_meta(
    meta: dict[str, Any] | None,
    owner_user_id: str,
    company_user_id: str | None = None,
    visibility: str | None = None,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_launch_meta(meta, graph)
    acl = dict(normalized.get('acl')) if isinstance(normalized.get('acl'), dict) else {}

    scope = _clean_str(acl.get('scope'))
    if not scope:
        scope = 'company' if visibility == 'shared' else 'private'

    acl.update(
        {
            'scope': scope,
            'owner_user_id': owner_user_id,
            'company_user_id': company_user_id or owner_user_id,
            'allow_agent_selection': bool(acl.get('allow_agent_selection', False)),
            'intent_keywords': _as_list(acl.get('intent_keywords')),
            'intent_examples': _as_list(acl.get('intent_examples')),
            'required_keywords': _as_list(acl.get('required_keywords')),
            'negative_keywords': _as_list(acl.get('negative_keywords')),
            'agent_selection_threshold': _as_float(
                acl.get('agent_selection_threshold'),
                DEFAULT_AGENT_SELECTION_THRESHOLD,
                0.5,
                0.95,
            ),
            'agent_selection_priority': _as_int(acl.get('agent_selection_priority'), 0, -100, 100),
            'agent_ambiguity_margin': _as_float(
                acl.get('agent_ambiguity_margin'),
                DEFAULT_AGENT_AMBIGUITY_MARGIN,
                0.05,
                0.3,
            ),
            'allowed_company_user_ids': _as_list(acl.get('allowed_company_user_ids')),
            'allowed_member_ids': _as_list(acl.get('allowed_member_ids')),
            'allowed_group_ids': _as_list(acl.get('allowed_group_ids')),
            'allowed_channel_ids': _as_list(acl.get('allowed_channel_ids')),
            'allowed_model_ids': _as_list(acl.get('allowed_model_ids')),
        }
    )

    normalized['acl'] = acl
    return normalized


def workflow_sensitive_node_types(graph: dict[str, Any]) -> list[str]:
    nodes = graph.get('nodes') if isinstance(graph, dict) else []
    if not isinstance(nodes, list):
        return []
    return sorted(
        {_node_type(node) for node in nodes if isinstance(node, dict) and _node_type(node) in SENSITIVE_NODE_TYPES}
    )


def workflow_acl_allows(
    workflow: Any,
    context: WorkflowAccessContext,
    allow_public_template: bool = True,
) -> bool:
    if not workflow:
        return False
    if context.is_admin or (context.user_id and context.user_id == getattr(workflow, 'user_id', None)):
        return True
    if allow_public_template and getattr(workflow, 'visibility', None) == 'public_template':
        return True
    if getattr(workflow, 'visibility', None) != 'shared':
        return False

    acl = workflow_acl(workflow)
    scope = _clean_str(acl.get('scope')) or (
        'company' if getattr(workflow, 'visibility', None) == 'shared' else 'private'
    )
    if scope in {'private', 'owner'}:
        return False

    allowed_channels = _as_set(acl.get('allowed_channel_ids'))
    if allowed_channels and (not context.channel_id or context.channel_id not in allowed_channels):
        return False

    allowed_models = _as_set(acl.get('allowed_model_ids'))
    if allowed_models and (not context.model_id or context.model_id not in allowed_models):
        return False

    if scope in ACL_COMPANY_SCOPES:
        owner_company_id = _clean_str(acl.get('company_user_id'))
        allowed_company_ids = _as_set(acl.get('allowed_company_user_ids'))
        company_matches = bool(
            context.company_user_id
            and (context.company_user_id == owner_company_id or context.company_user_id in allowed_company_ids)
        )
        if not company_matches:
            return False

    if scope == 'selected_members':
        allowed_members = _as_set(acl.get('allowed_member_ids'))
        if allowed_members and context.company_member_id in allowed_members:
            return True
        return False

    if scope == 'selected_groups':
        allowed_groups = _as_set(acl.get('allowed_group_ids'))
        if allowed_groups and context.group_ids.intersection(allowed_groups):
            return True
        return False

    return scope in ACL_COMPANY_SCOPES


def workflow_agent_candidate(
    workflow: Any,
    context: WorkflowAccessContext,
    message: str,
) -> dict[str, Any] | None:
    if not workflow_acl_allows(workflow, context, allow_public_template=False):
        return None
    if getattr(workflow, 'status', None) != 'published' or not getattr(workflow, 'default_version_id', None):
        return None
    graph = getattr(workflow, 'graph', None)
    if not isinstance(graph, dict) or runtime_unsupported_node_types(graph):
        return None

    launch = normalize_launch_contract(_workflow_meta(workflow), graph)
    if launch['mode'] in {'form_input', 'file_input'}:
        return None

    meta = _workflow_meta(workflow)
    acl = workflow_acl(workflow)
    agent = meta.get('agent') if isinstance(meta.get('agent'), dict) else {}
    is_enabled = bool(acl.get('allow_agent_selection') or agent.get('enabled') or agent.get('allow_selection'))
    if not is_enabled:
        return None

    keywords = _as_list(_policy_value(acl, agent, meta, 'intent_keywords'))
    examples = _as_list(_policy_value(acl, agent, meta, 'intent_examples'))
    required_keywords = _as_list(_policy_value(acl, agent, meta, 'required_keywords'))
    negative_keywords = _as_list(_policy_value(acl, agent, meta, 'negative_keywords'))
    if not keywords and not examples:
        return None

    normalized_message = _normalize_intent_text(message)
    if not normalized_message:
        return None

    matched_negative_keywords = [
        keyword for keyword in negative_keywords if _contains_intent_phrase(normalized_message, keyword)
    ]
    if matched_negative_keywords:
        return None

    matched_required_keywords = [
        keyword for keyword in required_keywords if _contains_intent_phrase(normalized_message, keyword)
    ]
    if required_keywords and len(matched_required_keywords) != len(required_keywords):
        return None

    matched_keywords = [keyword for keyword in keywords if _contains_intent_phrase(normalized_message, keyword)]
    usable_keyword_matches = [keyword for keyword in matched_keywords if len(_compact_intent_text(keyword)) >= 2]

    keyword_score = 0.0
    if usable_keyword_matches:
        longest = max(len(_compact_intent_text(keyword)) for keyword in usable_keyword_matches)
        keyword_score = 0.64 if longest == 2 else 0.7 if longest == 3 else 0.76 if longest < 8 else 0.84
        keyword_score = min(0.9, keyword_score + min(0.12, (len(usable_keyword_matches) - 1) * 0.04))

    example_matches = sorted(
        (
            {
                'example': example,
                'similarity': round(_intent_similarity(normalized_message, example), 4),
            }
            for example in examples
        ),
        key=lambda item: item['similarity'],
        reverse=True,
    )
    matched_examples = [item for item in example_matches if item['similarity'] >= 0.45][:3]
    example_score = 0.0
    if matched_examples:
        example_score = min(0.96, 0.48 + matched_examples[0]['similarity'] * 0.48)

    name_score = 0.0
    workflow_name = _clean_str(getattr(workflow, 'name', ''))
    if len(_compact_intent_text(workflow_name)) >= 4 and _contains_intent_phrase(normalized_message, workflow_name):
        name_score = 0.86

    score = max(keyword_score, example_score, name_score)
    signal_count = sum(value > 0 for value in (keyword_score, example_score, name_score))
    if signal_count >= 2:
        score = min(1.0, score + 0.06)
    if matched_required_keywords:
        score = min(1.0, score + 0.03)

    threshold = _as_float(
        _policy_value(acl, agent, meta, 'agent_selection_threshold'),
        DEFAULT_AGENT_SELECTION_THRESHOLD,
        0.5,
        0.95,
    )
    if score < threshold:
        return None

    priority = _as_int(_policy_value(acl, agent, meta, 'agent_selection_priority'), 0, -100, 100)
    ambiguity_margin = _as_float(
        _policy_value(acl, agent, meta, 'agent_ambiguity_margin'),
        DEFAULT_AGENT_AMBIGUITY_MARGIN,
        0.05,
        0.3,
    )
    reasons = []
    if usable_keyword_matches:
        reasons.append('explicit intent phrase')
    if matched_examples:
        reasons.append('similar example utterance')
    if name_score:
        reasons.append('explicit workflow name')

    return {
        'id': getattr(workflow, 'id', ''),
        'name': getattr(workflow, 'name', ''),
        'description': getattr(workflow, 'description', None),
        'visibility': getattr(workflow, 'visibility', ''),
        'default_version_id': getattr(workflow, 'default_version_id', None),
        'score': round(score, 4),
        'confidence': round(score, 4),
        'threshold': threshold,
        'priority': priority,
        'ambiguity_margin': ambiguity_margin,
        'matched_keywords': usable_keyword_matches,
        'matched_required_keywords': matched_required_keywords,
        'matched_examples': matched_examples,
        'reason': 'Matched ' + ', '.join(reasons) + '.',
    }


def decide_workflow_candidates(candidates: list[dict[str, Any]], max_items: int = 5) -> dict[str, Any]:
    ranked = sorted(
        candidates,
        key=lambda item: (item['score'], item.get('priority', 0), item.get('name', '')),
        reverse=True,
    )[:max_items]
    if not ranked:
        return {
            'decision': 'none',
            'action': 'continue_chat',
            'selected_workflow_id': None,
            'selected_version_id': None,
            'needs_confirmation': False,
            'reason': 'No published and authorized workflow passed its intent threshold.',
            'items': [],
        }

    top = ranked[0]
    if len(ranked) > 1:
        runner_up = ranked[1]
        ambiguity_margin = max(top['ambiguity_margin'], runner_up['ambiguity_margin'])
        if top['score'] - runner_up['score'] < ambiguity_margin:
            return {
                'decision': 'ambiguous',
                'action': 'ask_user',
                'selected_workflow_id': None,
                'selected_version_id': None,
                'needs_confirmation': True,
                'reason': 'The top workflow candidates are too close to choose safely.',
                'items': ranked,
            }

    return {
        'decision': 'selected',
        'action': 'execute_workflow',
        'selected_workflow_id': top['id'],
        'selected_version_id': top['default_version_id'],
        'needs_confirmation': False,
        'reason': 'One authorized workflow passed the confidence and ambiguity checks.',
        'items': ranked,
    }


def _node_type(node: dict[str, Any]) -> str:
    data = node.get('data') if isinstance(node.get('data'), dict) else {}
    semantic_type = data.get('type') or data.get('kind')
    if semantic_type:
        return str(semantic_type).strip()
    return str(node.get('type') or '').strip()


def validate_workflow_graph(graph: dict[str, Any]) -> dict[str, list[str] | bool]:
    errors: list[str] = []
    warnings: list[str] = []

    nodes = graph.get('nodes')
    edges = graph.get('edges')

    if not isinstance(nodes, list):
        errors.append('Graph nodes must be a list.')
        nodes = []
    if not isinstance(edges, list):
        errors.append('Graph edges must be a list.')
        edges = []
    if len(nodes) > 200:
        errors.append('Workflow graphs are limited to 200 nodes.')
    if len(edges) > 500:
        errors.append('Workflow graphs are limited to 500 edges.')
    model_node_types = {
        'agent',
        'supervisor_agent',
        'worker_agent',
        'planner',
        'evaluator',
        'chat_model',
        'vision_model',
    }
    if sum(1 for node in nodes if isinstance(node, dict) and _node_type(node) in model_node_types) > 8:
        errors.append('Workflows are limited to 8 model execution nodes.')

    node_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            errors.append('Every node must be an object.')
            continue
        node_id = str(node.get('id') or '').strip()
        if not node_id:
            errors.append('Every node needs an id.')
            continue
        if node_id in node_ids:
            duplicate_ids.add(node_id)
        node_ids.add(node_id)
        if _node_type(node) == 'database_query':
            data = node.get('data') if isinstance(node.get('data'), dict) else {}
            config = data.get('config') if isinstance(data.get('config'), dict) else {}
            if not _clean_str(config.get('table')):
                errors.append(f'Database query node {node_id} requires an allowed table name.')
            if _clean_str(config.get('operation') or 'select').lower() not in {'select', 'count'}:
                errors.append(f'Database query node {node_id} only supports select or count.')
        if _node_type(node) == 'semantic_query':
            data = node.get('data') if isinstance(node.get('data'), dict) else {}
            config = data.get('config') if isinstance(data.get('config'), dict) else {}
            plan = config.get('plan') if isinstance(config.get('plan'), dict) else {}
            if not _clean_str(config.get('dataset_id') or plan.get('datasetId')):
                errors.append(f'Semantic query node {node_id} requires a published dataset id.')

    for node_id in sorted(duplicate_ids):
        errors.append(f'Duplicate node id: {node_id}.')

    has_input = any(_node_type(node) in INPUT_TYPES for node in nodes if isinstance(node, dict))
    has_output = any(_node_type(node) in OUTPUT_TYPES for node in nodes if isinstance(node, dict))
    if nodes and not has_input:
        warnings.append('No input or trigger node found. The workflow may not know how to start.')
    if nodes and not has_output:
        warnings.append('No output node found. The workflow may run without a user-visible response.')

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append('Every edge must be an object.')
            continue
        source = str(edge.get('source') or '').strip()
        target = str(edge.get('target') or '').strip()
        if source not in node_ids:
            errors.append(f'Edge references missing source node: {source or "(empty)"}.')
        if target not in node_ids:
            errors.append(f'Edge references missing target node: {target or "(empty)"}.')
        if source in node_ids and target in node_ids:
            adjacency[source].append(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False

        visiting.add(node_id)
        for child_id in adjacency.get(node_id, []):
            if visit(child_id):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(visit(node_id) for node_id in node_ids):
        errors.append('Workflow graph cannot contain cycles.')

    sensitive_nodes = workflow_sensitive_node_types(graph)
    if sensitive_nodes:
        warnings.append(
            'Sensitive tool or data nodes found: '
            + ', '.join(sensitive_nodes)
            + '. Keep this workflow private unless a scoped company/group access policy is configured.'
        )

    return {'ok': len(errors) == 0, 'errors': errors, 'warnings': warnings}


def validate_workflow_visibility(
    graph: dict[str, Any],
    visibility: str | None,
    meta: dict[str, Any] | None = None,
) -> list[str]:
    if visibility not in {'shared', 'public_template'}:
        return []

    sensitive_nodes = workflow_sensitive_node_types(graph)
    if not sensitive_nodes:
        return []

    if visibility == 'shared':
        acl = meta.get('acl') if isinstance(meta, dict) and isinstance(meta.get('acl'), dict) else {}
        scope = _clean_str(acl.get('scope'))
        company_id = _clean_str(acl.get('company_user_id')) or bool(_as_list(acl.get('allowed_company_user_ids')))
        if scope in ACL_COMPANY_SCOPES and company_id:
            return []

    return [
        'Sensitive tool or data workflows must stay private or use scoped company ACL before sharing. '
        f'Found: {", ".join(sensitive_nodes)}.'
    ]


def validate_workflow_agent_policy(
    meta: dict[str, Any] | None,
    visibility: str | None,
) -> dict[str, list[str] | bool]:
    normalized_meta = meta if isinstance(meta, dict) else {}
    acl = normalized_meta.get('acl') if isinstance(normalized_meta.get('acl'), dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    scope = _clean_str(acl.get('scope')) or ('company' if visibility == 'shared' else 'private')

    if visibility == 'shared' and scope in {'private', 'owner'}:
        errors.append('Shared visibility requires a company, member, or group access scope.')
    if visibility == 'public_template' and acl.get('allow_agent_selection'):
        errors.append(
            'Public templates cannot be selected automatically. Clone the template into a private or shared workflow.'
        )
    if scope == 'selected_members' and not _as_list(acl.get('allowed_member_ids')):
        errors.append('Selected-member scope requires at least one allowed member ID.')
    if scope == 'selected_groups' and not _as_list(acl.get('allowed_group_ids')):
        errors.append('Selected-group scope requires at least one allowed group ID.')

    if acl.get('allow_agent_selection'):
        keywords = _as_list(acl.get('intent_keywords'))
        examples = _as_list(acl.get('intent_examples'))
        if not keywords and not examples:
            errors.append('Agent selection requires intent keywords or example utterances.')

        short_keywords = [keyword for keyword in keywords if len(_compact_intent_text(keyword)) < 2]
        if short_keywords:
            errors.append(
                'Intent keywords must contain at least two letters or characters: ' + ', '.join(short_keywords)
            )

        duplicate_policy_terms = sorted(
            set(_as_list(acl.get('required_keywords'))).intersection(_as_list(acl.get('negative_keywords')))
        )
        if duplicate_policy_terms:
            errors.append('Required and negative keywords cannot overlap: ' + ', '.join(duplicate_policy_terms))

        if len(keywords) < 2 and len(examples) < 2:
            warnings.append('Add at least two intent phrases or example utterances to reduce missed selections.')
        if not _as_list(acl.get('negative_keywords')):
            warnings.append('Add negative keywords when another workflow handles a similar intent.')

    return {'ok': len(errors) == 0, 'errors': errors, 'warnings': warnings}


async def execute_workflow_stub(workflow: Any, payload: dict[str, Any]) -> dict[str, Any]:
    graph = workflow.graph if isinstance(workflow.graph, dict) else {}
    validation = validate_workflow_graph(graph)
    if not validation['ok']:
        raise ValueError('; '.join(validation['errors']))

    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    edges = graph.get('edges') if isinstance(graph.get('edges'), list) else []

    return {
        'mode': 'validation_only',
        'message': 'Workflow graph validation completed. No workflow nodes were executed.',
        'workflow_id': workflow.id,
        'workflow_name': workflow.name,
        'node_count': len(nodes),
        'edge_count': len(edges),
        'input': payload,
    }
