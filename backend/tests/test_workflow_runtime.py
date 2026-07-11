import pytest
from types import SimpleNamespace

from open_webui.routers.interact_channels import (
    ChannelHealthRequest,
    ProvisionAccountRequest,
    _line_result_messages,
    _wechat_media_response,
    provision_account,
    channel_health,
)
from open_webui.utils.interact_billing import estimate_reserved_tokens
from open_webui.utils.workflow_runtime import (
    WorkflowRuntimeError,
    execute_workflow_graph,
    normalize_workflow_input,
)


def test_normalize_workflow_input_preserves_multimedia_parts():
    result = normalize_workflow_input(
        {
            'message': 'summarize this',
            'parts': [
                {'type': 'audio', 'url': 'https://example.test/audio.mp3'},
                {'type': 'video', 'platform': 'telegram', 'platformFileId': 'video-1'},
            ],
            'files': [
                {
                    'id': 'file-1',
                    'name': 'report.pdf',
                    'content_type': 'application/pdf',
                }
            ],
        }
    )

    assert [part['type'] for part in result['parts']] == ['text', 'audio', 'video', 'file']
    assert result['parts'][2]['platformFileId'] == 'video-1'
    assert result['parts'][3]['fileId'] == 'file-1'


@pytest.mark.asyncio
async def test_runtime_routes_media_to_typed_output():
    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'channel_input'}},
            {'id': 'media', 'data': {'type': 'media_output'}},
        ],
        'edges': [{'source': 'input', 'target': 'media'}],
    }

    result = await execute_workflow_graph(
        graph,
        {'parts': [{'type': 'image', 'url': 'https://example.test/image.jpg', 'alt': 'receipt'}]},
    )

    assert result['outputs'] == [{'type': 'image', 'url': 'https://example.test/image.jpg', 'alt': 'receipt'}]


@pytest.mark.asyncio
async def test_runtime_rejects_nodes_without_an_executor():
    graph = {
        'nodes': [{'id': 'request', 'data': {'type': 'http_request'}}],
        'edges': [],
    }

    with pytest.raises(WorkflowRuntimeError, match='http_request'):
        await execute_workflow_graph(graph, {'message': 'run query'})


@pytest.mark.asyncio
async def test_system_prompt_is_forwarded_without_replacing_user_input():
    captured = {}

    async def model_runner(prompt, system_prompt, model_id, parts):
        captured.update(prompt=prompt, system_prompt=system_prompt, model_id=model_id)
        return {'text': 'ok', 'model_id': model_id, 'usage': {}}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'system', 'data': {'type': 'system_prompt', 'config': {'text': 'Be concise.'}}},
            {'id': 'model', 'data': {'type': 'chat_model', 'config': {'model_id': 'model-a'}}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'system'},
            {'source': 'system', 'target': 'model'},
            {'source': 'model', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(
        graph,
        {'message': 'hello'},
        model_runner=model_runner,
    )

    assert captured == {
        'prompt': 'hello',
        'system_prompt': 'Be concise.',
        'model_id': 'model-a',
    }
    assert result['outputs'] == [{'type': 'text', 'text': 'ok'}]


@pytest.mark.asyncio
async def test_database_node_uses_registered_acl_runtime():
    captured = {}

    async def node_runner(node_type, config, incoming, workflow_input):
        captured.update(node_type=node_type, config=config, message=workflow_input['message'])
        return {'ok': True, 'rows': [{'id': 1}]}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {
                'id': 'database',
                'data': {
                    'type': 'database_query',
                    'config': {'connector_id': 'connector-a', 'table': 'crm.customers'},
                },
            },
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'database'},
            {'source': 'database', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(
        graph,
        {'message': 'list customers'},
        node_runner=node_runner,
    )

    assert captured['node_type'] == 'database_query'
    assert captured['config']['table'] == 'crm.customers'
    assert result['outputs'] == [{'type': 'json', 'value': {'ok': True, 'rows': [{'id': 1}]}}]


def test_line_adapter_uses_native_image_and_falls_back_for_files():
    messages = _line_result_messages(
        {
            'content': 'done',
            'outputs': [
                {'type': 'image', 'url': 'https://example.test/image.jpg'},
                {'type': 'file', 'filename': 'report.pdf', 'url': 'https://example.test/report.pdf'},
            ],
        }
    )

    assert messages[0] == {'type': 'text', 'text': 'done'}
    assert messages[1]['type'] == 'image'
    assert messages[2] == {
        'type': 'text',
        'text': 'report.pdf: https://example.test/report.pdf',
    }


def test_workflow_billing_reservation_scales_with_model_steps():
    base_input, base_output, _ = estimate_reserved_tokens(
        {'messages': [{'role': 'user', 'content': 'hello'}], 'max_completion_tokens': 10}
    )
    scaled_input, scaled_output, _ = estimate_reserved_tokens(
        {
            'messages': [{'role': 'user', 'content': 'hello'}],
            'max_completion_tokens': 10,
            '_billing_multiplier': 3,
        }
    )

    assert scaled_input == base_input * 3
    assert scaled_output == base_output * 3


def test_wechat_adapter_can_reuse_native_media_id():
    xml = _wechat_media_response('official-account', 'external-user', 'audio', 'media-1')

    assert '<MsgType><![CDATA[voice]]></MsgType>' in xml
    assert '<Voice><MediaId><![CDATA[media-1]]></MediaId></Voice>' in xml


@pytest.mark.asyncio
async def test_account_provision_awaits_password_hash(monkeypatch):
    captured = {}
    user = SimpleNamespace(id='user-1', email='new@example.com', name='New User', role='user')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )

    async def get_user_by_email(email):
        return None

    async def hash_password(password):
        return f'hashed:{password}'

    async def insert_auth(**kwargs):
        captured.update(kwargs)
        return user

    async def update_user(user_id, updates):
        return user

    async def assign_group(*args, **kwargs):
        return None

    async def get_config(key):
        assert key == 'ui.default_group_id'
        return ''

    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', get_user_by_email)
    monkeypatch.setattr('open_webui.routers.interact_channels.get_password_hash', hash_password)
    monkeypatch.setattr('open_webui.routers.interact_channels.Auths.insert_new_auth', insert_auth)
    monkeypatch.setattr('open_webui.routers.interact_channels.Users.update_user_by_id', update_user)
    monkeypatch.setattr('open_webui.routers.interact_channels.apply_default_group_assignment', assign_group)
    monkeypatch.setattr('open_webui.routers.interact_channels.Config.get', get_config)

    result = await provision_account(
        SimpleNamespace(),
        ProvisionAccountRequest(
            email='new@example.com',
            companyName='Example Company',
            contactName='New User',
            accountType='owner',
        ),
        None,
        None,
    )

    assert captured['password'].startswith('hashed:')
    assert result['created'] is True
    assert result['temporaryPassword']


