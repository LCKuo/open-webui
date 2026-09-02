import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from open_webui.models.interact_channels import line_identity_hash
from open_webui.routers.interact_channels import (
    ChannelHubLiffSessionRequest,
    ChannelSyncRequest,
    LineIdentityUnlinkRequest,
    _download_line_message_content,
    _enabled_agent_bindings,
    _enabled_product_bindings,
    _issue_line_account_link,
    _line_account_link_requested,
    _line_advance_workflow_session,
    _line_binding_matches_channel_product,
    _line_event_requires_identity,
    _line_guided_input,
    _line_next_guided_field,
    _line_part_as_file,
    _line_refresh_identity,
    _line_resolve_workflow_postback,
    _require_company_channel,
    channel_hub_liff_session,
    sync_channel,
    sync_line_rich_menu,
    unlink_line_identity,
)
from open_webui.utils.line_rich_menu import (
    GUEST_PRIMARY_HEIGHT,
    RICH_MENU_HEIGHT,
    RICH_MENU_UTILITY_HEIGHT,
    RICH_MENU_WIDTH,
    build_line_rich_menu,
    build_line_role_menus,
    parse_line_postback_data,
)
from PIL import Image


@pytest.fixture(autouse=True)
def allow_rich_menu_sync_claim(monkeypatch):
    async def claim(*args, **kwargs):
        return 'sync-token'

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.claim_rich_menu_sync',
        claim,
    )

    async def empty(*args, **kwargs):
        return []

    async def commit(*args, **kwargs):
        return SimpleNamespace(status='active')

    async def delete_variants(*args, **kwargs):
        return None

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_rich_menu_variants',
        empty,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_line_identity_bindings',
        empty,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.commit_rich_menu_set',
        commit,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.delete_rich_menu_variants',
        delete_variants,
    )


def _workflow(index: int) -> dict:
    return {
        'id': f'workflow-{index}',
        'versionId': f'version-{index}',
        'name': f'動態工作流 {index}',
        'buttonLabel': f'執行動態工作流 {index}',
        'description': f'這是第 {index} 個工作流的功能說明',
    }


def test_line_account_link_command_accepts_portal_deep_link_text():
    assert _line_account_link_requested('綁定帳號') is True
    assert _line_account_link_requested(' 綁定企業帳號！ ') is True
    assert _line_account_link_requested('查詢公司資料') is False


def test_line_identity_hash_is_scoped_to_each_bot_channel():
    first = line_identity_hash('channel-a', 'same-line-user')
    second = line_identity_hash('channel-b', 'same-line-user')

    assert first != second
    assert first == line_identity_hash('channel-a', 'same-line-user')


def test_member_channel_requires_identity_for_plain_messages_and_agent_actions():
    channel = SimpleNamespace(access_mode='member')

    assert _line_event_requires_identity(channel, 'message', '') is True
    assert _line_event_requires_identity(channel, 'postback', 'menu.workflows.v1') is True
    assert _line_event_requires_identity(channel, 'postback', 'menu.account_link.v1') is False
    assert _line_event_requires_identity(channel, 'postback', 'menu.help.v1') is False


def test_public_channel_keeps_plain_chat_open_but_protects_enterprise_workflows():
    channel = SimpleNamespace(access_mode='public')

    assert _line_event_requires_identity(channel, 'message', '') is False
    assert _line_event_requires_identity(channel, 'postback', 'workflow.launch.v2') is True


def test_channel_hub_filters_agents_and_quick_actions_by_company_role():
    channel = SimpleNamespace(
        agent_bindings=[
            {
                'modelId': 'support-agent',
                'label': '客服助手',
                'role': 'support',
                'enabled': True,
                'isDefault': True,
                'allowedRoles': ['owner', 'admin', 'member'],
                'quickActions': [
                    {'id': 'faq', 'label': '常見問題', 'message': '請整理常見問題'},
                ],
            },
            {
                'modelId': 'admin-agent',
                'label': '管理助手',
                'role': 'operations',
                'enabled': True,
                'allowedRoles': ['owner', 'admin'],
            },
        ],
    )

    member_agents = _enabled_agent_bindings(channel, 'member')
    assert [item['modelId'] for item in member_agents] == ['support-agent']
    assert member_agents[0]['role'] == 'support'
    assert member_agents[0]['quickActions'][0]['message'] == '請整理常見問題'
    assert [item['modelId'] for item in _enabled_agent_bindings(channel, 'admin')] == [
        'support-agent'
    ]


