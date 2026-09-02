import json
from numbers import Number
from uuid import uuid4

from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import (
    openai_chat_chunk_message_template,
    openai_chat_completion_message_template,
)


# An honest ledger is worth more than a flattering one.
# Let every cost here be counted true.
def normalize_usage(usage: dict) -> dict:
    """
    Normalize usage statistics to standard format.
    Handles OpenAI, Gemini, Ollama, and llama.cpp formats, including
    separately reported reasoning/compute tokens.

    Adds standardized token fields to the original data:
    - input_tokens: Number of tokens in the prompt
    - output_tokens: Number of tokens generated
    - compute_tokens: Number of separately reported reasoning tokens
    - total_tokens: Provider total, or the sum of input, output, and compute
    """
    if not usage:
        return {}

    # Map various field names to standard names
    input_tokens = (
        usage.get('input_tokens')  # Already standard
        or usage.get('prompt_tokens')  # OpenAI
        or usage.get('promptTokenCount')  # Gemini
        or usage.get('prompt_eval_count')  # Ollama
        or usage.get('prompt_n')  # llama.cpp
        or 0
    )

    output_tokens = (
        usage.get('output_tokens')  # Already standard
        or usage.get('completion_tokens')  # OpenAI
        or usage.get('candidatesTokenCount')  # Gemini
        or usage.get('eval_count')  # Ollama
        or usage.get('predicted_n')  # llama.cpp
        or 0
    )

    compute_tokens = (
        usage.get('compute_tokens')
        or usage.get('thoughts_token_count')
        or usage.get('thoughtsTokenCount')
        or usage.get('thoughts_tokens')
        or usage.get('reasoning_tokens')
        or 0
    )
    total_tokens = (
        usage.get('total_tokens')
        or usage.get('totalTokenCount')
        or usage.get('total_token_count')
        or (input_tokens + output_tokens + compute_tokens)
    )

    # Add standardized fields to original data
    result = dict(usage)
    result['input_tokens'] = int(input_tokens)
    result['output_tokens'] = int(output_tokens)
    result['compute_tokens'] = int(compute_tokens)
    result['total_tokens'] = int(total_tokens)
    result.setdefault('measurement', 'provider')

    return result


USAGE_TOKEN_KEYS = {
    'input_tokens',
    'output_tokens',
    'compute_tokens',
    'total_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
    'tool_use_input_tokens',
    'toolUsePromptTokenCount',
}

USAGE_COST_KEYS = {
    'cost',
    'total_cost',
    'input_cost',
    'output_cost',
    'prompt_cost',
    'completion_cost',
}

USAGE_SUMMABLE_KEYS = USAGE_TOKEN_KEYS | USAGE_COST_KEYS

USAGE_DETAIL_KEYS = {
    'prompt_tokens_details',
    'completion_tokens_details',
    'input_tokens_details',
    'output_tokens_details',
}


def _is_numeric_usage_value(value) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def _merge_numeric_usage_map(current: dict | None, incoming: dict | None) -> dict:
    current = current or {}
    incoming = incoming or {}
    result = {**current, **incoming}

    for key in set(current) | set(incoming):
        current_value = current.get(key, 0)
        incoming_value = incoming.get(key, 0)
        if isinstance(current_value, dict) or isinstance(incoming_value, dict):
            result[key] = _merge_numeric_usage_map(
                current_value if isinstance(current_value, dict) else {},
                incoming_value if isinstance(incoming_value, dict) else {},
            )
        elif _is_numeric_usage_value(current_value) or _is_numeric_usage_value(incoming_value):
            result[key] = (current_value if _is_numeric_usage_value(current_value) else 0) + (
                incoming_value if _is_numeric_usage_value(incoming_value) else 0
            )

    return result


