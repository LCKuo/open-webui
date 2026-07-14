import ast
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
        'database_query',
        'semantic_query',
    }
)

MEDIA_TYPES = {'image', 'audio', 'video', 'file'}
OUTPUT_TYPES = MEDIA_TYPES | {'text', 'card', 'handoff', 'json'}


class WorkflowRuntimeError(RuntimeError):
    pass


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
    for part in source.get('parts') or []:
        if isinstance(part, dict) and part.get('type') in OUTPUT_TYPES | {'location', 'postback'}:
            parts.append(dict(part))

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
        parts.append(
            {
                'type': part_type,
                'fileId': file_item.get('id') or file_item.get('fileId'),
                'url': file_item.get('url'),
                'filename': file_item.get('name') or file_item.get('filename'),
                'mimeType': mime_type or None,
                'size': file_item.get('size'),
            }
        )

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
        return str(values.get(key, match.group(0)))

    return re.sub(r'\{\{\s*([^{}]+?)\s*\}\}', replace, template)


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
    ordered_nodes, incoming_map = _topological_order(graph)
    results: dict[str, Any] = {}
    outputs: list[dict[str, Any]] = []
    node_results = []
    total_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    used_model_ids = []

    for node in ordered_nodes:
        node_id = str(node.get('id'))
        node_type = node_semantic_type(node)
        config = _node_config(node)
        incoming_values = [results[source] for source in incoming_map[node_id] if source in results]
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
        elif node_type in {'database_query', 'semantic_query'}:
            if node_runner is None:
                raise WorkflowRuntimeError(f'Node {node_id} requires a database runtime.')
            value = await node_runner(node_type, config, incoming, workflow_input)
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
        for sources in incoming_map.values():
            sink_ids.difference_update(sources)
        for sink_id in sink_ids:
            outputs.extend(_value_parts(results.get(sink_id)))

    return {
        'outputs': outputs,
        'nodes': node_results,
        'usage': total_usage,
        'model_ids': used_model_ids,
        'input': workflow_input,
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
