"""Live, private CRM chat-model catalog and bounded readiness checks."""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from open_webui.internal.db import get_async_db_context
from open_webui.models.access_grants import AccessGrants
from open_webui.models.config import Config
from open_webui.models.models import ModelForm, Models
from open_webui.models.workflows import WorkflowRun
from open_webui.utils.openai_tool_compat import (
    apply_chat_completion_tool_compat,
    managed_agent_reasoning_effort,
)

log = logging.getLogger(__name__)

_inventory = None
_inventory_lock = asyncio.Lock()
_checks = {}
_check_locks = {}
_selection_lock = asyncio.Lock()
_scan_tasks = set()
_probe_slots = asyncio.Semaphore(2)
CATALOG_MARKER = 'crmChatCatalog'
HEALTH_HISTORY_SECONDS = 86400
PROBE_REQUEST_SECONDS = 45
PROBE_TOTAL_SECONDS = 90
EXCLUDED = re.compile(r'embed|rerank|reward|guard|content-safety|topic-control|detector|deplot|nvclip|nemotron-parse|riva-translate|ising-calibration', re.I)
PREFERRED = re.compile(
    r'kimi-k[23]|deepseek-v[34]|nemotron-3(?:\.|-super|-ultra)|mistral-large|'
    r'qwen[235].*(?:instruct|thinking)|gpt-oss|llama-3\.[13].*70b|'
    r'gpt-(?:5\.4|5\.5|5\.6-(?:luna|sol|terra)|6-astra)$',
    re.I,
)


def selection_id(model_id):
    return hashlib.sha256(model_id.encode()).hexdigest()[:24]


def display_name(model_id):
    if model_id.startswith('own-') and '.' in model_id:
        model_id = model_id.split('.', 1)[1]
    name = model_id.rsplit('/', 1)[-1]
    name = re.sub(r'nvidia|free|免費', '', name, flags=re.I)
    return name.replace('-', ' ').strip().title()


def catalog_item(model_id):
    return {'id': selection_id(model_id), 'name': display_name(model_id),
            'preferred': bool(PREFERRED.search(model_id)),
            'focus': ['工具任務', '網站資料分析'] if PREFERRED.search(model_id) else ['文字／專項任務']}


def probe_token_limit(model_id, value=1024):
    model_id = str(model_id or '').lower()
    modern_openai = bool(re.match(r'^o\d+', model_id))
    match = re.match(r'^gpt-(\d+)', model_id)
    modern_openai = modern_openai or bool(match and int(match.group(1)) >= 5)
    return {'max_completion_tokens' if modern_openai else 'max_tokens': value}


def health_key(inv, model_id):
    return 'crm.model_health.' + hashlib.sha256((inv['fingerprint'] + ':' + model_id).encode()).hexdigest()


def health_status(checked):
    if not isinstance(checked, dict) or not isinstance(checked.get('checkedAt'), (int, float)):
        return 'unchecked'
    if checked.get('status') not in ('ready', 'busy', 'unavailable', 'denied', 'incompatible'):
        return 'unchecked'
    if checked['status'] == 'ready' and time.time() - checked['checkedAt'] > HEALTH_HISTORY_SECONDS:
        return 'stale'
    return checked['status']


def model_sort_key(item):
    group = 0 if item['status'] == 'ready' else 2 if item['status'] in ('busy', 'unavailable', 'denied', 'incompatible') else 1
    return group, not item['preferred'], item.get('elapsedSeconds') or 9999, item['name']


async def stored_check(inv, model_id):
    saved = await Config.get(health_key(inv, model_id))
    return saved if isinstance(saved, dict) else None


def scan_key(inv):
    return 'crm.model_scan.' + inv['fingerprint']


def scan_public(state):
    if not isinstance(state, dict):
        return None
    result = {key: state.get(key) for key in ('id', 'status', 'total', 'completed', 'startedAt', 'updatedAt')}
    if result['status'] == 'running' and time.time() - (result['updatedAt'] or 0) > 120:
        result['status'] = 'interrupted'
    return result


