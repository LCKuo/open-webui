import importlib.util
import ast
import re
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feedback = load_module('feedback', 'open_webui/utils/crm_discovery_feedback.py')
actions = load_module('actions', 'open_webui/tools/interact_crm_actions.py')


@pytest.mark.parametrize('message', [
    '為什麼最後一個bd任務，寫已飽和，新增0筆？', '最新 BD 任務結果',
    '探索 #49 為什麼沒有新增', '搜尋進度如何', '查看任務49',
])
def test_status_questions(message):
    assert feedback.is_discovery_status_question(message)


@pytest.mark.parametrize('message', ['執行潛客探索', '啟動自動獲客', '待確認名單', '今天客戶回信了嗎', '查詢 BD 待確認公司', 'BD 客戶為什麼沒有回信'])
def test_does_not_intercept_other_commands(message):
    assert not feedback.is_discovery_status_question(message)


def test_explicit_id_or_latest():
    assert feedback.discovery_run_id('最後一個bd任務新增0筆') is None
    assert feedback.discovery_run_id('任務 #49 新增0筆') == 49
    assert feedback.discovery_run_id('任務編號：49') == 49


def test_factual_format_and_human_confirmation():
    text = feedback.format_discovery_status({'runs':[{
        'runId':49,'statusLabel':'搜尋完成，未新增','completedRounds':2,'maxRounds':2,'sourceCount':26,
        'outcome':'淨新增 0 家；2 筆重複、2 筆排除',
        'adjustments':[], 'recommendations':[{'title':'檢查客群','detail':'確認排除條件','requiresConfirmation':True}],
    }]})
    assert '26 個來源' in text and '2 筆排除' in text
    assert '需你確認' in text and '市場已找完' in text
    assert '已調整' not in text


@pytest.mark.asyncio
async def test_read_only_action_payload(monkeypatch):
    execute = AsyncMock(return_value=json.dumps({'ok':True,'runs':[]}))
    monkeypatch.setattr(actions, '_execute', execute)
    await actions.interact_crm_bd_discovery_status(run_id=49)
    args = execute.call_args.args
    assert args[:3] == ('bd.discovery.status', {'runId':49,'limit':1}, 'bd')
    execute.reset_mock()
    await actions.interact_crm_bd_discovery_status()
    assert execute.call_args.args[1] == {'limit':1}


def direct_namespace():
    source = ast.parse((ROOT / 'open_webui/routers/interact_channels.py').read_text(encoding='utf-8'))
    names = {'_bd_direct_command', '_direct_crm_channel_command', '_crm_action_result', '_crm_channel_tool_context', '_direct_crm_embedded_discovery_status'}
    future = ast.parse('from __future__ import annotations').body
    module = ast.Module(body=future + [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names], type_ignores=[])
    namespace = {
        're': re, 'json': json, 'is_discovery_status_question': feedback.is_discovery_status_question,
        'discovery_run_id': feedback.discovery_run_id, 'format_discovery_status': feedback.format_discovery_status,
        'interact_crm_bd_discovery_status': AsyncMock(return_value=json.dumps({'ok':True,'runs':[]})),
        'interact_crm_bd_discovery_start': AsyncMock(), 'interact_crm_bd_candidates_list': AsyncMock(),
    }
    exec(compile(module, '<direct CRM command>', 'exec'), namespace)
    return namespace


@pytest.mark.asyncio
async def test_real_direct_router_reads_status_without_starting_discovery():
    ns = direct_namespace()
    result = await ns['_direct_crm_channel_command'](None, SimpleNamespace(product_role='bd', id='bd-channel', model_id='bd-model'), '為什麼最後一個bd任務，写已飽和，新增0筆？', {'companyUserId':'company', 'productUserId':'employee'}, None)
    assert result['ok'] is True
    assert ns['interact_crm_bd_discovery_status'].call_args.kwargs['run_id'] is None
    ns['interact_crm_bd_discovery_start'].assert_not_called()
    ns['interact_crm_bd_candidates_list'].assert_not_called()


@pytest.mark.asyncio
async def test_direct_router_rejects_missing_employee_identity():
    ns = direct_namespace()
    result = await ns['_direct_crm_channel_command'](None, SimpleNamespace(product_role='bd', id='bd-channel', model_id='bd-model'), 'BD任務結果', {}, None)
    assert result['ok'] is False
    ns['interact_crm_bd_discovery_status'].assert_not_called()


@pytest.mark.asyncio
async def test_am_cannot_use_bd_direct_status():
    ns = direct_namespace()
    result = await ns['_direct_crm_channel_command'](None, SimpleNamespace(product_role='am'), 'BD任務結果', {}, None)
    assert result is None
    ns['interact_crm_bd_discovery_status'].assert_not_called()


@pytest.mark.asyncio
async def test_embedded_status_uses_verified_identity_and_read_only_bridge():
    ns = direct_namespace()
    result = await ns['_direct_crm_embedded_discovery_status'](None, 'bd', '查詢 BD 任務 #49', {'companyUserId':'company','productUserId':'employee'}, 'bd-model', True)
    assert result['ok'] is True and result['usage'] == {}
    args = ns['interact_crm_bd_discovery_status'].call_args.kwargs
    assert args['run_id'] == 49
    assert args['__metadata__']['source'] == 'crm_embedded'
    assert args['__metadata__']['productUserId'] == 'employee'


@pytest.mark.parametrize('role,enabled,identity', [('am',True,{'companyUserId':'company','productUserId':'employee'}),('bd',False,{'companyUserId':'company','productUserId':'employee'}),('bd',True,{})])
@pytest.mark.asyncio
async def test_embedded_status_preserves_role_tool_and_identity_gates(role,enabled,identity):
    ns = direct_namespace()
    assert await ns['_direct_crm_embedded_discovery_status'](None, role, 'BD 任務 #49 結果', identity, 'bd-model', enabled) is None
    ns['interact_crm_bd_discovery_status'].assert_not_called()