def test_channel_hub_filters_product_actions_and_rejects_non_https_urls():
    channel = SimpleNamespace(
        product_role='bd',
        product_bindings=[
            {
                'productKey': 'crm',
                'instanceId': 'crm-1',
                'label': 'CRM',
                'enabled': True,
                'allowedRoles': ['owner', 'admin', 'member'],
                'actions': [
                    {
                        'id': 'customers',
                        'label': '客戶清單',
                        'href': 'https://crm.example.com/customers',
                        'allowedRoles': ['owner', 'admin', 'member'],
                    },
                    {
                        'id': 'settings',
                        'label': '管理設定',
                        'href': 'https://crm.example.com/settings',
                        'allowedRoles': ['owner'],
                    },
                    {
                        'id': 'unsafe',
                        'label': '不安全網址',
                        'href': 'javascript:alert(1)',
                        'allowedRoles': ['member'],
                    },
                    {
                        'id': 'ask-bd',
                        'label': '研究潛在客戶',
                        'kind': 'message',
                        'message': '請先詢問本次研究條件。',
                        'agentRole': 'bd',
                        'allowedRoles': ['member'],
                    },
                ],
            },
        ],
    )

    member_products = _enabled_product_bindings(channel, 'member')
    assert len(member_products) == 1
    assert [action['id'] for action in member_products[0]['actions']] == ['customers', 'ask-bd']
    assert member_products[0]['actions'][1]['agentRole'] == 'bd'
    assert {action['id'] for action in _enabled_product_bindings(channel, 'owner')[0]['actions']} == {
        'customers',
        'settings',
    }


def test_channel_sync_schema_supports_standalone_and_future_product_adapters():
    payload = ChannelSyncRequest(
        companyEmail='company@example.com',
        companyUserId='company-id',
        channelType='line',
        channelIdentifier='line-1',
        name='企業工作台',
        agentBindings=[{
            'modelId': 'erp-agent',
            'label': 'ERP 助手',
            'role': 'inventory',
            'isDefault': True,
        }],
        modelId='erp-agent',
        channelMode='single_product',
        productRole='inventory',
        productBindings=[{
            'productKey': 'erp',
            'instanceId': 'erp-1',
            'label': 'ERP',
            'actions': [{
                'id': 'inventory',
                'label': '庫存',
                'kind': 'link',
                'href': 'https://erp.example.com/inventory',
            }, {
                'id': 'inventory-agent',
                'label': '詢問庫存',
                'kind': 'message',
                'message': '請查詢指定品項庫存。',
                'agentRole': 'inventory',
            }],
        }],
        menuProfile={'title': '辦公室工作台', 'subtitle': '選擇系統'},
    )

    assert payload.agentBindings[0].role == 'inventory'
    assert payload.channelMode == 'single_product'
    assert payload.productBindings[0].productKey == 'erp'
    assert payload.productBindings[0].actions[1].kind == 'message'


def test_am_role_menu_opens_task_specific_liff_forms_without_questionnaire_messages():
    artifacts = build_line_role_menus(
        audience='member',
        alias_ids={'home': 'am-home', 'workflows': 'am-workflows'},
        channel_name='AM Bot',
        liff_uri='https://liff.line.me/2000000000-amtest',
        product_role='am',
    )
    home = next(item for item in artifacts if item.tab == 'home')
    actions = [area['action'] for area in home.menu['areas']]
    payload = json.dumps(actions, ensure_ascii=False)

    assert 'https://liff.line.me/2000000000-amtest?task=am-create' in payload
    assert 'https://liff.line.me/2000000000-amtest?task=am-update' in payload
    assert '記錄客戶跟進' in payload
    assert '修正跟進紀錄' in payload
    assert '請依序詢問' not in payload
    assert '執行新的潛客探索' not in payload
    assert '客戶健康摘要' in payload
    assert '不要反問' in payload
    assert home.menu['chatBarText'] == 'AM 客戶經營'
    assert len(home.menu['areas']) == 8
    assert home.menu['areas'][2]['bounds']['width'] >= RICH_MENU_WIDTH // 3


def test_bd_role_menu_runs_default_discovery_and_reserves_liff_for_explicit_override():
    artifacts = build_line_role_menus(
        audience='member',
        alias_ids={'home': 'bd-home', 'workflows': 'bd-workflows'},
        channel_name='BD Bot',
        liff_uri='https://liff.line.me/2000000000-bdtest',
        product_role='bd',
    )
    home = next(item for item in artifacts if item.tab == 'home')
    actions = [area['action'] for area in home.menu['areas']]
    payload = json.dumps(actions, ensure_ascii=False)

    assert 'https://liff.line.me/2000000000-bdtest?task=bd-discovery' in payload
    assert '執行新的潛客探索' in payload
    assert '不要反問' in payload
    assert '請先列出可用目標客群' not in payload
    assert '改善搜尋輪廓' in payload
    assert '記錄客戶跟進' not in payload
    assert '自由提問' not in payload
    assert home.menu['chatBarText'] == 'BD 新客開發'
    assert len(home.menu['areas']) == 8
    assert home.menu['areas'][2]['bounds']['width'] >= RICH_MENU_WIDTH // 3


