from open_webui.utils.assistant_content import output_text, response_text


def test_chat_completions_content_is_normalized():
    assert response_text({'choices': [{'message': {'content': 'hello'}}]}) == 'hello'


def test_responses_output_is_normalized_without_reasoning():
    response = {
        'output': [
            {'type': 'reasoning', 'content': [{'type': 'output_text', 'text': 'private'}]},
            {
                'type': 'message',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'public'}],
            },
        ]
    }
    assert response_text(response) == 'public'


def test_anthropic_content_blocks_are_normalized():
    assert response_text({'content': [{'type': 'text', 'text': 'answer'}]}) == 'answer'


def test_multiple_assistant_output_messages_have_stable_order():
    output = [
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'one'}]},
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'two'}]},
    ]
    assert output_text(output) == 'one\ntwo'