async def claim_scan(key, state):
    # Row locking prevents separate app workers from launching the same scan.
    async with get_async_db_context() as db:
        await db.execute(insert(Config).values(key=key, value={}, updated_at=0).on_conflict_do_nothing(index_elements=['key']))
        row = await db.scalar(select(Config).where(Config.key == key).with_for_update())
        old = scan_public(row.value)
        if old and old['status'] == 'running':
            value = dict(row.value)
            value['models'] = list(dict.fromkeys([*value.get('models', []), *state['models']]))
            value['total'] = len(value['models'])
            row.value = value
            await db.commit()
            return False, scan_public(value)
        row.value = state
        row.updated_at = int(time.time())
        await db.commit()
    return True, scan_public(state)


async def scan_step(key, job_id, operation):
    async with get_async_db_context() as db:
        row = await db.scalar(select(Config).where(Config.key == key).with_for_update())
        if not row or row.value.get('id') != job_id or row.value.get('status') != 'running':
            return None
        state = dict(row.value)
        model_id = None
        if operation == 'take':
            cursor = state.get('cursor', 0)
            if cursor >= len(state['models']):
                return None
            model_id = state['models'][cursor]
            state['cursor'] = cursor + 1
        elif operation == 'finish':
            state['completed'] += 1
            if state['completed'] >= state['total']:
                state['status'] = 'complete'
        else:
            state['status'] = 'interrupted'
        state['updatedAt'] = time.time()
        row.value = state
        row.updated_at = int(time.time())
        await db.commit()
        return model_id


async def run_scan(user, inv, state):
    key = scan_key(inv)

    async def worker():
        while model_id := await scan_step(key, state['id'], 'take'):
            try:
                async with _probe_slots:
                    await probe(user, model_id, inv)
            except Exception:
                log.exception('Background model check failed: %s', selection_id(model_id))
            await scan_step(key, state['id'], 'finish')

    try:
        await asyncio.gather(worker(), worker())
    except asyncio.CancelledError:
        await scan_step(key, state['id'], 'interrupt')
        raise
    except Exception:
        log.exception('Background model scan interrupted')
        await scan_step(key, state['id'], 'interrupt')


async def queue_checks(user, model_ids=None, scope='preferred'):
    inv = await inventory(user=user)
    current = scan_public(await Config.get(scan_key(inv)))
    history = await Config.get_many(*(health_key(inv, mid) for mid in inv['ids']))
    due = []
    for mid in inv['ids']:
        if model_ids is not None and mid not in model_ids:
            continue
        if model_ids is None and scope != 'all' and not PREFERRED.search(mid):
            continue
        model = await Models.get_model_by_id(mid)
        if model and (not model.is_active or not safe_base_record(model)):
            continue
        old = history.get(health_key(inv, mid)) or {}
        age = time.time() - old.get('checkedAt', 0)
        cooldown = HEALTH_HISTORY_SECONDS if old.get('ok') else 60 if old.get('status') == 'busy' or model_ids is not None else 86400
        if old and age < cooldown:
            continue
        due.append(mid)
    due.sort(key=lambda mid: (not bool((history.get(health_key(inv, mid)) or {}).get('ok')),
                              (history.get(health_key(inv, mid)) or {}).get('elapsedSeconds', 9999), not bool(PREFERRED.search(mid)), mid))
    if not due:
        return {'ok': True, 'status': 'complete', 'scan': current,
                'message': '目前已有近期檢查結果，無需重複測試。'}
    now = time.time()
    state = {'id': uuid.uuid4().hex, 'status': 'running', 'total': len(due), 'completed': 0,
             'startedAt': now, 'updatedAt': now, 'models': due, 'cursor': 0}
    claimed, public = await claim_scan(scan_key(inv), state)
    if claimed:
        task = asyncio.create_task(run_scan(user, inv, state))
        _scan_tasks.add(task)
        task.add_done_callback(_scan_tasks.discard)
    return {'ok': False, 'status': 'checking', 'scan': public,
            'message': '已加入背景檢查，可先做其他工作；完成後再由您確認套用。'}