@pytest.mark.asyncio
async def test_channel_hub_never_returns_a_channel_from_another_company(monkeypatch):
    async def other_company_channel(*args, **kwargs):
        return SimpleNamespace(id='channel-a', company_user_id='company-b')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_id',
        other_company_channel,
    )

    with pytest.raises(HTTPException) as error:
        await channel_hub_liff_session(
            ChannelHubLiffSessionRequest(
                channelId='channel-a',
                companyUserId='company-a',
                idToken='x' * 30,
            ),
        )

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_account_link_keeps_each_bot_channel_in_its_own_url_and_state(monkeypatch):
    created_links = []
    sent_messages = []

    async def line_api(channel, *args, **kwargs):
        return {'linkToken': f'link-token-{channel.id}'}

    async def create_link(**kwargs):
        created_links.append(kwargs)

    async def send_messages(channel, reply_token, messages):
        sent_messages.append((channel.id, reply_token, messages))

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._line_portal_base_url',
        lambda: 'https://portal.example.com',
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_api)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.create_line_identity_link',
        create_link,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_messages', send_messages)

    await _issue_line_account_link(SimpleNamespace(id='channel-a'), 'same-line-user', 'reply-a')
    await _issue_line_account_link(SimpleNamespace(id='channel-b'), 'same-line-user', 'reply-b')

    assert [item['channel_id'] for item in created_links] == ['channel-a', 'channel-b']
    assert created_links[0]['state'] != created_links[1]['state']
    first_uri = sent_messages[0][2][0]['contents']['footer']['contents'][0]['action']['uri']
    second_uri = sent_messages[1][2][0]['contents']['footer']['contents'][0]['action']['uri']
    assert 'channelId=channel-a' in first_uri
    assert 'channelId=channel-b' in second_uri
    assert first_uri != second_uri


@pytest.mark.asyncio
async def test_channel_sync_rejects_an_internal_channel_id_owned_by_another_company(
    monkeypatch,
):
    async def webui_user(*args, **kwargs):
        return SimpleNamespace(role='user')

    async def other_company_channel(*args, **kwargs):
        return SimpleNamespace(
            id='shared-channel-id',
            company_email='other@example.com',
            company_user_id='other-company-id',
        )

    monkeypatch.setattr('open_webui.routers.interact_channels._require_service_token', lambda *args: None)
    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', webui_user)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_by_id',
        other_company_channel,
    )

    with pytest.raises(HTTPException) as error:
        await sync_channel(
            'shared-channel-id',
            ChannelSyncRequest(
                companyEmail='company@example.com',
                companyUserId='company-id',
                channelType='line',
                channelIdentifier='line-channel-a',
                name='LINE Bot A',
            ),
            authorization='Bearer service-token',
        )

    assert error.value.status_code == 409
    assert error.value.detail == 'The requested channel ID belongs to another company.'


def test_rich_menu_uses_dynamic_workflow_ids_and_balanced_layout():
    artifact = build_line_rich_menu([_workflow(1), _workflow(2)], 'LINE 客服')
    image = Image.open(io.BytesIO(artifact.image))
    actions = [area['action'] for area in artifact.menu['areas']]
    workflow_actions = [
        json.loads(action['data']) for action in actions if json.loads(action['data']).get('a') == 'workflow.launch.v2'
    ]

    assert image.size == (RICH_MENU_WIDTH, RICH_MENU_HEIGHT)
    assert len(artifact.menu['areas']) == 6
    assert artifact.workflow_ids == ['workflow-1', 'workflow-2']
    assert workflow_actions == [
        {'a': 'workflow.launch.v2', 'w': 'workflow-1'},
        {'a': 'workflow.launch.v2', 'w': 'workflow-2'},
    ]
    assert all('v' not in action for action in workflow_actions)
    assert len(artifact.image) < 1_000_000


def test_rich_menu_visibly_distinguishes_guided_workflow_actions():
    guided = {
        **_workflow(1),
        'launchMode': 'form_input',
    }
    artifact = build_line_rich_menu([guided], 'LINE 客服')
    action = artifact.menu['areas'][0]['action']

    assert action['label'] == '開始填寫'
    assert action['displayText'].startswith('開始填寫：')


def test_rich_menu_caps_direct_workflows_and_keeps_all_workflows_entry():
    artifact = build_line_rich_menu([_workflow(index) for index in range(10)], 'LINE 客服')
    payloads = [json.loads(area['action'].get('data', '{}')) for area in artifact.menu['areas']]

    assert artifact.workflow_ids == [f'workflow-{index}' for index in range(4)]
    assert len(artifact.menu['areas']) == 8
    assert {'a': 'menu.workflows.v1'} in payloads


def test_rich_menu_keeps_instant_and_guided_workflows_visible():
    workflows = [
        {**_workflow(1), 'launchMode': 'form_input'},
        {**_workflow(2), 'launchMode': 'text_input'},
        {**_workflow(3), 'launchMode': 'file_input'},
        {**_workflow(4), 'launchMode': 'instant'},
        {**_workflow(5), 'launchMode': 'instant'},
    ]

    artifact = build_line_rich_menu(workflows, 'LINE 客服')

    assert artifact.workflow_ids == [
        'workflow-1',
        'workflow-2',
        'workflow-4',
        'workflow-5',
    ]
    assert len(artifact.menu['areas']) == 8


def test_rich_menu_uses_compact_bottom_utility_strip():
    artifact = build_line_rich_menu([_workflow(1)], 'LINE 客服')
    utility_areas = artifact.menu['areas'][-4:]

    assert all(area['bounds']['y'] == RICH_MENU_HEIGHT - RICH_MENU_UTILITY_HEIGHT for area in utility_areas)
    assert all(area['bounds']['height'] == RICH_MENU_UTILITY_HEIGHT for area in utility_areas)


