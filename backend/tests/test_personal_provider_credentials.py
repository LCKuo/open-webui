import unittest
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import open_webui.models.provider_credentials as credential_models
import pytest
import pytest_asyncio
from open_webui.models.provider_credentials import (
    UserProviderCredential,
    provider_connection_id,
)
from open_webui.routers import openai, tasks, workflows
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.utils import crm_model_catalog
from open_webui.utils import middleware as chat_middleware
from open_webui.utils.interact_billing import BillingIdentity, InteractBillingClient
from open_webui.utils import models as model_utils
from open_webui.utils.models import get_filtered_models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

webui_main = import_module('open_webui.main')


@pytest.mark.asyncio
async def test_personal_model_pipeline_uses_request_local_inventory():
    model_id = 'chengsyin-bd-agent'
    request = SimpleNamespace(
        state=SimpleNamespace(runtime_models={model_id: {'id': model_id}}),
        app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
    )
    user = SimpleNamespace(id='user-1', email='user@example.com', name='User', role='user')
    payload = {'model': model_id, 'messages': [{'role': 'user', 'content': 'Analyze MD'}]}

    assert chat_middleware._models_for_request(request) == request.state.runtime_models
    assert await process_pipeline_inlet_filter(
        request, payload, user, chat_middleware._models_for_request(request)
    ) == payload


def test_shared_model_pipeline_falls_back_to_global_inventory():
    shared = {'shared-model': {'id': 'shared-model'}}
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=shared)))
    assert chat_middleware._models_for_request(request) is shared


