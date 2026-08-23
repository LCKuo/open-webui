from unittest.mock import MagicMock, patch

from ddgs.exceptions import DDGSException
from open_webui.retrieval.web.duckduckgo import search_duckduckgo


@patch('open_webui.retrieval.web.duckduckgo.DDGS')
def test_search_falls_back_to_auto_backend(mock_ddgs):
    client = MagicMock()
    client.text.side_effect = [
        DDGSException('No results found.'),
        [{'href': 'https://example.com', 'title': 'Example', 'body': 'Result'}],
    ]
    mock_ddgs.return_value.__enter__.return_value = client

    results = search_duckduckgo('test query', 5, backend='brave')

    assert [result.link for result in results] == ['https://example.com']
    assert client.text.call_args_list[0].kwargs['backend'] == 'brave'
    assert 'backend' not in client.text.call_args_list[1].kwargs


@patch('open_webui.retrieval.web.duckduckgo.DDGS')
def test_search_returns_empty_when_auto_backend_fails(mock_ddgs):
    client = MagicMock()
    client.text.side_effect = DDGSException('No results found.')
    mock_ddgs.return_value.__enter__.return_value = client

    assert search_duckduckgo('test query', 5, backend='auto') == []
    assert client.text.call_count == 1