def test_rich_menu_falls_back_from_blank_button_label():
    artifact = build_line_rich_menu(
        [{**_workflow(1), 'buttonLabel': '   '}],
        'LINE 客服',
    )

    assert artifact.workflow_ids == ['workflow-1']
    assert artifact.menu['areas'][0]['action']['displayText'] == '執行：動態工作流 1'


def test_rich_menu_liff_extension_reuses_existing_slot_contract():
    artifact = build_line_rich_menu([], 'LINE 客服', liff_uri='https://liff.line.me/test')
    actions = [area['action'] for area in artifact.menu['areas']]

    assert any(action.get('type') == 'uri' and action.get('uri') == 'https://liff.line.me/test' for action in actions)
    assert parse_line_postback_data('{"a":"menu.ask.v1"}') == {'a': 'menu.ask.v1'}
    assert parse_line_postback_data('not-json') is None


def test_crm_product_channel_rejects_legacy_portal_and_other_instance_bindings():
    channel = SimpleNamespace(
        channel_mode='single_product',
        product_bindings=[{
            'enabled': True,
            'productKey': 'crm',
            'instanceId': 'crm-instance-a',
            'identityLinkUrl': 'https://crm.example.com/line-link',
        }],
    )

    assert not _line_binding_matches_channel_product(
        channel,
        SimpleNamespace(identity_source='company_portal'),
    )
    assert not _line_binding_matches_channel_product(
        channel,
        SimpleNamespace(
            identity_source='product',
            product_key='crm',
            product_instance_id='crm-instance-b',
            product_user_id='7',
        ),
    )
    assert _line_binding_matches_channel_product(
        channel,
        SimpleNamespace(
            identity_source='product',
            product_key='crm',
            product_instance_id='crm-instance-a',
            product_user_id='7',
        ),
    )


@pytest.mark.asyncio
async def test_existing_crm_product_binding_derives_employee_link_from_registered_product(monkeypatch):
    sent_messages = []

    async def line_api(*args, **kwargs):
        return {'linkToken': 'line-link-token'}

    async def no_op(*args, **kwargs):
        return None

    async def send_messages(channel, reply_token, messages):
        sent_messages.extend(messages)

    channel = SimpleNamespace(
        id='channel-id',
        channel_mode='single_product',
        product_bindings=[{
            'enabled': True,
            'productKey': 'crm',
            'instanceId': 'crm-instance-a',
            'label': '製造業 CRM',
            'actions': [{'kind': 'link', 'href': 'https://crm.example.com/dashboard'}],
        }],
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_api)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.create_line_identity_link',
        no_op,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._send_line_messages', send_messages)

    await _issue_line_account_link(channel, 'line-user', 'reply-token')

    uri = sent_messages[0]['contents']['footer']['contents'][0]['action']['uri']
    assert uri.startswith('https://crm.example.com/line-link?')
    assert 'channelId=channel-id' in uri


def test_role_menus_use_shared_tabs_and_large_stable_categories():
    artifacts = build_line_role_menus(
        audience='admin',
        alias_ids={
            'home': 'ia-channel-ah',
            'workflows': 'ia-channel-aw',
            'manage': 'ia-channel-ax',
        },
        channel_name='LINE 客服',
        portal_base_url='https://portal.example.com',
    )

    assert [(item.audience, item.tab) for item in artifacts] == [
        ('admin', 'home'),
        ('admin', 'workflows'),
        ('admin', 'manage'),
    ]
    for artifact in artifacts:
        image = Image.open(io.BytesIO(artifact.image))
        assert image.size == (RICH_MENU_WIDTH, RICH_MENU_HEIGHT)
        assert len(artifact.image) < 1_000_000
        assert artifact.menu['chatBarText'] == '開啟 AI 工作台'
        assert any(area['action']['type'] == 'richmenuswitch' for area in artifact.menu['areas'])

    workflow_actions = [
        parse_line_postback_data(area['action'].get('data'))
        for area in artifacts[1].menu['areas']
        if area['action'].get('type') == 'postback'
    ]
    assert {'a': 'menu.workflows.instant.v1'} in workflow_actions
    assert {'a': 'menu.workflows.guided.v1'} in workflow_actions
    assert {'a': 'menu.workflows.file.v1'} in workflow_actions
    assert not any(action and action.get('a') == 'workflow.launch.v2' for action in workflow_actions)


def test_guest_menu_exposes_account_link_without_company_management():
    artifact = build_line_role_menus(
        audience='guest',
        alias_ids={'home': 'ia-channel-gh'},
        channel_name='LINE 客服',
    )[0]
    actions = [
        parse_line_postback_data(area['action'].get('data'))
        for area in artifact.menu['areas']
        if area['action'].get('type') == 'postback'
    ]

    assert {'a': 'menu.account_link.v1'} in actions
    assert {'a': 'menu.account_status.v1'} in actions
    assert {'a': 'menu.resync.v1'} not in actions
    assert artifact.menu['chatBarText'] == '綁定企業帳號'
    assert len(artifact.menu['areas']) == 4
    assert artifact.menu['areas'][0]['bounds'] == {
        'x': 0,
        'y': 0,
        'width': RICH_MENU_WIDTH,
        'height': GUEST_PRIMARY_HEIGHT,
    }
    assert all(area['bounds']['y'] == GUEST_PRIMARY_HEIGHT for area in artifact.menu['areas'][1:])