async def personal_connections(user):
    if not user:
        return []
    from open_webui.routers.openai import get_openai_runtime_connections

    personal_mode, connections = await get_openai_runtime_connections(user)
    return connections if personal_mode else []


async def connection(user=None, model_id=None):
    own_connections = await personal_connections(user)
    if own_connections:
        selected = None
        if model_id:
            selected = next(
                (
                    item
                    for item in own_connections
                    if model_id.startswith(f'{item["config"]["prefix_id"]}.')
                ),
                None,
            )
        selected = selected or (own_connections[0] if len(own_connections) == 1 else None)
        if not selected:
            raise HTTPException(409, '無法判斷此模型所屬的自有 AI 連線，請重新整理模型清單。')
        return selected['url'].rstrip('/'), selected['key']

    conf = await Config.get_many('openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    for i, url in enumerate(conf.get('openai.api_base_urls') or []):
        parsed = urlsplit(url)
        cfg = (conf.get('openai.api_configs') or {}).get(str(i), {})
        keys = conf.get('openai.api_keys') or []
        if (parsed.scheme == 'https' and parsed.hostname == 'integrate.api.nvidia.com'
                and parsed.path.rstrip('/') == '/v1' and not parsed.query and cfg.get('enable', True)):
            shared_key = keys[i] if i < len(keys) else ''
            if shared_key:
                return url.rstrip('/'), shared_key
    raise HTTPException(503, 'AI 模型服務尚未完成連線，請聯絡管理員。')


async def inventory(force=False, user=None):
    global _inventory
    own_connections = await personal_connections(user)
    if own_connections:
        fingerprint_source = json.dumps(
            [(item['url'], item['key'], item['config']['prefix_id']) for item in own_connections],
            separators=(',', ':'),
        )
    else:
        url, key = await connection(user)
        fingerprint_source = url + key
    fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
    async with _inventory_lock:
        if _inventory and _inventory['fingerprint'] == fingerprint and not force and time.time() - _inventory['at'] < 60:
            return _inventory
        try:
            if own_connections:
                from open_webui.routers.openai import get_models_request

                responses = await asyncio.gather(
                    *(
                        get_models_request(
                            None,
                            item['url'],
                            item['key'],
                            user=user,
                            config=item['config'],
                        )
                        for item in own_connections
                    )
                )
                ids = []
                for item, response in zip(own_connections, responses):
                    data = response if isinstance(response, list) else (response or {}).get('data', [])
                    prefix = item['config']['prefix_id']
                    ids.extend(
                        f'{prefix}.{model_id}'
                        for model in data
                        if isinstance(model, dict)
                        and isinstance((model_id := model.get('id')), str)
                        and not EXCLUDED.search(model_id)
                        and len(model_id) < 200
                    )
                ids = sorted(set(ids))
            else:
                headers = {'Authorization': 'Bearer ' + key} if key else {}
                async with httpx.AsyncClient(timeout=12) as client:
                    response = await client.get(url + '/models', headers=headers)
                    response.raise_for_status()
                    data = response.json()['data']
                ids = sorted({str(m['id']) for m in data if isinstance(m, dict) and isinstance(m.get('id'), str)
                              and not EXCLUDED.search(m['id']) and len(m['id']) < 200})
            if not ids:
                raise ValueError('Empty catalog')
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, '暫時無法更新模型清單；尚未確認可用性，請稍後重新整理。') from None
        _inventory = {
            'ids': ids,
            'at': time.time(),
            'fingerprint': fingerprint,
            'personal': bool(own_connections),
        }
        return _inventory


