from open_webui.constants import ERROR_MESSAGES


def test_web_search_error_converts_exceptions_to_serializable_text():
    assert ERROR_MESSAGES.WEB_SEARCH_ERROR(RuntimeError('No results found.')) == 'No results found.'