@pytest.mark.asyncio
async def test_forced_identity_refresh_fails_closed_without_portal_auth(monkeypatch):
    async def binding(*args, **kwargs):
        return SimpleNamespace(role_verified_at=0)

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_line_identity_binding',
        binding,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels.is_billing_enabled', lambda: False)

    result = await _line_refresh_identity(
        SimpleNamespace(id='channel-id'),
        'line-user',
        force=True,
    )

    assert result is None


@pytest.mark.asyncio
async def test_stale_identity_refresh_fails_closed_when_portal_is_unavailable(monkeypatch):
    binding = SimpleNamespace(
        id='binding-id',
        company_user_id='company-id',
        company_member_id='member-id',
        member_email='member@example.com',
        member_role='member',
        member_status='active',
        group_ids=[],
        role_verified_at=0,
    )

    async def get_binding(*args, **kwargs):
        return binding

    async def unavailable(*args, **kwargs):
        raise RuntimeError('portal unavailable')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_line_identity_binding',
        get_binding,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractBillingClient.authorize_line_identity',
        unavailable,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels.is_billing_enabled', lambda: True)

    result = await _line_refresh_identity(
        SimpleNamespace(id='channel-id'),
        'line-user',
    )

    assert result is None


@pytest.mark.asyncio
async def test_revoked_identity_is_disabled_and_moved_to_guest_menu(monkeypatch):
    binding = SimpleNamespace(
        id='binding-id',
        company_user_id='company-id',
        company_member_id='member-id',
        member_email='member@example.com',
        member_role='admin',
        member_status='active',
        group_ids=['group-id'],
        role_verified_at=0,
    )
    menu_assignments = []

    async def get_binding(*args, **kwargs):
        return binding

    async def denied(*args, **kwargs):
        raise HTTPException(status_code=403, detail='COMPANY_MEMBER_NOT_ACTIVE')

    async def update_binding(binding_id, **kwargs):
        assert binding_id == 'binding-id'
        return SimpleNamespace(**{**binding.__dict__, **kwargs})

    async def assign_menu(channel, external_user_id, audience):
        menu_assignments.append((external_user_id, audience))
        return True

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.get_line_identity_binding',
        get_binding,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.update_line_identity_binding',
        update_binding,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractBillingClient.authorize_line_identity',
        denied,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_assign_role_menu', assign_menu)
    monkeypatch.setattr('open_webui.routers.interact_channels.is_billing_enabled', lambda: True)

    result = await _line_refresh_identity(
        SimpleNamespace(id='channel-id'),
        'line-user',
        force=True,
    )

    assert result.member_status == 'disabled'
    assert menu_assignments == [('line-user', 'guest')]


@pytest.mark.asyncio
async def test_unlink_keeps_binding_when_line_menu_reset_fails(monkeypatch):
    channel = SimpleNamespace(id='channel-id')
    binding = SimpleNamespace(
        id='binding-id',
        external_user_id='line-user',
        member_role='member',
        member_status='active',
    )
    deleted = []

    async def company_channel(*args, **kwargs):
        return channel

    async def bindings(*args, **kwargs):
        return [binding]

    async def line_failure(*args, **kwargs):
        raise RuntimeError('LINE unavailable')

    async def delete_binding(**kwargs):
        deleted.append(kwargs)
        return binding

    monkeypatch.setattr('open_webui.routers.interact_channels._require_company_channel', company_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels._require_service_token', lambda *args: None)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_line_identity_bindings',
        bindings,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_failure)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.delete_line_identity_binding',
        delete_binding,
    )

    with pytest.raises(HTTPException) as error:
        await unlink_line_identity(
            'channel-id',
            LineIdentityUnlinkRequest(
                companyEmail='company@example.com',
                companyUserId='company-id',
                bindingId='binding-id',
            ),
            authorization='Bearer service-token',
        )

    assert error.value.status_code == 502
    assert deleted == []


def test_channel_sync_can_distinguish_omitted_liff_setting_from_explicit_clear():
    shared = {
        'companyEmail': 'company@example.com',
        'channelType': 'line',
        'channelIdentifier': 'line-channel',
        'name': 'LINE Bot',
    }

    omitted = ChannelSyncRequest(**shared)
    explicit_clear = ChannelSyncRequest(**shared, richMenuLiffUri=None)

    assert 'richMenuLiffUri' not in omitted.model_fields_set
    assert 'richMenuLiffUri' in explicit_clear.model_fields_set


def test_line_file_metadata_uses_downloaded_mime_size_and_file_id():
    file_item = _line_part_as_file(
        {
            'type': 'image',
            'filename': 'photo.png',
            'mimeType': 'image/png',
            'size': 321,
            'platformFileId': 'line-message-id',
            'fileId': 'stored-file-id',
        }
    )

    assert file_item['content_type'] == 'image/png'
    assert file_item['size'] == 321
    assert file_item['id'] == 'stored-file-id'