async def resolve_selection(opaque_id, force=False, user=None):
    inv = await inventory(force, user=user)
    model_id = next((item for item in inv['ids'] if selection_id(item) == opaque_id), None)
    if not model_id:
        raise HTTPException(409, '此模型已不在目前可用清單，請重新整理並改選其他模型。')
    return model_id, inv


def safe_base_record(model):
    if not model or model.base_model_id:
        return model is None
    params = model.params.model_dump()
    meta = model.meta.model_dump()
    return not params.get('system') and not any(meta.get(k) for k in ('knowledge', 'toolIds', 'filterIds', 'functionIds'))


async def authorize_inventory(user):
    inv = await inventory(user=user)
    # Serialize grant creation; never copy private Agent prompts or grants.
    async with _selection_lock:
        for model_id in inv['ids']:
            model = await Models.get_model_by_id(model_id)
            if model is None:
                model = await Models.insert_new_model(ModelForm(
                    id=model_id, name=display_name(model_id), base_model_id=None, params={},
                    meta={CATALOG_MARKER: True, 'capabilities': {'citations': True, 'status_updates': True}},
                    access_grants=[], is_active=True,
                ), user.id)
            if not model or not model.is_active or not safe_base_record(model):
                continue
            if model.user_id != user.id:
                await AccessGrants.grant_access('model', model_id, 'user', user.id, 'read')
    return inv


async def role_agent(user, role):
    models = await Models.get_models_by_user_id(user.id)
    matches = []
    for model in models:
        meta = model.meta.model_dump()
        uses = meta.get('managedUseCases') or []
        valid = 'prospecting_discovery' in uses if role == 'bd' else bool(set(uses) & {'customer_growth', 'account_management'})
        if model.user_id == user.id and model.is_active and model.base_model_id and valid:
            matches.append(model)
    if len(matches) != 1:
        raise HTTPException(409, '目前尚未設定唯一的對應 Agent，請先到 Agent 設定確認。')
    return matches[0]


def failure(status):
    if status in (404, 410):
        return 'unavailable', '此模型目前無法使用或已停止提供，請改選其他模型。'
    if status in (401, 403):
        return 'denied', '此模型目前未獲服務授權，請改選其他模型或聯絡管理員。'
    if status == 429:
        return 'busy', '此模型目前額度已用完或忙碌中，請稍後再試或改選其他模型。'
    if status in (400, 422):
        return 'incompatible', '此模型尚未通過工具任務相容性檢查，請改選其他模型。'
    return 'busy', '此模型暫時無法回應，請稍後再試或改選其他模型。'