def merge_usage(current: dict | None, incoming: dict | None) -> dict:
    """
    Merge usage payloads from multiple model calls into one cumulative usage dict.

    Canonical token fields are additive; provider aliases keep the latest value.
    """
    current_usage = normalize_usage(current or {}) if current else {}
    incoming_usage = normalize_usage(incoming or {}) if incoming else {}

    if not incoming_usage:
        return current_usage
    if not current_usage:
        return incoming_usage

    result = {**current_usage, **incoming_usage}

    for key in USAGE_SUMMABLE_KEYS:
        if key in current_usage or key in incoming_usage:
            current_value = current_usage.get(key, 0)
            incoming_value = incoming_usage.get(key, 0)
            if _is_numeric_usage_value(current_value) or _is_numeric_usage_value(incoming_value):
                result[key] = (current_value if _is_numeric_usage_value(current_value) else 0) + (
                    incoming_value if _is_numeric_usage_value(incoming_value) else 0
                )

    for key in USAGE_DETAIL_KEYS:
        if isinstance(current_usage.get(key), dict) or isinstance(incoming_usage.get(key), dict):
            result[key] = _merge_numeric_usage_map(
                current_usage.get(key) if isinstance(current_usage.get(key), dict) else {},
                incoming_usage.get(key) if isinstance(incoming_usage.get(key), dict) else {},
            )

    result['prompt_tokens'] = (
        incoming_usage.get('prompt_tokens')
        or incoming_usage.get('input_tokens')
        or current_usage.get('prompt_tokens', 0)
    )
    result['completion_tokens'] = (
        incoming_usage.get('completion_tokens')
        or incoming_usage.get('output_tokens')
        or current_usage.get('completion_tokens', 0)
    )
    measurements = {
        str(value)
        for value in (current_usage.get('measurement'), incoming_usage.get('measurement'))
        if value
    }
    if measurements:
        result['measurement'] = next(iter(measurements)) if len(measurements) == 1 else 'mixed'

    return result


def record_auxiliary_usage(request, response, form_data: dict | None = None):
    """Attach an internal model call's usage to the active parent billing request."""
    state = getattr(request, 'state', None)
    if not state or not getattr(state, 'interact_billing_parent_active', False):
        return response

    response_data = response if isinstance(response, dict) else None
    if response_data is None and getattr(response, 'body', None):
        try:
            decoded = json.loads(response.body.decode('utf-8', 'replace'))
            response_data = decoded if isinstance(decoded, dict) else None
        except (AttributeError, TypeError, ValueError):
            response_data = None

    usage = response_data.get('usage') if isinstance(response_data, dict) else None
    if (not isinstance(usage, dict) or not usage) and form_data:
        from open_webui.utils.interact_billing import estimate_prompt_tokens, estimate_text_tokens

        output_value = None
        if isinstance(response_data, dict):
            choices = response_data.get('choices')
            if isinstance(choices, list) and choices:
                message = choices[0].get('message') if isinstance(choices[0], dict) else None
                output_value = message.get('content') if isinstance(message, dict) else None
            if output_value is None:
                output_value = response_data.get('output')
        estimated_input = estimate_prompt_tokens(form_data.get('messages') or [])
        estimated_output = estimate_text_tokens(output_value)
        usage = {
            'input_tokens': estimated_input,
            'output_tokens': estimated_output,
            'total_tokens': estimated_input + estimated_output,
            'measurement': 'estimated',
        }
    if isinstance(usage, dict) and usage:
        state.interact_billing_aux_usage = merge_usage(
            getattr(state, 'interact_billing_aux_usage', None),
            usage,
        )
    return response


def convert_ollama_tool_call_to_openai(tool_calls: list) -> list:
    openai_tool_calls = []
    for tool_call in tool_calls:
        function = tool_call.get('function', {})
        openai_tool_call = {
            'index': tool_call.get('index', function.get('index', 0)),
            'id': tool_call.get('id', f'call_{str(uuid4())}'),
            'type': 'function',
            'function': {
                'name': function.get('name', ''),
                'arguments': json.dumps(function.get('arguments', {})),
            },
        }
        openai_tool_calls.append(openai_tool_call)
    return openai_tool_calls


def convert_ollama_usage_to_openai(data: dict) -> dict:
    input_tokens = int(data.get('prompt_eval_count', 0))
    output_tokens = int(data.get('eval_count', 0))
    total_tokens = input_tokens + output_tokens

    return {
        # Standardized fields
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': total_tokens,
        # OpenAI-compatible fields (for backward compatibility)
        'prompt_tokens': input_tokens,
        'completion_tokens': output_tokens,
        # Ollama-specific metrics
        'response_token/s': (
            round(
                ((data.get('eval_count', 0) / (data.get('eval_duration', 0) / 10_000_000)) * 100),
                2,
            )
            if data.get('eval_duration', 0) > 0
            else 'N/A'
        ),
        'prompt_token/s': (
            round(
                ((data.get('prompt_eval_count', 0) / (data.get('prompt_eval_duration', 0) / 10_000_000)) * 100),
                2,
            )
            if data.get('prompt_eval_duration', 0) > 0
            else 'N/A'
        ),
        'total_duration': data.get('total_duration', 0),
        'load_duration': data.get('load_duration', 0),
        'prompt_eval_count': data.get('prompt_eval_count', 0),
        'prompt_eval_duration': data.get('prompt_eval_duration', 0),
        'eval_count': data.get('eval_count', 0),
        'eval_duration': data.get('eval_duration', 0),
        'approximate_total': (lambda s: f'{s // 3600}h{(s % 3600) // 60}m{s % 60}s')(
            (data.get('total_duration', 0) or 0) // 1_000_000_000
        ),
        'completion_tokens_details': {
            'reasoning_tokens': 0,
            'accepted_prediction_tokens': 0,
            'rejected_prediction_tokens': 0,
        },
    }