@pytest.mark.asyncio
async def test_line_attachment_is_downloaded_with_exact_metadata_and_persisted(  # noqa: C901
    monkeypatch,
):
    requested = {}

    class FakeContent:
        async def iter_chunked(self, size):
            yield b'png-bytes'

    class FakeResponse:
        status = 200
        headers = {'Content-Type': 'image/png', 'Content-Length': '9'}
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, headers):
            requested.update({'url': url, 'headers': headers})
            return FakeResponse()

    async def owner(email):
        return SimpleNamespace(id='owner-id', email=email, name='Owner')

    async def insert_file(user_id, form):
        requested.update({'user_id': user_id, 'form': form})
        return SimpleNamespace(id=form.id)

    def upload_file(file, filename, tags):
        requested.update({'filename': filename, 'tags': tags})
        return file.read(), 'stored/path'

    monkeypatch.setattr('open_webui.routers.interact_channels.aiohttp.ClientSession', FakeSession)
    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', owner)
    monkeypatch.setattr('open_webui.routers.interact_channels.Files.insert_new_file', insert_file)
    monkeypatch.setattr('open_webui.routers.interact_channels.Storage.upload_file', upload_file)

    result = await _download_line_message_content(
        SimpleNamespace(
            id='channel-id',
            company_email='company@example.com',
            channel_access_token='secret-token',
        ),
        {'type': 'image', 'platformFileId': 'message/id'},
    )

    assert requested['url'].endswith('/message/message%2Fid/content')
    assert requested['headers']['Authorization'] == 'Bearer secret-token'
    assert requested['form'].meta['content_type'] == 'image/png'
    assert result['mimeType'] == 'image/png'
    assert result['size'] == 9
    assert result['fileId'] == requested['form'].id


@pytest.mark.asyncio
async def test_rich_menu_company_boundary_checks_company_id_and_email(monkeypatch):
    channel = SimpleNamespace(
        company_email='company@example.com',
        company_user_id='company-a',
    )

    async def get_channel(channel_id):
        return channel

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)

    assert (
        await _require_company_channel(
            'channel-id',
            'company@example.com',
            'company-a',
        )
        is channel
    )
    with pytest.raises(HTTPException) as error:
        await _require_company_channel(
            'channel-id',
            'company@example.com',
            'company-b',
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_v2_postback_resolves_current_authorized_version(monkeypatch):
    channel = SimpleNamespace(id='channel-id')

    async def options(*args, **kwargs):
        return [_workflow(1)]

    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)

    resolved = await _line_resolve_workflow_postback(
        channel,
        {'a': 'workflow.launch.v2', 'w': 'workflow-1'},
        SimpleNamespace(member_status='active'),
    )
    denied = await _line_resolve_workflow_postback(
        channel,
        {'a': 'workflow.launch.v2', 'w': 'another-company-workflow'},
        SimpleNamespace(member_status='active'),
    )

    assert resolved == _workflow(1)
    assert denied is None


@pytest.mark.asyncio
async def test_guided_form_collects_typed_fields_in_schema_order(monkeypatch):
    option = {
        **_workflow(1),
        'launchMode': 'form_input',
        'instruction': '請設定報表條件。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'title': '筆數', 'minimum': 1, 'maximum': 10},
                'department': {
                    'type': 'string',
                    'title': '部門',
                    'enum': ['sales', 'support'],
                },
            },
            'required': ['limit'],
            'additionalProperties': False,
        },
        'defaultInput': {},
        'fileRules': {},
        'requiresConfirmation': False,
    }
    session_input = _line_guided_input(option)
    assert _line_next_guided_field(option, session_input) == 'limit'
    session = SimpleNamespace(
        id='session-id',
        status='collecting',
        current_field='limit',
        input=session_input,
    )
    updates = []

    async def update_session(session_id, **kwargs):
        updates.append(kwargs)
        return SimpleNamespace(id=session_id, **kwargs)

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.update_workflow_session',
        update_session,
    )

    result = await _line_advance_workflow_session(option, session, '5', [])

    assert result['status'] == 'prompt'
    assert updates[-1]['session_input']['data']['limit'] == 5
    assert updates[-1]['current_field'] == 'department'
    assert '第 2/2 項：部門' in result['text']


@pytest.mark.asyncio
async def test_guided_input_cancel_does_not_execute(monkeypatch):
    option = {
        **_workflow(1),
        'launchMode': 'text_input',
        'instruction': '請輸入問題。',
        'inputSchema': {
            'type': 'object',
            'properties': {'message': {'type': 'string', 'title': '問題'}},
            'required': ['message'],
            'additionalProperties': False,
        },
        'defaultInput': {},
        'fileRules': {},
    }
    deleted = []

    async def delete_session(session_id, **kwargs):
        deleted.append(session_id)
        return True

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.delete_workflow_session',
        delete_session,
    )
    session = SimpleNamespace(
        id='session-id',
        status='collecting',
        current_field='message',
        input=_line_guided_input(option),
    )

    result = await _line_advance_workflow_session(option, session, '取消', [])

    assert result['status'] == 'cancelled'
    assert deleted == ['session-id']


