from __future__ import annotations

import logging
import urllib.request

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from open_webui.retrieval.web.main import SearchResult, get_filtered_results

log = logging.getLogger(__name__)


def search_duckduckgo(
    query: str,
    count: int,
    filter_list: list[str | None] = None,
    concurrent_requests: int | None = None,
    backend: str | None = 'auto',
) -> list[SearchResult]:
    """
    Search using DuckDuckGo's Search API and return the results as a list of SearchResult objects.
    Args:
        query (str): The query to search for
        count (int): The number of results to return
        backend (str): The search backend to use (auto, duckduckgo, google, brave, etc.)

    Returns:
        list[SearchResult]: A list of search results
    """
    # The ddgs library (primp-based) does not auto-detect proxy env vars.
    # Resolve via stdlib getproxies() — same pattern as the other loaders.
    env_proxies = urllib.request.getproxies()
    proxy = env_proxies.get('https') or env_proxies.get('http')
    search_results = []
    with DDGS(proxy=proxy) as ddgs:
        if concurrent_requests:
            ddgs.threads = concurrent_requests

        def run(selected_backend: str | None):
            kwargs = {'safesearch': 'moderate', 'max_results': count}
            if selected_backend and selected_backend != 'auto':
                kwargs['backend'] = selected_backend
            results = ddgs.text(query, **kwargs)
            return results if results is not None else []

        try:
            search_results = run(backend)
        except DDGSException as exc:
            log.warning('DuckDuckGo backend %s failed: %s', backend or 'auto', exc)
            search_results = []

        if not search_results and backend and backend != 'auto':
            try:
                search_results = run('auto')
            except DDGSException as exc:
                log.warning('DuckDuckGo automatic backend fallback failed: %s', exc)
                search_results = []
    if filter_list:
        search_results = get_filtered_results(search_results, filter_list)

    # Return the list of search results
    return [
        SearchResult(
            link=result['href'],
            title=result.get('title'),
            snippet=result.get('body'),
        )
        for result in search_results
    ]