@pytest.mark.asyncio
async def test_account_provision_repairs_user_without_credentials(monkeypatch):
    captured = {}
    user = SimpleNamespace(id='user-1', email='orphan@example.com', name='Orphan User', role='user')

    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )

    async def get_user_by_email(email):
        return user

    async def hash_password(password):
        return f'hashed:{password}'

    async def ensure_auth(user_id, email, password):
        captured.update(user_id=user_id, email=email, password=password)
        return True

    async def update_user(user_id, updates):
        return user

    async def get_config(key):
        return ''

    async def assign_group(*args, **kwargs):
        return None

    monkeypatch.setattr('open_webui.routers.interact_channels.Users.get_user_by_email', get_user_by_email)
    monkeypatch.setattr('open_webui.routers.interact_channels.get_password_hash', hash_password)
    monkeypatch.setattr('open_webui.routers.interact_channels.Auths.ensure_auth_for_existing_user', ensure_auth)
    monkeypatch.setattr('open_webui.routers.interact_channels.Users.update_user_by_id', update_user)
    monkeypatch.setattr('open_webui.routers.interact_channels.Config.get', get_config)
    monkeypatch.setattr('open_webui.routers.interact_channels.apply_default_group_assignment', assign_group)

    result = await provision_account(
        SimpleNamespace(),
        ProvisionAccountRequest(
            email='orphan@example.com',
            companyName='Example Company',
            contactName='Orphan User',
            accountType='owner',
        ),
        None,
        None,
    )

    assert captured['password'].startswith('hashed:')
    assert result['created'] is False
    assert result['temporaryPassword']


@pytest.mark.asyncio
async def test_binding_health_allows_account_to_be_created_later(monkeypatch):
    monkeypatch.setattr(
        'open_webui.routers.interact_channels._require_service_token',
        lambda *args: None,
    )
    monkeypatch.setattr(
        'open_webui.routers.interact_channels.is_billing_enabled',
        lambda: True,
    )

    async def missing_user(email):
        return None

    monkeypatch.setattr(
        'open_webui.routers.interact_channels.Users.get_user_by_email',
        missing_user,
    )
    monkeypatch.setattr('open_webui.routers.interact_channels._channel_worker_expected', 1)
    monkeypatch.setattr('open_webui.routers.interact_channels._channel_worker_tasks', {object()})

    result = await channel_health(
        ChannelHealthRequest(
            companyEmail=' New@Example.com ',
            allowMissingUser=True,
        ),
        None,
        None,
    )

    assert result['serviceTokenVerified'] is True
    assert result['billingConfigured'] is True
    assert result['accountRequired'] is True
    assert result['userVerified'] is False
    assert result['workersReady'] is True


@pytest.mark.asyncio
async def test_runtime_limits_model_execution_fan_out():
    nodes = [{'id': 'input', 'data': {'type': 'chat_input'}}]
    nodes.extend({'id': f'model-{index}', 'data': {'type': 'chat_model'}} for index in range(9))

    with pytest.raises(WorkflowRuntimeError, match='8 model execution nodes'):
        await execute_workflow_graph({'nodes': nodes, 'edges': []}, {'message': 'hello'})