@pytest.mark.asyncio
async def test_guided_file_input_waits_for_explicit_finish_when_multiple_files_allowed(monkeypatch):
    option = {
        **_workflow(1),
        'launchMode': 'file_input',
        'instruction': '請上傳要整理的附件。',
        'inputSchema': {
            'type': 'object',
            'properties': {},
            'required': [],
            'additionalProperties': False,
        },
        'defaultInput': {},
        'fileRules': {'maxFiles': 3, 'maxSizeMB': 25, 'allowedMimeTypes': []},
        'requiresConfirmation': False,
    }
    updates = []

    async def update_session(session_id, **kwargs):
        updates.append(kwargs)
        return SimpleNamespace(id=session_id, **kwargs)

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.update_workflow_session',
        update_session,
    )
    session = SimpleNamespace(
        id='session-id',
        status='collecting',
        current_field='files',
        input=_line_guided_input(option),
    )

    first = await _line_advance_workflow_session(
        option,
        session,
        '[LINE file]',
        [{'type': 'file', 'filename': 'report.csv', 'platformFileId': 'line-file-1'}],
    )

    assert first['status'] == 'prompt'
    assert '目前已收到 1 個' in first['text']
    assert updates[-1]['current_field'] == 'files'

    session.input = updates[-1]['session_input']
    finished = await _line_advance_workflow_session(option, session, '完成上傳', [])

    assert finished['status'] == 'execute'
    assert finished['input']['files'][0]['filename'] == 'report.csv'


@pytest.mark.asyncio
async def test_rich_menu_sync_replaces_old_menu_only_after_new_default(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='token',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=True,
        status='pending',
        rich_menu_id='old-menu',
        content_hash='old-hash',
        workflow_ids=['old-workflow'],
        liff_uri=None,
    )
    calls = []
    updates = []
    commits = []

    async def get_channel(channel_id):
        return channel

    async def get_state(channel_id):
        return state

    async def options(*args, **kwargs):
        return [_workflow(1)]

    async def line_request(channel, method, url, **kwargs):
        calls.append((method, url))
        return {'richMenuId': 'new-menu'} if url.endswith('/v2/bot/richmenu') else {}

    async def update_status(channel_id, **kwargs):
        updates.append(kwargs)
        return SimpleNamespace(status=kwargs['status'])

    async def commit_menu_set(**kwargs):
        commits.append(kwargs)
        return SimpleNamespace(status='active')

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.update_rich_menu_status', update_status)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.commit_rich_menu_set', commit_menu_set)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_request)

    result = await sync_line_rich_menu('channel-id')

    assert result['ok'] is True
    set_default_index = next(index for index, call in enumerate(calls) if '/user/all/richmenu/new-menu' in call[1])
    delete_old_index = next(index for index, call in enumerate(calls) if call[1].endswith('/old-menu'))
    assert set_default_index < delete_old_index
    assert commits[-1]['rich_menu_id'] == 'new-menu'
    assert len(commits[-1]['variants']) == 6


@pytest.mark.asyncio
async def test_rich_menu_sync_failure_keeps_previous_active_menu(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='token',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=True,
        status='pending',
        rich_menu_id='old-menu',
        content_hash='old-hash',
        workflow_ids=['old-workflow'],
        liff_uri=None,
    )
    updates = []

    async def get_channel(channel_id):
        return channel

    async def get_state(channel_id):
        return state

    async def options(*args, **kwargs):
        return [_workflow(1)]

    async def line_request(*args, **kwargs):
        raise RuntimeError('LINE unavailable')

    async def update_status(channel_id, **kwargs):
        updates.append(kwargs)
        return SimpleNamespace(status=kwargs['status'])

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.update_rich_menu_status', update_status)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_request)

    result = await sync_line_rich_menu('channel-id')

    assert result['ok'] is False
    assert updates[-1]['status'] == 'error'
    assert updates[-1]['rich_menu_id'] == 'old-menu'
    assert updates[-1]['content_hash'] == 'old-hash'


@pytest.mark.asyncio
async def test_rich_menu_restores_previous_default_when_state_persistence_fails(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='token',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=True,
        status='active',
        rich_menu_id='old-menu',
        content_hash='old-hash',
        workflow_ids=['old-workflow'],
        liff_uri=None,
    )
    calls = []

    async def get_channel(channel_id):
        return channel

    async def get_state(channel_id):
        return state

    async def options(*args, **kwargs):
        return [_workflow(1)]

    async def line_request(channel, method, url, **kwargs):
        calls.append((method, url))
        return {'richMenuId': 'new-menu'} if url.endswith('/v2/bot/richmenu') else {}

    update_count = 0

    async def update_status(channel_id, **kwargs):
        nonlocal update_count
        update_count += 1
        return SimpleNamespace(status=kwargs['status'])

    async def fail_commit(*args, **kwargs):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.update_rich_menu_status', update_status)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.commit_rich_menu_set', fail_commit)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_request)

    result = await sync_line_rich_menu('channel-id')

    assert result['ok'] is False
    set_new = next(index for index, call in enumerate(calls) if '/user/all/richmenu/new-menu' in call[1])
    restore_old = next(index for index, call in enumerate(calls) if '/user/all/richmenu/old-menu' in call[1])
    delete_new = next(
        index for index, call in enumerate(calls) if call[0] == 'DELETE' and call[1].endswith('/new-menu')
    )
    assert set_new < restore_old < delete_new
    assert update_count >= 1


