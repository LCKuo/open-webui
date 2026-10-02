"""Deterministic relevance checks before discovery consumes page/model budgets."""

import re
import unicodedata
from urllib.parse import urlparse


_GENERIC = re.compile(
    r'官方網站|官方|官網|公司|股份|有限|製造商|供應商|整合商|名錄|會員|參展商|'
    r'產品|設備|聯絡人|聯絡|業務|台灣|臺灣|台北|臺北|新北|桃園|台南|臺南|'
    r'台中|臺中|高雄|屏東|嘉義|北部|南部|中部|東部|'
    r'\b(?:official|website|company|contact|email|taiwan|site)\b', re.I
)
_DIRECTORIES = ('104.com.tw', '1111.com.tw', 'wikipedia.org', 'facebook.com',
                'twincn.com', 'twii.com.tw', 'iyp.com.tw', 'info.technews.tw',
                'findbiz.nat.gov.tw', 'datagove.com')


def _normalize(text):
    return unicodedata.normalize('NFKC', str(text or '')).lower()


def discovery_company_matches(name, content):
    core = re.sub(r'(股份)?有限公司|[\s（）()\-]', '', _normalize(name))
    return len(core) >= 2 and core in re.sub(r'[\s（）()\-]', '', _normalize(content))


def discovery_recovery_seeds(queries, search_brief=None):
    brief = search_brief if isinstance(search_brief, dict) else {}
    seeds = []
    for candidate in brief.get('recoveryCandidates') or []:
        if not isinstance(candidate, dict):
            continue
        name, url = str(candidate.get('name') or ''), str(candidate.get('website') or '')
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if not name or parsed.scheme not in {'https', 'http'} or not host or any(host == d or host.endswith('.' + d) for d in _DIRECTORIES):
            continue
        for index, query in enumerate(queries):
            site = re.search(r'\bsite:([^\s/]+)', query, re.I)
            if site and host == site.group(1).lower().removeprefix('www.'):
                seeds.append({'query': query, 'title': name, 'url': url, 'snippet': '',
                              '_query_order': index, '_result_rank': -1, '_recovery_name': name})
                break
    return seeds


def _anchors(text):
    text = _GENERIC.sub(' ', _normalize(text))
    words = set(re.findall(r'[a-z][a-z0-9-]{2,}', text))
    # Short acronyms alone (e.g. semiconductor SiP) do not establish an industry match.
    words -= {'cip', 'sip', 'www', 'com', 'net', 'org', 'http', 'https'}
    for chunk in re.findall(r'[\u3400-\u9fff]{2,}', text):
        words.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return words


def discovery_source_rejection(item, query, search_brief=None):
    """Return a reason, not a company eligibility decision. Never trust search ranking."""
    brief = search_brief if isinstance(search_brief, dict) else {}
    host = (urlparse(str(item.get('url') or item.get('link') or '')).hostname or '').lower()
    site = re.search(r'\bsite:([^\s/]+)', query, re.I)
    if site:
        domain = site.group(1).lower().removeprefix('www.')
        if host.removeprefix('www.') != domain and not host.endswith('.' + domain):
            return 'site_domain_mismatch'
        return None
    corpus = _normalize(' '.join(str(item.get(k) or '') for k in ('title', 'snippet')))
    segment = brief.get('targetSegment') or {}
    recovery = brief.get('recoveryCandidates') or []
    for candidate in recovery:
        name = str(candidate.get('name') or '')
        core = re.sub(r'(股份)?有限公司|[\s（）()\-]', '', _normalize(name))
        if name and discovery_company_matches(name, query) and len(core) >= 2:
            # A named-company search must not be satisfied by another company in the industry.
            return None if discovery_company_matches(name, corpus) else 'company_identity_mismatch'
    values = [query, str(segment.get('name') or '')]
    for key in ('industries', 'companyRoles', 'productKeywords', 'evidenceKeywords', 'needSignals'):
        if isinstance(segment.get(key), list):
            values.extend(str(value) for value in segment[key])
    anchors = _anchors(' '.join(values))
    if not anchors:
        return 'insufficient_topic_context'
    return None if any(anchor in corpus for anchor in anchors) else 'off_topic'


def discovery_source_priority(item):
    host = (urlparse(str(item.get('url') or '')).hostname or '').lower()
    title = str(item.get('title') or '')
    score = 0
    if any(host == d or host.endswith('.' + d) for d in _DIRECTORIES):
        score += 60
    if re.search(r'名錄|黃頁|會員|參展商|directory|exhibitor|catalog', title, re.I):
        score += 30
    if host.endswith(('.gov', '.gov.tw')):
        score += 20
    if re.search(r'股份有限公司|有限公司|企業|實業|工業|科技|\bcompany\b|co\.?\s*,?\s*ltd', title, re.I):
        score -= 20
    if re.search(r'\bsite:', str(item.get('query') or ''), re.I):
        score -= 30
    return score
