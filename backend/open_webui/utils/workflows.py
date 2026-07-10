from typing import Any


INPUT_TYPES = {'input', 'chat_input', 'channel_input', 'webhook_trigger', 'schedule_trigger'}
OUTPUT_TYPES = {'output', 'chat_output', 'channel_reply', 'media_output', 'handoff'}


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

    return {'ok': len(errors) == 0, 'errors': errors, 'warnings': warnings}


async def execute_workflow_stub(workflow: Any, payload: dict[str, Any]) -> dict[str, Any]:
    graph = workflow.graph if isinstance(workflow.graph, dict) else {}
    validation = validate_workflow_graph(graph)
    if not validation['ok']:
        raise ValueError('; '.join(validation['errors']))

    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    edges = graph.get('edges') if isinstance(graph.get('edges'), list) else []

    return {
        'message': 'Workflow accepted the input. Execution engine integration is ready to be wired next.',
        'workflow_id': workflow.id,
        'workflow_name': workflow.name,
        'node_count': len(nodes),
        'edge_count': len(edges),
        'input': payload,
    }