async def probe(user, model_id, inv=None):
    inv = inv or await inventory(user=user)
    if model_id not in inv['ids']:
        raise HTTPException(409, '此模型已停止提供，請重新選型。')
    url, key = await connection(user, model_id)
    cache_key = (user.id, inv['fingerprint'], model_id)
    lock = _check_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = await stored_check(inv, model_id) or _checks.get(cache_key)
        if cached and time.time() - cached['checkedAt'] < (HEALTH_HISTORY_SECONDS if cached['ok'] else 60):
            return cached
        started = time.monotonic()
        failure_code = None
        result = {'ok': False, 'status': 'busy', 'message': '此模型回應逾時，請改選其他模型。', 'checkedAt': time.time()}
        tool = {'type': 'function', 'function': {'name': 'confirm_readiness', 'description': 'Read test readiness.',
                'parameters': {'type': 'object', 'properties': {'ready': {'type': 'boolean'}}, 'required': ['ready']}}}
        upstream_model_id = model_id.split('.', 1)[1] if model_id.startswith('own-') and '.' in model_id else model_id
        payload = {'model': upstream_model_id, 'stream': False, **probe_token_limit(upstream_model_id),
                   'messages': [{'role': 'user', 'content': 'Call confirm_readiness with ready=true. After the tool returns, reply exactly MODEL_READY. This is a synthetic compatibility test.'}],
                   'tools': [tool], 'tool_choice': 'required'}
        effort = managed_agent_reasoning_effort(model_id)
        if effort:
            payload['reasoning_effort'] = effort
        apply_chat_completion_tool_compat(payload)
        try:
            async with asyncio.timeout(PROBE_TOTAL_SECONDS):
                headers = {'Authorization': 'Bearer ' + key} if key else {}
                async with httpx.AsyncClient(timeout=PROBE_REQUEST_SECONDS) as client:
                    response = await client.post(url + '/chat/completions', headers=headers, json=payload)
                    if response.status_code != 200:
                        failure_code = f'http_{response.status_code}'
                        result['status'], result['message'] = failure(response.status_code)
                    else:
                        data = response.json()
                        message = data.get('choices', [{}])[0].get('message', {})
                        calls = message.get('tool_calls') or []
                        valid = len(calls) == 1 and calls[0].get('function', {}).get('name') == 'confirm_readiness'
                        args = json.loads(calls[0]['function']['arguments']) if valid else {}
                        if valid and args.get('ready') is True:
                            followup = {**payload, 'tool_choice': 'none', 'messages': [*payload['messages'], message,
                                        {'role': 'tool', 'tool_call_id': calls[0]['id'], 'content': '{"ready":true}'}]}
                            completed = await client.post(url + '/chat/completions', headers=headers, json=followup)
                            if completed.status_code != 200:
                                failure_code = f'http_{completed.status_code}'
                                result['status'], result['message'] = failure(completed.status_code)
                            elif 'MODEL_READY' in (completed.json().get('choices', [{}])[0].get('message', {}).get('content') or ''):
                                result.update(ok=True, status='ready', message='已通過連線、工具呼叫與回覆檢查。')
                            else:
                                result.update(status='incompatible', message='工具執行後沒有完整回覆，尚未通過檢查。')
                        else:
                            result.update(status='incompatible', message='此模型未能完成工具呼叫，請改選其他模型。')
        except (TimeoutError, httpx.TimeoutException) as exc:
            failure_code = type(exc).__name__
            seconds = round(time.monotonic() - started)
            result['message'] = f'在 {seconds} 秒內未完成回應，本次無法確認可用；請改選已驗證模型或稍後重試。'
        except httpx.HTTPError as exc:
            failure_code = type(exc).__name__
            result['message'] = '暫時無法連線到模型服務，請改選已驗證模型或稍後重試。'
        except (ValueError, KeyError, TypeError, IndexError):
            result.update(status='incompatible', message='此模型回傳格式不完整，尚未通過檢查。')
        result['checkedAt'] = time.time()
        result['elapsedSeconds'] = round(time.monotonic() - started, 1)
        _checks[cache_key] = result
        try:
            await Config.upsert({health_key(inv, model_id): result})
        except Exception:
            log.exception('Could not save model readiness history: %s', selection_id(model_id))
        log.info('Model readiness model=%s status=%s reason=%s elapsed=%s',
                 selection_id(model_id), result['status'], failure_code or result['status'], result['elapsedSeconds'])
        return result


async def catalog(user, refresh=False):
    inv = await inventory(refresh, user=user)
    history = await Config.get_many(*(health_key(inv, model_id) for model_id in inv['ids']))
    items = []
    for model_id in inv['ids']:
        model = await Models.get_model_by_id(model_id)
        if model and (not model.is_active or not safe_base_record(model)):
            continue
        item = catalog_item(model_id)
        checked = history.get(health_key(inv, model_id)) or _checks.get((user.id, inv['fingerprint'], model_id))
        item.update(status=health_status(checked), checkedAt=checked.get('checkedAt') if isinstance(checked, dict) else None,
                    elapsedSeconds=checked.get('elapsedSeconds') if isinstance(checked, dict) else None)
        items.append(item)
    items.sort(key=model_sort_key)
    selected = {}
    for role in ('bd', 'am'):
        try:
            agent = await role_agent(user, role)
            selected[role] = {'id': selection_id(agent.base_model_id), 'name': display_name(agent.base_model_id),
                              'inCatalog': agent.base_model_id in inv['ids']}
        except HTTPException:
            selected[role] = None
    return {'items': items, 'updatedAt': inv['at'], 'selected': selected,
            'source': 'personal' if inv.get('personal') else 'platform',
            'modelCount': len(items),
            'scan': scan_public(await Config.get(scan_key(inv)))}


