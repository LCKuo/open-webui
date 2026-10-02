import asyncio
import json
from open_webui.utils.line_rich_menu import _role_menu_items
from open_webui.tools import interact_crm_actions as actions


def test_live_tool_registry_exposes_all_habit_tools_with_role_boundary():
    assert actions.crm_role_action_tools({}) == []
    am = {tool.__name__ for tool in actions.crm_role_action_tools({'crm_am_actions': True})}
    bd = {tool.__name__ for tool in actions.crm_role_action_tools({'crm_bd_actions': True})}
    assert len(am) == 10 and len(bd) == 10
    assert not am.intersection(bd)
    assert 'interact_crm_am_handoff_accept' in am
    assert 'interact_crm_bd_candidate_review' in bd
    assert 'interact_crm_am_operation_get' in am
    assert 'interact_crm_bd_operation_get' in bd


def test_handoff_requires_employee_confirmed_expected_close_date(monkeypatch):
    captured = []
    async def execute(action, payload, *args):
        captured.append((action, payload))
        return '{}'
    monkeypatch.setattr(actions, '_execute', execute)
    asyncio.run(actions.interact_crm_am_handoff_accept(15, '2026-12-31'))
    assert captured == [('am.handoff.accept', {'handoffId': 15, 'expectedCloseDate': '2026-12-31'})]


def test_quote_write_in_embedded_crm_keeps_original_confirmation_form():
    assert actions._embedded_action_requires_original_form('am.quotation_follow_up.record')


def test_am_forms_use_task_links():
    items = _role_menu_items('member', 'home', portal_base_url=None, liff_uri='https://liff.line.me/am', product_role='am')
    uris = [item['action'].get('uri', '') for item in items]
    for task in ['am-create', 'am-update', 'am-draft']:
        assert f'https://liff.line.me/am?task={task}' in uris


def test_bd_custom_discovery_uses_task_link():
    items = _role_menu_items('member', 'home', portal_base_url=None, liff_uri='https://liff.line.me/bd', product_role='bd')
    assert any(item['action'].get('uri') == 'https://liff.line.me/bd?task=bd-discovery' for item in items)


def test_follow_up_tools_forward_schedule_and_revision(monkeypatch):
    captured = []
    async def execute(action, payload, *args):
        captured.append((action, payload))
        return json.dumps({'ok': True})
    monkeypatch.setattr(actions, '_execute', execute)
    common = dict(company_id=1, follow_up_type='line', subject='QA', content='QA content', outcome='QA',
                  follow_up_at='2026-09-07T09:00:00+08:00', follow_up_plan_mode='scheduled',
                  next_follow_up_at='2026-10-07T09:00:00+08:00', follow_up_plan_revision='revision')
    asyncio.run(actions.interact_crm_follow_up_create(**common))
    asyncio.run(actions.interact_crm_follow_up_update(follow_up_id=2, expected_version='12', **common))
    for _, payload in captured:
        assert payload['followUpPlanMode'] == 'scheduled'
        assert payload['nextFollowUpAt'] == common['next_follow_up_at']
        assert payload['followUpPlanRevision'] == 'revision'


def test_follow_up_default_does_not_change_human_tracking_decision(monkeypatch):
    captured = []
    async def execute(action, payload, *args):
        captured.append(payload)
        return '{}'
    monkeypatch.setattr(actions, '_execute', execute)
    asyncio.run(actions.interact_crm_follow_up_create(
        company_id=1, follow_up_type='line', subject='QA', content='QA content',
        outcome='No current demand', follow_up_at='2026-09-07T09:00:00+08:00'))
    assert captured[0]['followUpPlanMode'] == 'unchanged'


def test_am_work_list_and_shipment_care_tools_use_controlled_actions(monkeypatch):
    captured = []
    async def execute(action, payload, *args):
        captured.append((action, payload))
        return json.dumps({'ok': True})
    monkeypatch.setattr(actions, '_execute', execute)
    asyncio.run(actions.interact_crm_am_work_items_list(kind='priority', limit=3))
    asyncio.run(actions.interact_crm_shipment_care_complete(
        document_id=31,
        contact_method='call',
        content='確認收貨與使用狀況',
        outcome='客戶回覆使用正常',
        next_follow_up_at='2026-10-01T09:00:00+08:00',
        follow_up_plan_revision='revision-1',
    ))
    assert captured[0] == ('am.insights.list', {'kind': 'priority', 'limit': 3})
    assert captured[1][0] == 'am.shipment_care.complete'
    assert captured[1][1]['documentId'] == 31
    assert captured[1][1]['followUpPlanRevision'] == 'revision-1'


def test_channel_event_produces_stable_crm_action_request_id():
    metadata = {'channelEventId': 'event-77'}
    payload = {'companyId': 9, 'subject': '已確認'}
    first = actions._action_request_id(metadata, 'company-1', 'am.follow_up.create', payload)
    second = actions._action_request_id(metadata, 'company-1', 'am.follow_up.create', dict(reversed(list(payload.items()))))
    changed = actions._action_request_id(metadata, 'company-1', 'am.follow_up.update', payload)
    assert first == second
    assert first != changed


def test_crm_action_timeout_returns_the_operation_id_for_receipt_lookup(monkeypatch):
    metadata = {
        'source': 'channel',
        'channelId': 'am-line',
        'modelId': 'model-1',
        'companyUserId': 'company-1',
        'channelEventId': 'event-88',
    }
    expected = actions._action_request_id(
        metadata, 'company-1', 'am.work_summary.get', {'period': 'today'}
    )
    monkeypatch.setattr(actions, '_service_config', lambda: ('https://crm.example', 'token'))

    def fail_session(**_kwargs):
        raise TimeoutError('upstream timeout')

    monkeypatch.setattr(actions.aiohttp, 'ClientSession', fail_session)
    result = json.loads(asyncio.run(actions._execute(
        'am.work_summary.get', {'period': 'today'}, 'am', None, {}, metadata
    )))
    assert result['ok'] is False
    assert result['requestId'] == expected
    assert result['action'] == 'am.work_summary.get'
