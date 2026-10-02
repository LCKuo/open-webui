import copy
import json

import pytest

from open_webui.utils.tool_call_stream import merge_tool_call_delta
from open_webui.utils.misc import convert_output_to_messages


def test_gemini_unindexed_call_and_signature_round_trip():
    calls = []
    delta = {'id': 'gemini', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{"name":"QA"}'},
             'extra_content': {'google': {'thought_signature': 'opaque-test-signature'}}}
    original = copy.deepcopy(delta)
    merge_tool_call_delta(calls, delta)
    assert delta == original
    assert calls[0]['index'] == 0
    messages = convert_output_to_messages([
        {'type': 'function_call', 'call_id': calls[0]['id'], **calls[0]['function'], 'extra_content': calls[0]['extra_content']},
        {'type': 'function_call_output', 'call_id': 'gemini', 'output': [{'type': 'input_text', 'text': 'QA found'}]},
    ])
    assert messages[0]['tool_calls'][0]['extra_content'] == delta['extra_content']
    assert messages[1]['tool_call_id'] == 'gemini'
    messages[0]['tool_calls'][0]['extra_content']['google']['thought_signature'] = 'changed'
    assert calls[0]['extra_content'] == original['extra_content']


def test_standard_indexed_fragments_and_late_metadata():
    calls = []
    merge_tool_call_delta(calls, {'index': 0, 'id': 'a', 'function': {'name': 'lookup', 'arguments': '{"q":'}})
    merge_tool_call_delta(calls, {'index': 0, 'function': {'arguments': '"test"}'}, 'extra_content': {'google': {'thought_signature': 's'}}})
    merge_tool_call_delta(calls, {'index': 0, 'extra_content': {'google': {'other': 'x'}}})
    assert json.loads(calls[0]['function']['arguments']) == {'q': 'test'}
    assert calls[0]['extra_content']['google'] == {'thought_signature': 's', 'other': 'x'}


def test_multiple_unindexed_calls_route_fragments_by_id():
    calls = []
    for key in ['a', 'b']:
        merge_tool_call_delta(calls, {'id': key, 'function': {'name': 'lookup', 'arguments': '{'}})
    merge_tool_call_delta(calls, {'id': 'b', 'function': {'arguments': '"b":2}'}})
    merge_tool_call_delta(calls, {'id': 'a', 'function': {'arguments': '"a":1}'}})
    assert [call['index'] for call in calls] == [0, 1]
    assert [json.loads(call['function']['arguments']) for call in calls] == [{'a': 1}, {'b': 2}]


def test_single_call_can_continue_without_index_or_id():
    calls = []
    merge_tool_call_delta(calls, {'index': 4, 'id': 'a', 'function': {'arguments': '{'}})
    merge_tool_call_delta(calls, {'function': {'arguments': '}'}})
    merge_tool_call_delta(calls, {'id': 'b', 'function': {'arguments': {'b': 2}}})
    assert calls[0]['function']['arguments'] == '{}'
    assert calls[1]['index'] == 5
    assert json.loads(calls[1]['function']['arguments']) == {'b': 2}


@pytest.mark.parametrize('delta', [
    {'function': {'arguments': '}'}},
    {'index': 0, 'id': 'b'},
    {'index': 2, 'id': 'a'},
    {'index': -1}, {'index': '0'}, {'index': True},
])
def test_ambiguous_or_conflicting_deltas_are_not_silently_executed(delta):
    calls = [{'index': 0, 'id': 'a', 'function': {'arguments': '{'}}, {'index': 1, 'id': 'b', 'function': {'arguments': '{'}}]
    original = copy.deepcopy(calls)
    with pytest.raises(ValueError):
        merge_tool_call_delta(calls, delta)
    assert calls == original


def test_generic_provider_does_not_get_extra_fields():
    messages = convert_output_to_messages([
        {'type': 'function_call', 'call_id': 'a', 'name': 'lookup', 'arguments': {}},
        {'type': 'function_call_output', 'call_id': 'a', 'output': [{'type': 'input_text', 'text': 'ok'}]},
    ])
    assert 'extra_content' not in messages[0]['tool_calls'][0]