async def select_model(request, user, role, opaque_id):
    model_id, inv = await resolve_selection(opaque_id, force=True, user=user)
    agent = await role_agent(user, role)
    existing = await Models.get_model_by_id(model_id)
    if existing and (not existing.is_active or not safe_base_record(existing)):
        raise HTTPException(403, '此模型目前不可選用。')
    checked = await stored_check(inv, model_id)
    if not checked or health_status(checked) in ('unchecked', 'stale') or time.time() - checked.get('checkedAt', 0) >= HEALTH_HISTORY_SECONDS:
        return await queue_checks(user, [model_id])
    if not checked['ok']:
        return checked
    await authorize_inventory(user)
    latest = await Models.get_model_by_id(agent.id)
    # Compare persisted fields, not the display-only owner object on the role lookup.
    if not latest or any(latest.model_dump().get(k) != agent.model_dump().get(k) for k in latest.model_dump()):
        raise HTTPException(409, 'Agent 設定剛剛已變動，請重新整理再套用。')
    async with get_async_db_context() as db:
        active = await db.scalar(select(WorkflowRun.id).where(WorkflowRun.user_id == user.id,
                                 WorkflowRun.status.in_(['queued', 'running'])).limit(1))
        if active:
            raise HTTPException(409, '目前仍有 AI 任務執行中，請等待完成後再更換模型。')
    params = agent.params.model_dump()
    for key in ('temperature', 'top_p', 'reasoning_effort', 'chat_template_kwargs'):
        params.pop(key, None)
    params['max_tokens'] = 16384
    effort = managed_agent_reasoning_effort(model_id)
    if effort:
        params['reasoning_effort'] = effort
    form = ModelForm(**{**latest.model_dump(), 'base_model_id': model_id, 'params': params,
                        'meta': {**agent.meta.model_dump(), CATALOG_MARKER: True}})
    updated = await Models.update_model_by_id(agent.id, form)
    if not updated:
        raise HTTPException(409, 'Agent 設定已變動，請重新整理後再試。')
    from open_webui.utils.models import get_all_models, refresh_runtime_model_cache_entry
    if not await personal_connections(user):
        refresh_runtime_model_cache_entry(request, updated)
    from open_webui.events import EVENTS, publish_event
    try:
        await publish_event(request, EVENTS.MODEL_UPDATED, actor=user, subject_id=updated.id,
                            data={'name': updated.name, 'source': 'crm_model_catalog',
                                  'previousBaseModelId': agent.base_model_id, 'baseModelId': model_id})
    except Exception:
        log.exception('Catalog model audit event failed for model %s owner %s', updated.id, user.id)
    try:
        await get_all_models(request, user=user, refresh=True)
    except Exception:
        # The selected entry is already refreshed; a catalog outage must not undo a saved choice.
        pass
    return {**checked, 'selected': catalog_item(model_id)}


async def assert_agent_ready(user, model_id):
    model = await Models.get_model_by_id(model_id)
    if not model or not model.meta.model_dump().get(CATALOG_MARKER):
        return
    base_id = model.base_model_id or model.id
    inv = await inventory(user=user)
    result = await probe(user, base_id, inv)
    if not result['ok']:
        raise HTTPException(503, result['message'])
