import ast
import asyncio
import importlib.util
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse, parse_qsl, urlencode

import pytest

ROOT = Path(os.environ.get('BD_RUNTIME_ROOT', Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('source_policy', ROOT / 'open_webui/utils/discovery_source_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def namespace():
    tree = ast.parse((ROOT / 'open_webui/routers/workflows.py').read_text(encoding='utf-8'))
    helpers = {'_discovery_source_is_low_value', '_discovery_source_is_document', '_discovery_source_quality',
               '_normalize_discovery_source_url', '_bounded_runtime_int', '_prioritize_web_search_fetch_results',
               '_usable_fetched_page_content', '_as_bool'}
    body = ast.parse('from __future__ import annotations').body
    body += [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in helpers
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id.startswith('_DISCOVERY_') for target in node.targets)]
    runner = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == 'node_runner')
    branch = next(node for node in runner.body if isinstance(node, ast.If) and ast.unparse(node.test) == "node_type == 'web_search'")
    runner.body = branch.body
    body.append(runner)
    ns = {'re': re, 'json': json, 'asyncio': asyncio, 'urlparse': urlparse, 'parse_qsl': parse_qsl, 'urlencode': urlencode,
          'discovery_source_rejection': policy.discovery_source_rejection, 'discovery_source_priority': policy.discovery_source_priority,
          'discovery_company_matches': policy.discovery_company_matches, 'discovery_recovery_seeds': policy.discovery_recovery_seeds,
          'builtin_search_web': AsyncMock(), 'builtin_fetch_url': AsyncMock(), 'request': None,
          'user': SimpleNamespace(model_dump=lambda: {}), 'WorkflowRuntimeError': RuntimeError}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<real web-search node>', 'exec'), ns)
    return ns


@pytest.mark.parametrize('url,title,snippet', [
    ('https://poki.com/', 'Free Online Games', 'Play games now'),
    ('https://zhidao.baidu.com/a', '這首歌歌詞是甚麼', '求歌名'),
    ('https://courtcasefinder.com/', 'Court records', 'Find court records'),
    ('https://teamviewer.com/', 'TeamViewer', 'Remote desktop software'),
    ('https://104.com.tw/company/abc', '半導體公司', 'SiP semiconductor packaging'),
])
def test_filters_unrelated_results_without_domain_blacklist(url, title, snippet):
    assert policy.discovery_source_rejection({'url': url, 'title': title, 'snippet': snippet}, '台灣 食品設備 SIP 衛生級製程') == 'off_topic'


def test_site_restriction_is_enforced_even_when_backend_ignores_it():
    assert policy.discovery_source_rejection({'url': 'https://superuser.com/q', 'title': 'Email contact'}, 'site:utekinco.com.tw 聯絡 Email') == 'site_domain_mismatch'
    assert policy.discovery_source_rejection({'url': 'https://www.utekinco.com.tw/contact'}, 'site:utekinco.com.tw 聯絡 Email') is None


def test_named_company_search_does_not_accept_another_company():
    brief = {'recoveryCandidates': [{'name': '鼎高機械科技股份有限公司'}]}
    query = '鼎高機械科技股份有限公司 官方網站 產品 公司介紹'
    assert policy.discovery_source_rejection({'url': 'https://www.top-bpmc.com/', 'title': '鼎高機械科技股份有限公司', 'snippet': '食品及製藥機械'}, query, brief) is None
    assert policy.discovery_source_rejection({'url': 'https://another.example/', 'title': '食品機械有限公司'}, query, brief) == 'company_identity_mismatch'


def test_document_and_challenge_pages_do_not_consume_content_budget():
    ns = namespace()
    assert ns['_discovery_source_is_document']('https://example.gov.tw/a.ashx?id=1', '[PDF] 業務報告')
    for text in ['You need to enable JavaScript to run this app.', '{"error":"fetch failed"}', '403 Forbidden']:
        assert not ns['_usable_fetched_page_content'](text)


@pytest.mark.asyncio
async def test_real_node_filters_before_fetch_and_keeps_budget():
    ns = namespace()
    ns['builtin_search_web'].return_value = json.dumps([
        {'link': 'https://poki.com/', 'title': 'Free Online Games', 'snippet': 'Play games'},
        {'link': 'https://yenchen.com.tw/zh-TW/index/index.html', 'title': '元成機械股份有限公司', 'snippet': '專業製藥機械與生技設備'},
        {'link': 'https://example.gov.tw/a.ashx', 'title': '[PDF] 製藥產業報告', 'snippet': '製藥食品產業'},
    ])
    ns['builtin_fetch_url'].return_value = '元成機械股份有限公司 製藥設備 ' * 1000
    result = await ns['node_runner']('web_search', {'quality_filter': True, 'fetch_pages': 5}, {}, {'data': {
        'search_queries': ['台南 生技設備 製藥設備製造商 官方網站'],
        'search_brief': {'sourcePolicy': {'maxFetchedPages': 5, 'maxPageChars': 4000, 'maxTotalContentChars': 12000}},
    }})
    assert result['result_count'] == 1
    assert 'search_brief' not in result
    assert result['source_filter']['rejected_count'] == 2
    assert ns['builtin_fetch_url'].call_args.args[0].startswith('https://yenchen.com.tw/')
    assert len(result['results'][0]['content']) <= 4000


@pytest.mark.asyncio
async def test_all_off_topic_is_explained_not_retried_as_ai_failure():
    ns = namespace()
    ns['builtin_search_web'].return_value = json.dumps([{'link': 'https://poki.com/', 'title': 'Games', 'snippet': 'Play'}])
    result = await ns['node_runner']('web_search', {'quality_filter': True, 'fetch_pages': 5}, {}, {'data': {'search_queries': ['食品設備 官網']}})
    assert result['results'] == [] and result['source_filter']['rejected_count'] == 1
    ns['builtin_fetch_url'].assert_not_called()
    ns['builtin_search_web'].assert_awaited_once()


def test_replay_real_run54_sources():
    fixture_path = Path(os.environ.get('BD_FIXTURE_PATH', ROOT.parents[2] / 'CRM/manufacturing-crm/artifacts/bd-source-recovery-20260909/run54.json'))
    if not fixture_path.exists():
        pytest.skip('Production replay fixture is local to the CRM workspace')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    rejected, kept = [], []
    for round in fixture['rounds']:
        for item in round['search']['results']:
            reason = policy.discovery_source_rejection(item, item['query'], {'targetSegment': fixture['segment']})
            (rejected if reason else kept).append(item)
    assert any('yenchen.com.tw' in item['url'] for item in kept)
    assert any('1111.com.tw' in item['url'] and '鼎高' in item['title'] for item in kept)
    assert not any(any(domain in item['url'] for domain in ('poki.com', 'zhidao.baidu.com', 'teamviewer.com', 'courtcasefinder.com', 'answers.microsoft.com', 'unicourt.com', 'publiccourtrecords.org')) for item in kept)
    assert len(rejected) >= 14


def test_recovery_seed_rejects_directories_and_nonmatching_site():
    brief = {'recoveryCandidates': [
        {'name': '鼎高機械科技股份有限公司', 'website': 'https://www.1111.com.tw/corp/72464206/'},
        {'name': '友德國際股份有限公司', 'website': 'https://www.utekinco.com.tw/'},
    ]}
    seeds = policy.discovery_recovery_seeds(['site:1111.com.tw 鼎高機械科技', 'site:utekinco.com.tw 友德國際'], brief)
    assert [v['url'] for v in seeds] == ['https://www.utekinco.com.tw/']
    assert policy.discovery_recovery_seeds(['site:another.example 友德國際'], brief) == []
    assert policy.discovery_source_priority({'url': 'https://twincn.com/item.aspx', 'title': '友德國際股份有限公司'}) > policy.discovery_source_priority({'url': 'https://utekinco.com.tw/', 'title': '友德國際股份有限公司'})


@pytest.mark.asyncio
@pytest.mark.parametrize('body,accepted', [('友德國際股份有限公司 食品製藥機械與專業設備 ' * 200, True), ('另外一家機械公司 產品與服務 ' * 200, False), ('{"error":"blocked"}', False)])
async def test_direct_company_site_recovery_without_trusting_search_ranking(body, accepted):
    ns = namespace()
    ns['builtin_search_web'].return_value = json.dumps([{'link': 'https://superuser.com/q', 'title': 'Email support', 'snippet': ''}])
    ns['builtin_fetch_url'].return_value = body
    result = await ns['node_runner']('web_search', {'quality_filter': True, 'fetch_pages': 5}, {}, {'data': {
        'search_queries': ['site:utekinco.com.tw 友德國際股份有限公司 產品 公司介紹'],
        'search_brief': {'recoveryCandidates': [{'name': '友德國際股份有限公司', 'website': 'https://www.utekinco.com.tw/'}],
                         'sourcePolicy': {'maxFetchedPages': 1, 'maxPageChars': 1000, 'maxTotalContentChars': 1000}},
    }})
    assert result['result_count'] == int(accepted)
    ns['builtin_fetch_url'].assert_awaited_once()
    ns['builtin_search_web'].assert_awaited_once()
    assert result['source_filter']['rejected_count'] == (1 if accepted else 2)
    if accepted:
        assert len(result['results'][0]['content']) <= 1000
        assert '_recovery_name' not in result['results'][0]


@pytest.mark.asyncio
async def test_direct_recovery_respects_blocked_domains():
    ns = namespace()
    ns['builtin_search_web'].return_value = json.dumps([{'link': 'https://superuser.com/q', 'title': 'Email support', 'snippet': ''}])
    result = await ns['node_runner']('web_search', {'quality_filter': True, 'fetch_pages': 5}, {}, {'data': {
        'search_queries': ['site:utekinco.com.tw 友德國際股份有限公司'], 'blocked_domains': ['utekinco.com.tw'],
        'search_brief': {'recoveryCandidates': [{'name': '友德國際股份有限公司', 'website': 'https://www.utekinco.com.tw/'}]},
    }})
    assert result['results'] == []
    ns['builtin_fetch_url'].assert_not_called()
