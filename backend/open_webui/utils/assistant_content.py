from __future__ import annotations

from typing import Any

PUBLIC_TEXT_PART_TYPES = {'text', 'output_text'}


def content_text(content: Any) -> str:
    """Return public text from a provider content value without exposing reasoning."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''

    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get('type') not in PUBLIC_TEXT_PART_TYPES:
            continue
        text = part.get('text')
        if isinstance(text, str):
            chunks.append(text)
    return ''.join(chunks)


def output_text(output: Any) -> str:
    """Return assistant text from an OpenAI Responses-style output list."""
    if not isinstance(output, list):
        return ''

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        if item.get('role') not in (None, 'assistant'):
            continue
        text = content_text(item.get('content'))
        if text.strip():
            chunks.append(text)
    return '\n'.join(chunks)


def response_text(response: Any) -> str:
    """Normalize public assistant text from supported provider response envelopes."""
    if not isinstance(response, dict):
        return ''

    choices = response.get('choices')
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
        text = content_text(message.get('content'))
        if text:
            return text

    message = response.get('message')
    if isinstance(message, dict):
        text = content_text(message.get('content'))
        if text:
            return text

    text = content_text(response.get('content'))
    if text:
        return text
    return output_text(response.get('output'))