@pytest_asyncio.fixture
async def credential_store(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return
        async with sessions() as session:
            yield session

    monkeypatch.setattr(credential_models, 'get_async_db_context', session_context)
    async with engine.begin() as connection:
        await connection.run_sync(UserProviderCredential.__table__.create)
    yield credential_models.UserProviderCredentials, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_credentials_are_encrypted_and_isolated_by_user(credential_store):
    store, sessions = credential_store
    fields = {
        'name': 'My OpenAI',
        'provider': 'openai',
        'base_url': 'https://api.openai.com/v1',
        'auth_type': 'bearer',
    }
    first = await store.upsert('user-a', api_key='personal-secret-a', **fields)
    second = await store.upsert('user-b', api_key='personal-secret-b', **fields)

    assert await store.get_api_key('user-a', first.connection_id) == 'personal-secret-a'
    assert await store.get_api_key('user-b', second.connection_id) == 'personal-secret-b'

    async with sessions() as session:
        rows = (await session.execute(select(UserProviderCredential))).scalars().all()
    assert {row.key_last4 for row in rows} == {'et-a', 'et-b'}
    assert all('personal-secret' not in row.api_key_encrypted for row in rows)

    assert await store.delete('user-a', first.connection_id) is True
    assert await store.has_for_user('user-a') is False
    assert await store.has_for_user('user-b') is True


@pytest.mark.asyncio
async def test_no_auth_self_hosted_connection_has_no_fake_key(credential_store):
    store, _ = credential_store
    item = await store.upsert(
        'user-a',
        name='Office LLM',
        provider='custom',
        base_url='http://llm.example.test/v1/',
        auth_type='none',
        api_key=None,
    )
    runtime = await store.get_runtime_connections('user-a')

    assert item.base_url == 'http://llm.example.test/v1'
    assert item.key_last4 == ''
    assert runtime[0]['key'] == ''
    assert runtime[0]['config']['auth_type'] == 'none'
    assert runtime[0]['config']['personal_owner_id'] == 'user-a'


class PersonalProviderCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id='user-1')
        self.url = 'https://personal.example.com/v1'
        self.personal = {
            'url': self.url,
            'key': 'personal-secret',
            'config': {
                'enable': True,
                'auth_type': 'bearer',
                'prefix_id': 'own-12345678',
                'user_supplied': True,
                'personal_owner_id': self.user.id,
                'personal_connection_id': '1234567890',
                'personal_connection_name': 'My Provider',
            },
        }

    def test_connection_identity_ignores_cosmetic_url_formatting(self):
        self.assertEqual(
            provider_connection_id(self.url, {'provider': 'custom', 'auth_type': 'bearer'}),
            provider_connection_id(
                'HTTPS://PERSONAL.EXAMPLE.COM/v1/',
                {'provider': 'custom', 'auth_type': 'bearer', 'prefix_id': 'ignored'},
            ),
        )

    async def test_personal_connections_replace_the_platform_set(self):
        with (
            patch.object(
                openai.UserProviderCredentials,
                'get_runtime_connections',
                AsyncMock(return_value=[self.personal]),
            ),
            patch.object(openai, 'get_openai_runtime_config', AsyncMock()) as platform_config,
        ):
            personal_mode, connections = await openai.get_openai_runtime_connections(self.user)

        self.assertTrue(personal_mode)
        self.assertEqual(connections, [self.personal])
        platform_config.assert_not_awaited()

    async def test_crm_warm_check_only_queues_the_selected_role_model(self):
        selected_agent = SimpleNamespace(base_model_id='own-12345678.gpt-5.6-terra')
        form = SimpleNamespace(
            companyEmail='owner@example.com',
            companyUserId=self.user.id,
            action='warm',
            productRole='bd',
            scope='preferred',
        )
        claims = {
            'crm_user_role': 'owner',
            'company_email': form.companyEmail,
            'company_user_id': self.user.id,
            'webui_user_id': self.user.id,
        }
        resolved_user = SimpleNamespace(id=self.user.id, email=form.companyEmail)
        with (
            patch.object(workflows, '_authorize_service_or_crm', return_value=claims),
            patch.object(workflows, '_resolve_service_user', AsyncMock(return_value=resolved_user)),
            patch.object(workflows, '_assert_crm_request_context'),
            patch.object(crm_model_catalog, 'role_agent', AsyncMock(return_value=selected_agent)),
            patch.object(
                crm_model_catalog,
                'queue_checks',
                AsyncMock(return_value={'status': 'complete'}),
            ) as queue,
        ):
            result = await workflows.crm_model_catalog(None, form, authorization='service', db=AsyncMock())

        self.assertEqual(result['status'], 'complete')
        queue.assert_awaited_once_with(resolved_user, [selected_agent.base_model_id])

    async def test_missing_personal_connection_uses_platform_configuration(self):
        platform = (True, ['https://platform.example.com/v1'], ['shared-secret'], {'0': {'enable': True}})
        with (
            patch.object(
                openai.UserProviderCredentials,
                'get_runtime_connections',
                AsyncMock(return_value=[]),
            ),
            patch.object(openai, 'get_openai_runtime_config', AsyncMock(return_value=platform)),
        ):
            personal_mode, connections = await openai.get_openai_runtime_connections(self.user)

        self.assertFalse(personal_mode)
        self.assertEqual(connections[0]['key'], 'shared-secret')
        self.assertEqual(connections[0]['url'], 'https://platform.example.com/v1')

    async def test_personal_model_inventory_is_prefixed_owned_and_not_globally_cached(self):
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'platform-model': {'id': 'platform-model'}}))
        )
        with (
            patch.object(
                openai,
                'get_openai_runtime_connections',
                AsyncMock(return_value=(True, [self.personal])),
            ),
            patch.object(
                openai,
                'get_models_request',
                AsyncMock(return_value={'data': [{'id': 'model-1', 'name': 'Model One'}]}),
            ) as request_models,
        ):
            result = await openai.get_all_models(request, self.user)

        self.assertTrue(result['personal_mode'])
        self.assertEqual(result['data'][0]['id'], 'own-12345678.model-1')
        self.assertEqual(result['data'][0]['personal_owner_id'], self.user.id)
        self.assertEqual(result['data'][0]['name'], 'Model One · My Provider')
        self.assertEqual(request.app.state.OPENAI_MODELS, {'platform-model': {'id': 'platform-model'}})
        self.assertEqual(request_models.await_args.args[2], 'personal-secret')

    async def test_get_connection_routes_by_the_current_users_personal_index(self):
        with patch.object(
            openai,
            'get_openai_runtime_connections',
            AsyncMock(return_value=(True, [self.personal])),
        ):
            url, key, config = await openai.get_openai_connection(0, user=self.user)

        self.assertEqual(url, self.url)
        self.assertEqual(key, 'personal-secret')
        self.assertEqual(config['personal_owner_id'], self.user.id)

    async def test_crm_catalog_uses_only_personal_connection_models(self):
        crm_model_catalog._inventory = None
        with (
            patch.object(
                crm_model_catalog,
                'personal_connections',
                AsyncMock(return_value=[self.personal]),
            ),
            patch.object(
                openai,
                'get_models_request',
                AsyncMock(return_value={'data': [{'id': 'model-1'}, {'id': 'embedding-model'}]}),
            ),
        ):
            result = await crm_model_catalog.inventory(force=True, user=self.user)

        self.assertEqual(result['ids'], ['own-12345678.model-1'])
        self.assertTrue(result['personal'])

    async def test_personal_model_is_visible_without_global_model_record(self):
        personal_model = {
            'id': 'own-12345678.model-1',
            'personal_owner_id': self.user.id,
        }
        user = SimpleNamespace(id=self.user.id, role='user')

        self.assertEqual(await get_filtered_models([personal_model], user), [personal_model])

    async def test_background_tasks_reload_only_the_users_personal_models(self):
        personal_model = {
            'id': 'own-12345678.model-1',
            'personal_owner_id': self.user.id,
        }
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(MODELS={'platform-model': {'id': 'platform-model'}})
            ),
        )
        with (
            patch.object(
                tasks,
                'get_runtime_models_for_user',
                AsyncMock(return_value={personal_model['id']: personal_model}),
            ),
        ):
            models = await tasks._task_models_for_request(
                request,
                self.user,
                personal_model['id'],
            )

        self.assertEqual(models, {personal_model['id']: personal_model})
        self.assertEqual(request.state.runtime_models, models)
        self.assertFalse(getattr(request.state, 'direct', False))

    async def test_personal_mode_keeps_compatible_agent_presets_request_local(self):
        personal_base = {
            'id': 'own-12345678.gpt-5.6-terra',
            'name': 'GPT-5.6 Terra',
            'owned_by': 'openai',
            'personal_owner_id': self.user.id,
            'personal_connection_id': 'connection-1',
            'provider': 'openai',
            'urlIdx': 0,
        }

        def custom_model(model_id, base_model_id):
            params = SimpleNamespace(model_dump=lambda: {})
            meta = SimpleNamespace(model_dump=lambda: {})
            return SimpleNamespace(
                id=model_id,
                base_model_id=base_model_id,
                name=model_id,
                created_at=1,
                is_active=True,
                params=params,
                meta=meta,
                model_dump=lambda: {
                    'id': model_id,
                    'base_model_id': base_model_id,
                    'params': {},
                    'meta': {},
                },
            )

        compatible = custom_model('chengsyin-bd-agent', personal_base['id'])
        platform_only = custom_model('other-agent', 'platform-model')
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={'platform-model': {'id': 'platform-model'}},
                    BASE_MODELS=[{'id': 'platform-model'}],
                )
            )
        )
        with (
            patch.object(
                model_utils.Config,
                'get_many',
                AsyncMock(
                    return_value={
                        'models.base_models_cache': True,
                        'evaluation.arena.enable': False,
                        'evaluation.arena.models': [],
                        'models.default_metadata': {},
                    }
                ),
            ),
            patch.object(
                model_utils.UserProviderCredentials,
                'has_for_user',
                AsyncMock(return_value=True),
            ),
            patch.object(
                model_utils,
                'fetch_openai_models',
                AsyncMock(return_value=[personal_base]),
            ),
            patch.object(
                model_utils.Models,
                'get_models_by_user_id',
                AsyncMock(return_value=[compatible, platform_only]),
            ),
            patch.object(
                model_utils.Functions,
                'get_active_function_ids_by_type',
                AsyncMock(return_value=[]),
            ),
            patch.object(
                model_utils.Functions,
                'get_functions_by_ids',
                AsyncMock(return_value=[]),
            ),
            patch.object(
                model_utils.Functions,
                'get_function_valves_by_ids',
                AsyncMock(return_value={}),
            ),
        ):
            result = await model_utils.get_all_models(request, user=self.user)

        by_id = {item['id']: item for item in result}
        self.assertIn(personal_base['id'], by_id)
        self.assertIn(compatible.id, by_id)
        self.assertNotIn(platform_only.id, by_id)
        self.assertEqual(by_id[compatible.id]['personal_owner_id'], self.user.id)
        self.assertEqual(by_id[compatible.id]['urlIdx'], 0)
        self.assertEqual(request.app.state.MODELS, {'platform-model': {'id': 'platform-model'}})

    async def test_standalone_personal_background_task_is_billed(self):
        personal_model = {
            'id': 'own-12345678.model-1',
            'user_supplied': True,
            'personal_owner_id': self.user.id,
        }
        user = SimpleNamespace(id=self.user.id, role='user')
        request = SimpleNamespace(
            state=SimpleNamespace(direct=True, model=personal_model),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
        )
        payload = {
            'model': personal_model['id'],
            'messages': [{'role': 'user', 'content': 'hello'}],
        }
        authorization = SimpleNamespace(request_id='billing-request')
        billing_client = SimpleNamespace(
            authorize=AsyncMock(return_value=authorization),
            commit=AsyncMock(),
            cancel=AsyncMock(),
        )
        response = {
            'choices': [{'message': {'content': 'hi'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 1},
        }

        with (
            patch.object(tasks, 'is_billing_enabled', return_value=True),
            patch.object(tasks, 'InteractBillingClient', return_value=billing_client),
            patch.object(tasks, 'check_model_access', AsyncMock()),
            patch.object(tasks, 'generate_chat_completion', AsyncMock(return_value=response)),
        ):
            result = await tasks._generate_task_completion(request, payload, user)

        self.assertEqual(result, response)
        billing_client.authorize.assert_awaited_once_with(user, payload, ANY)
        billing_client.commit.assert_awaited_once()
        billing_client.cancel.assert_not_awaited()
        self.assertFalse(request.state.interact_billing_parent_active)

    async def test_webui_api_key_calling_personal_model_is_billed_as_external_api(self):
        personal_model = {
            'id': 'own-12345678.model-1',
            'name': 'My Provider Model',
            'personal_owner_id': self.user.id,
            'info': {'meta': {}},
        }
        user = SimpleNamespace(
            id=self.user.id,
            role='user',
            email='owner@example.com',
            name='Owner',
        )
        request = SimpleNamespace(
            state=SimpleNamespace(
                auth_type='api_key',
                api_key_id='key-webui-client',
            ),
            headers={'user-agent': 'regression-test'},
            app=SimpleNamespace(
                state=SimpleNamespace(MODELS={}, redis=None),
            ),
        )
        form_data = {
            'model': personal_model['id'],
            'messages': [{'role': 'user', 'content': 'hello'}],
            'stream': False,
        }
        provider_response = {
            'choices': [{'message': {'content': 'personal response'}}],
            'usage': {'prompt_tokens': 17, 'completion_tokens': 9},
        }
        billing_client = InteractBillingClient()
        billing_client.resolve_identity = AsyncMock(
            return_value=BillingIdentity(company_user={'id': 'company-1'})
        )
        billing_client._request = AsyncMock(
            side_effect=[
                {
                    'allowed': True,
                    'reservation_id': 'reservation-1',
                    'reserved_tokens': 12345,
                },
                {'ok': True},
            ]
        )

        async def keep_processed_payload(_request, payload, _user, metadata, _model):
            return payload, metadata, []

        with (
            patch.object(
                webui_main.UserProviderCredentials,
                'has_for_user',
                AsyncMock(return_value=True),
            ),
            patch.object(
                webui_main,
                'get_runtime_models_for_user',
                AsyncMock(return_value={personal_model['id']: personal_model}),
            ),
            patch.object(webui_main.Models, 'get_model_by_id', AsyncMock(return_value=None)),
            patch.object(webui_main.Config, 'get', AsyncMock(return_value={})),
            patch.object(webui_main, 'check_model_access', AsyncMock()),
            patch('open_webui.utils.automations._resolve_model_tool_ids', return_value=[]),
            patch('open_webui.utils.automations._resolve_model_filter_ids', return_value=[]),
            patch('open_webui.utils.automations._resolve_model_features', AsyncMock(return_value={})),
            patch('open_webui.utils.automations._resolve_model_terminal_id', return_value=None),
            patch.object(webui_main, 'process_chat_payload', side_effect=keep_processed_payload),
            patch.object(
                webui_main,
                'chat_completion_handler',
                AsyncMock(return_value=provider_response),
            ),
            patch.object(
                webui_main,
                'build_chat_response_context',
                AsyncMock(return_value={'assistant_message': {}}),
            ),
            patch.object(
                webui_main,
                'process_chat_response',
                AsyncMock(return_value=provider_response),
            ),
            patch.object(webui_main, 'is_billing_enabled', return_value=True),
            patch.object(webui_main, 'InteractBillingClient', return_value=billing_client),
        ):
            result = await webui_main.chat_completion(request, form_data, user)

        self.assertEqual(result, provider_response)
        self.assertFalse(getattr(request.state, 'direct', False))
        self.assertEqual(request.state.runtime_models, {personal_model['id']: personal_model})
        self.assertEqual(billing_client._request.await_count, 2)

        authorize_call, commit_call = billing_client._request.await_args_list
        self.assertEqual(authorize_call.args[1], '/api/integrations/open-webui/usage/authorize')
        authorize_payload = authorize_call.args[2]
        self.assertEqual(authorize_payload['model'], personal_model['id'])
        self.assertEqual(authorize_payload['usage_channel'], 'external_api')
        self.assertEqual(authorize_payload['metadata']['apiKeyId'], 'key-webui-client')

        self.assertEqual(commit_call.args[1], '/api/integrations/open-webui/usage/commit')
        commit_payload = commit_call.args[2]
        self.assertEqual(commit_payload['model'], personal_model['id'])
        self.assertEqual(commit_payload['usage_channel'], 'external_api')
        self.assertEqual(commit_payload['parameters']['apiKeyId'], 'key-webui-client')
        self.assertEqual(commit_payload['usage']['input_tokens'], 17)
        self.assertEqual(commit_payload['usage']['output_tokens'], 9)
        self.assertEqual(commit_payload['usage']['billable_tokens'], 71)

    async def test_personal_embedding_is_billed_like_platform_embedding(self):
        personal_model = {
            'id': 'own-12345678.embedding-model',
            'user_supplied': True,
            'personal_owner_id': self.user.id,
        }
        user = SimpleNamespace(id=self.user.id, role='user')
        request = SimpleNamespace(
            state=SimpleNamespace(auth_type='jwt', api_key_id=None),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
        )
        form_data = {'model': personal_model['id'], 'input': ['hello']}
        authorization = SimpleNamespace(request_id='embedding-billing-request')
        billing_client = SimpleNamespace(
            authorize=AsyncMock(return_value=authorization),
            commit=AsyncMock(),
            cancel=AsyncMock(),
        )
        response = {
            'data': [{'embedding': [0.1, 0.2]}],
            'usage': {'prompt_tokens': 1, 'total_tokens': 1},
        }

        with (
            patch.object(
                webui_main.UserProviderCredentials,
                'has_for_user',
                AsyncMock(return_value=True),
            ),
            patch.object(webui_main, 'get_all_models', AsyncMock(return_value=[personal_model])),
            patch.object(webui_main, 'is_billing_enabled', return_value=True),
            patch.object(webui_main, 'InteractBillingClient', return_value=billing_client),
            patch.object(webui_main, 'generate_embeddings', AsyncMock(return_value=response)),
        ):
            result = await webui_main.embeddings(request, form_data, user)

        self.assertEqual(result, response)
        billing_client.authorize.assert_awaited_once()
        billing_client.commit.assert_awaited_once()
        billing_client.cancel.assert_not_awaited()

    async def test_crm_workflow_with_personal_models_still_authorizes_billing(self):
        service_user = SimpleNamespace(id=self.user.id)
        workflow = SimpleNamespace(id='workflow-1', name='CRM AI')
        run = SimpleNamespace(id='run-1')
        run_form = SimpleNamespace(
            input={'company': 'Example'},
            workflow_version_id='version-1',
            model_id='own-12345678.model-1',
            trigger_type='crm',
        )
        claims = {
            'company_user_id': 'company-1',
            'crm_instance_id': 'crm-1',
            'jti': 'request-1',
        }
        authorization = SimpleNamespace(company_user_id='company-1')
        billing_client = SimpleNamespace(
            authorize=AsyncMock(return_value=authorization),
            cancel=AsyncMock(),
        )
        billing_form = {
            'model': run_form.model_id,
            '_billing_model_attempts': 1,
            '_billing_multiplier': 1,
        }

        with (
            patch.object(workflows, 'is_billing_enabled', return_value=True),
            patch.object(
                workflows,
                'workflow_billing_form_data',
                AsyncMock(return_value=billing_form),
            ),
            patch.object(workflows, 'InteractBillingClient', return_value=billing_client),
        ):
            client, result, form, metadata = await workflows._authorize_crm_workflow_billing(
                service_user,
                workflow,
                run,
                run_form,
                claims,
            )

        self.assertIs(client, billing_client)
        self.assertIs(result, authorization)
        self.assertEqual(form, billing_form)
        self.assertEqual(metadata['workflow']['runId'], run.id)
        billing_client.authorize.assert_awaited_once()
        billing_client.cancel.assert_not_awaited()

    async def test_custom_no_auth_connection_is_supported(self):
        form = openai.PersonalAPIKeyForm(
            name='Office LLM',
            provider='custom',
            base_url='https://llm.example.com/v1/',
            auth_type='none',
        )
        with patch.object(openai, 'validate_url', return_value=True):
            provider, base_url, auth_type = await openai.prepare_personal_connection(form)

        self.assertEqual(provider, 'custom')
        self.assertEqual(base_url, 'https://llm.example.com/v1/')
        self.assertEqual(auth_type, 'none')


if __name__ == '__main__':
    unittest.main()
