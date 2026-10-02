import copy
import json


def merge_tool_call_delta(calls: list[dict], delta: dict) -> None:
    """Merge streamed calls, including providers that identify calls without an index."""
    delta = copy.deepcopy(delta)
    index = delta.get('index')
    call_id = delta.get('id')
    by_id = next((call for call in calls if call_id and call.get('id') == call_id), None)
    if index is None:
        if by_id is not None:
            index = by_id['index']
        elif call_id or not calls:
            index = max((call['index'] for call in calls), default=-1) + 1
        elif len(calls) == 1:
            index = calls[0]['index']
        else:
            raise ValueError('Ambiguous streamed tool call: missing both index and id')
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError('Invalid streamed tool call index')
    current = next((call for call in calls if call['index'] == index), None)
    if by_id is not None and by_id is not current:
        raise ValueError('Conflicting streamed tool call index and id')
    if current is not None and call_id and current.get('id') not in (None, '', call_id):
        raise ValueError('Conflicting streamed tool call ids')

    function = delta.get('function') or {}
    arguments = function.get('arguments')
    if arguments is not None and not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    if current is None:
        delta['index'] = index
        delta['function'] = {**function, 'name': function.get('name') or '', 'arguments': arguments or ''}
        calls.append(delta)
        return

    if call_id:
        current['id'] = call_id
    if delta.get('type'):
        current['type'] = delta['type']
    current.setdefault('function', {})
    if function.get('name'):
        current['function']['name'] = function['name']
    if arguments is not None:
        current['function']['arguments'] = current['function'].get('arguments', '') + arguments

    # Opaque provider metadata (e.g. Gemini thought signatures) must survive the next tool turn.
    def merge_metadata(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge_metadata(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    if isinstance(delta.get('extra_content'), dict):
        merge_metadata(current.setdefault('extra_content', {}), delta['extra_content'])