def convert_response_ollama_to_openai(ollama_response: dict) -> dict:
    model = ollama_response.get('model', 'ollama')
    message_content = ollama_response.get('message', {}).get('content', '')
    reasoning_content = ollama_response.get('message', {}).get('thinking', None)
    tool_calls = ollama_response.get('message', {}).get('tool_calls', None)
    openai_tool_calls = None

    if tool_calls:
        openai_tool_calls = convert_ollama_tool_call_to_openai(tool_calls)

    data = ollama_response

    usage = convert_ollama_usage_to_openai(data)

    response = openai_chat_completion_message_template(
        model, message_content, reasoning_content, openai_tool_calls, usage
    )
    return response


async def convert_streaming_response_ollama_to_openai(ollama_streaming_response):
    has_tool_calls = False
    # All chunks in a single completion must share the same id (OpenAI spec).
    completion_id = f'chatcmpl-{str(uuid4())}'
    first = True
    async for data in ollama_streaming_response.body_iterator:
        data = JSONCodec.loads(data)

        model = data.get('model', 'ollama')
        message = data.get('message') or {}
        message_content = message.get('content', None)
        reasoning_content = message.get('thinking', None)
        tool_calls = message.get('tool_calls', None)
        openai_tool_calls = None

        if tool_calls:
            openai_tool_calls = convert_ollama_tool_call_to_openai(tool_calls)
            has_tool_calls = True

        done = data.get('done', False)

        usage = None
        if done:
            usage = convert_ollama_usage_to_openai(data)

        data = openai_chat_chunk_message_template(
            model, message_content, reasoning_content, openai_tool_calls, usage, message_id=completion_id
        )

        # First chunk must carry delta.role (OpenAI spec).
        if first:
            data['choices'][0]['delta']['role'] = 'assistant'
            first = False

        if done and has_tool_calls:
            data['choices'][0]['finish_reason'] = 'tool_calls'

        line = f'data: {JSONCodec.dumps(data)}\n\n'
        yield line

    yield 'data: [DONE]\n\n'


def convert_embedding_response_ollama_to_openai(response) -> dict:
    """
    Convert the response from Ollama embeddings endpoint to the OpenAI-compatible format.

    Args:
        response (dict): The response from the Ollama API,
            e.g. {"embedding": [...], "model": "..."}
            or {"embeddings": [{"embedding": [...], "index": 0}, ...], "model": "..."}

    Returns:
        dict: Response adapted to OpenAI's embeddings API format.
            e.g. {
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [...], "index": 0},
                    ...
                ],
                "model": "...",
            }
    """
    # Ollama batch-style output from /api/embed
    # Response format: {"embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]], "model": "..."}
    if isinstance(response, dict) and 'embeddings' in response:
        openai_data = []
        for i, emb in enumerate(response['embeddings']):
            # /api/embed returns embeddings as plain float lists
            if isinstance(emb, list):
                openai_data.append(
                    {
                        'object': 'embedding',
                        'embedding': emb,
                        'index': i,
                    }
                )
            # Also handle dict format for robustness
            elif isinstance(emb, dict):
                openai_data.append(
                    {
                        'object': 'embedding',
                        'embedding': emb.get('embedding'),
                        'index': emb.get('index', i),
                    }
                )
        return {
            'object': 'list',
            'data': openai_data,
            'model': response.get('model'),
        }
    # Ollama single output
    elif isinstance(response, dict) and 'embedding' in response:
        return {
            'object': 'list',
            'data': [
                {
                    'object': 'embedding',
                    'embedding': response['embedding'],
                    'index': 0,
                }
            ],
            'model': response.get('model'),
        }
    # Already OpenAI-compatible?
    elif isinstance(response, dict) and 'data' in response and isinstance(response['data'], list):
        return response

    # Fallback: return as is if unrecognized
    return response