@pytest.mark.asyncio
async def test_rich_menu_failure_restores_user_menu_and_removes_new_aliases(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='token',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=True,
        status='active',
        rich_menu_id='old-default',
        content_hash='old-hash',
        workflow_ids=['old-workflow'],
        liff_uri=None,
    )
    previous = SimpleNamespace(
        audience='member',
        tab='home',
        alias_id='old-member-alias',
        rich_menu_id='old-member-home',
        content_hash='old-member-hash',
    )
    binding = SimpleNamespace(
        external_user_id='line-user',
        member_role='member',
        member_status='active',
    )
    calls = []
    created_count = 0

    async def get_channel(*args):
        return channel

    async def get_state(*args):
        return state

    async def previous_variants(*args):
        return [previous]

    async def bindings(*args, **kwargs):
        return [binding]

    async def options(*args, **kwargs):
        return [_workflow(1)]

    async def line_request(channel, method, url, **kwargs):
        nonlocal created_count
        calls.append((method, url, kwargs))
        if url.endswith('/v2/bot/richmenu'):
            created_count += 1
            return {'richMenuId': f'new-menu-{created_count}'}
        return {}

    async def fail_commit(*args, **kwargs):
        raise RuntimeError('database unavailable')

    async def update_status(channel_id, **kwargs):
        return SimpleNamespace(status=kwargs['status'])

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_rich_menu_variants',
        previous_variants,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_line_identity_bindings',
        bindings,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.commit_rich_menu_set', fail_commit)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.update_rich_menu_status',
        update_status,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_request)

    result = await sync_line_rich_menu('channel-id')

    assert result['ok'] is False
    assigned_new = next(
        index
        for index, call in enumerate(calls)
        if call[0] == 'POST' and '/user/line-user/richmenu/new-menu-' in call[1]
    )
    restored_old = next(
        index for index, call in enumerate(calls) if call[0] == 'POST' and call[1].endswith('/richmenu/old-member-home')
    )
    assert assigned_new < restored_old
    assert any(method == 'DELETE' and '/richmenu/alias/' in url for method, url, _ in calls)
    assert sum(method == 'DELETE' and '/v2/bot/richmenu/new-menu-' in url for method, url, _ in calls) == 6


@pytest.mark.asyncio
async def test_successful_rich_menu_rekey_cleanup_removes_stale_alias(monkeypatch):
    channel = SimpleNamespace(
        id='new-channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='token',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=True,
        status='pending',
        rich_menu_id='old-menu',
        content_hash='old-hash',
        workflow_ids=[],
        liff_uri=None,
    )
    previous = SimpleNamespace(
        audience='guest',
        tab='home',
        alias_id='alias-from-old-channel-id',
        rich_menu_id='old-menu',
        content_hash='old-content',
    )
    calls = []
    created_count = 0

    async def get_channel(*args):
        return channel

    async def get_state(*args):
        return state

    async def previous_variants(*args):
        return [previous]

    async def options(*args, **kwargs):
        return []

    async def line_request(channel, method, url, **kwargs):
        nonlocal created_count
        calls.append((method, url))
        if url.endswith('/v2/bot/richmenu'):
            created_count += 1
            return {'richMenuId': f'new-menu-{created_count}'}
        return {}

    async def commit(*args, **kwargs):
        return SimpleNamespace(status='active')

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.InteractChannels.list_rich_menu_variants',
        previous_variants,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.commit_rich_menu_set', commit)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_workflow_options', options)
    monkeypatch.setattr('open_webui.routers.interact_channels._line_api_request', line_request)

    result = await sync_line_rich_menu('new-channel-id')

    assert result['ok'] is True
    assert (
        'DELETE',
        'https://api.line.me/v2/bot/richmenu/alias/alias-from-old-channel-id',
    ) in calls


@pytest.mark.asyncio
async def test_disabling_menu_without_token_reports_error_and_keeps_remote_state(monkeypatch):
    channel = SimpleNamespace(
        id='channel-id',
        channel_type='line',
        enabled=True,
        reply_mode='ai',
        channel_access_token='',
        name='LINE 客服',
    )
    state = SimpleNamespace(
        enabled=False,
        status='active',
        rich_menu_id='old-menu',
        content_hash='old-hash',
        workflow_ids=['old-workflow'],
        liff_uri=None,
    )
    updates = []

    async def get_channel(channel_id):
        return channel

    async def get_state(channel_id):
        return state

    async def update_status(channel_id, **kwargs):
        updates.append(kwargs)
        return SimpleNamespace(status=kwargs['status'])

    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_by_id', get_channel)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.get_rich_menu', get_state)
    monkeypatch.setattr('open_webui.routers.interact_channels.InteractChannels.update_rich_menu_status', update_status)

    result = await sync_line_rich_menu('channel-id')

    assert result['ok'] is False
    assert result['status'] == 'error'
    assert 'access token' in result['error']
    assert updates[-1]['rich_menu_id'] == 'old-menu'
    assert updates[-1]['content_hash'] == 'old-hash'
