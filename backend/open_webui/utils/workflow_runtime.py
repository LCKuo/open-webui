import ast
import hashlib
import json
import operator
import re
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
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
        'extract_fields',
        'structured_extract',
        'knowledge_query',
        'database_query',
        'semantic_query',
        'customer_contact_lookup',
        'condition',
        'user_choice',
        'approval_gate',
        'email_compose',
        'email_send',
        'email_delivery_status',
        'email_result',
    }
)

MEDIA_TYPES = {'image', 'audio', 'video', 'file'}
OUTPUT_TYPES = MEDIA_TYPES | {'text', 'card', 'handoff', 'json'}


class WorkflowRuntimeError(RuntimeError):
    pass


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
    total_usage = dict(state.get('usage') or {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
    used_model_ids = list(state.get('model_ids') or [])

    def checkpoint_state() -> dict[str, Any]:
        return {
            'results': results,
            'active_edges': active_edges,
            'node_results': node_results,
            'usage': total_usage,
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
            model_result = await model_runner(prompt, system_prompt, model_id, workflow_input['parts'])
            value = {'type': 'text', 'text': str(model_result.get('text') or '')}
            usage = model_result.get('usage') if isinstance(model_result.get('usage'), dict) else {}
            for key in total_usage:
                total_usage[key] += int(usage.get(key) or 0)
            resolved_model_id = str(model_result.get('model_id') or model_id or '')
            if resolved_model_id and resolved_model_id not in used_model_ids:
                used_model_ids.append(resolved_model_id)
        elif node_type == 'calculator':
            expression = _render_template(str(config.get('expression') or '{{input}}'), workflow_input, incoming)
            value = {'type': 'text', 'text': str(_safe_calculate(expression))}
        elif node_type == 'transform_json':
            value = config.get('value', incoming)
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
        elif node_type == 'approval_gate':
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
            'knowledge_query',
            'database_query',
            'semantic_query',
            'structured_extract',
            'customer_contact_lookup',
            'email_compose',
            'email_send',
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
