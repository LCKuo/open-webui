import json
import ast
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from open_webui.models.models import ModelModel
from open_webui.utils import crm_model_catalog as catalog
from open_webui.utils.interact_crm_auth import assert_crm_company_context


def model(id='owned-bd', owner='owner', base='old'):
    return ModelModel(id=id, user_id=owner, base_model_id=base, name=id, is_active=True,
                      params={'system': 'keep company prompt'} if base else {},
                      meta={'managedUseCases': ['prospecting_discovery']} if base else {},
                      access_grants=[], created_at=1, updated_at=1)


def response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request('POST', 'https://example.com'))


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        catalog._inventory = None
        catalog._checks.clear()
        catalog._check_locks.clear()
        catalog._inventory_lock = __import__('asyncio').Lock()
        catalog._selection_lock = __import__('asyncio').Lock()
        self.user = SimpleNamespace(id='owner')
        self.inv = {'ids': ['moonshotai/kimi-k3'], 'fingerprint': 'fingerprint', 'at': time.time()}
        for method, value in [('get', None), ('get_many', {}), ('upsert', None)]:
            mocked = patch.object(catalog.Config, method, AsyncMock(return_value=value))
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_display_privacy_and_specialist_filter(self):
        entry = catalog.catalog_item('nvidia/nemotron-3-super-120b-a12b')
        self.assertNotIn('nvidia', json.dumps(entry).lower())
        self.assertEqual(len(entry['id']), 24)
        self.assertTrue(entry['preferred'])
        self.assertTrue(catalog.EXCLUDED.search('nvidia/nv-embedqa'))
        self.assertTrue(catalog.EXCLUDED.search('meta/llama-guard-4'))
        self.assertFalse(catalog.EXCLUDED.search('moonshotai/kimi-k3'))

    def test_current_gpt_tool_models_are_preferred(self):
        for model_id in (
            'own-123.gpt-5.6-luna',
            'own-123.gpt-5.6-sol',
            'own-123.gpt-5.6-terra',
            'own-123.gpt-6-astra',
        ):
            self.assertTrue(catalog.catalog_item(model_id)['preferred'], model_id)
        self.assertFalse(catalog.catalog_item('own-123.gpt-image-2')['preferred'])

    def test_readiness_history_does_not_disappear_after_five_minutes(self):
        self.assertEqual(catalog.health_status({'status': 'ready', 'checkedAt': time.time() - 400}), 'ready')
        self.assertEqual(catalog.health_status({'status': 'ready', 'checkedAt': time.time() - 3600}), 'ready')
        self.assertEqual(catalog.health_status({'status': 'ready', 'checkedAt': time.time() - 90000}), 'stale')
        self.assertEqual(catalog.health_status({'status': 'busy', 'checkedAt': time.time() - 600}), 'busy')

    def test_probe_wait_budget_covers_slow_model_before_crm_transport_timeout(self):
        self.assertGreaterEqual(catalog.PROBE_REQUEST_SECONDS, 40)
        self.assertGreaterEqual(catalog.PROBE_TOTAL_SECONDS, 2 * catalog.PROBE_REQUEST_SECONDS)
        self.assertLess(catalog.PROBE_TOTAL_SECONDS, 120)

    async def test_catalog_loads_persisted_checks_after_process_restart(self):
        check = {'ok': True, 'status': 'ready', 'checkedAt': time.time() - 400}
        with patch.object(catalog, 'inventory', AsyncMock(return_value=self.inv)), \
             patch.object(catalog.Config, 'get_many', AsyncMock(return_value={catalog.health_key(self.inv, self.inv['ids'][0]): check})), \
             patch.object(catalog.Models, 'get_model_by_id', AsyncMock(return_value=None)), \
             patch.object(catalog, 'role_agent', AsyncMock(side_effect=HTTPException(409, 'No agent'))):
            result = await catalog.catalog(self.user)
            self.assertEqual(result['items'][0]['status'], 'ready')
            self.assertEqual(result['items'][0]['checkedAt'], check['checkedAt'])

    def test_failed_preferred_model_cannot_rank_above_verified_models(self):
        models = [{'name': 'Kimi', 'status': 'busy', 'preferred': True},
                  {'name': 'Unknown', 'status': 'unchecked', 'preferred': True},
                  {'name': 'Working', 'status': 'ready', 'preferred': False}]
        self.assertEqual([item['name'] for item in sorted(models, key=catalog.model_sort_key)], ['Working', 'Unknown', 'Kimi'])

    def test_does_not_share_company_agents_or_custom_prompts(self):
        self.assertFalse(catalog.safe_base_record(model()))
        base = model(base=None)
        self.assertTrue(catalog.safe_base_record(base))
        base.params.system = 'private company secret'
        self.assertFalse(catalog.safe_base_record(base))

    async def test_live_inventory_removal_blocks_cached_selection(self):
        with patch.object(catalog, 'inventory', AsyncMock(return_value={'ids': []})):
            with self.assertRaises(HTTPException) as error:
                await catalog.resolve_selection(catalog.selection_id('moonshotai/kimi-k3'))
            self.assertEqual(error.exception.status_code, 409)

    async def test_missing_models_list_fails_closed(self):
        client = AsyncMock()
        client.get.return_value = response(200, {'data': []})
        with patch.object(catalog, 'connection', AsyncMock(return_value=('https://example.com', 'secret'))), \
             patch.object(catalog.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            with self.assertRaises(HTTPException):
                await catalog.inventory()
            self.assertIsNone(catalog._inventory)

    async def run_probe(self, replies, model_id='moonshotai/kimi-k3'):
        client = AsyncMock()
        client.post.side_effect = replies
        with patch.object(catalog, 'connection', AsyncMock(return_value=('https://example.com', 'secret'))), \
             patch.object(catalog.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            result = await catalog.probe(self.user, model_id, self.inv)
        return result, client

    async def test_provider_removed_busy_and_auth_failures(self):
        for code, expected in [(404, 'unavailable'), (403, 'denied'), (429, 'busy'), (504, 'busy')]:
            catalog._checks.clear()
            result, client = await self.run_probe([response(code, {})])
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], expected)
            self.assertEqual(client.post.await_count, 1)

    async def test_tool_roundtrip_is_required(self):
        first = {'choices': [{'message': {'role': 'assistant', 'content': None, 'reasoning_content': 'opaque state',
                 'tool_calls': [{'id': 'tool-1', 'type': 'function', 'function': {'name': 'confirm_readiness', 'arguments': '{"ready":true}'}}]}}]}
        result, client = await self.run_probe([response(200, first), response(200, {'choices': [{'message': {'content': 'MODEL_READY'}}]})])
        self.assertTrue(result['ok'])
        self.assertEqual(client.post.await_count, 2)
        self.assertEqual(client.post.call_args.kwargs['json']['messages'][1]['reasoning_content'], 'opaque state')

    async def test_luna_probe_uses_modern_openai_token_limit(self):
        model_id = 'own-d0a1d8c476.gpt-5.6-luna'
        self.inv['ids'] = [model_id]
        first = {'choices': [{'message': {'role': 'assistant', 'content': None,
                 'tool_calls': [{'id': 'tool-1', 'type': 'function', 'function': {'name': 'confirm_readiness', 'arguments': '{"ready":true}'}}]}}]}
        result, client = await self.run_probe(
            [response(200, first), response(200, {'choices': [{'message': {'content': 'MODEL_READY'}}]})],
            model_id,
        )

        self.assertTrue(result['ok'])
        self.assertEqual(client.post.await_count, 2)
        for call in client.post.await_args_list:
            payload = call.kwargs['json']
            self.assertEqual(payload['model'], 'gpt-5.6-luna')
            self.assertEqual(payload['max_completion_tokens'], 1024)
            self.assertNotIn('max_tokens', payload)

    async def test_terra_probe_disables_reasoning_for_chat_completion_tools(self):
        model_id = 'own-d0a1d8c476.gpt-5.6-terra'
        self.inv['ids'] = [model_id]
        first = {'choices': [{'message': {'role': 'assistant', 'content': None,
                 'tool_calls': [{'id': 'tool-1', 'type': 'function', 'function': {'name': 'confirm_readiness', 'arguments': '{"ready":true}'}}]}}]}
        result, client = await self.run_probe(
            [response(200, first), response(200, {'choices': [{'message': {'content': 'MODEL_READY'}}]})],
            model_id,
        )

        self.assertTrue(result['ok'])
        for call in client.post.await_args_list:
            self.assertEqual(call.kwargs['json']['reasoning_effort'], 'none')

    def test_legacy_probe_keeps_max_tokens(self):
        self.assertEqual(catalog.probe_token_limit('moonshotai/kimi-k3'), {'max_tokens': 1024})

    async def test_empty_answer_is_not_a_success(self):
        result, _ = await self.run_probe([response(200, {'choices': [{'message': {'content': ''}}]})])
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'incompatible')

    async def test_agent_lookup_stays_in_company(self):
        with patch.object(catalog.Models, 'get_models_by_user_id', AsyncMock(return_value=[model(owner='foreign')])):
            with self.assertRaises(HTTPException):
                await catalog.role_agent(self.user, 'bd')

    async def test_failure_never_changes_agent_or_grants(self):
        with patch.object(catalog, 'resolve_selection', AsyncMock(return_value=('moonshotai/kimi-k3', self.inv))), \
             patch.object(catalog, 'role_agent', AsyncMock(return_value=model())), \
             patch.object(catalog.Models, 'get_model_by_id', AsyncMock(return_value=None)), \
             patch.object(catalog, 'stored_check', AsyncMock(return_value={'ok': False, 'status': 'busy', 'checkedAt': time.time()})), \
             patch.object(catalog, 'authorize_inventory', AsyncMock()) as grants, \
             patch.object(catalog.Models, 'update_model_by_id', AsyncMock()) as update:
            result = await catalog.select_model(None, self.user, 'bd', 'id')
            self.assertFalse(result['ok'])
            grants.assert_not_awaited()
            update.assert_not_awaited()

    async def test_success_preserves_agent_prompt_knowledge_and_grants(self):
        from open_webui.utils import models as runtime_models
        from open_webui import events
        agent = model()
        agent.meta.knowledge = [{'id': 'private-knowledge'}]
        session = AsyncMock()
        session.__aenter__.return_value.scalar.return_value = None
        with patch.object(catalog, 'resolve_selection', AsyncMock(return_value=('moonshotai/kimi-k3', self.inv))), \
             patch.object(catalog, 'role_agent', AsyncMock(return_value=agent)), \
             patch.object(catalog.Models, 'get_model_by_id', AsyncMock(side_effect=[None, agent])), \
             patch.object(catalog, 'stored_check', AsyncMock(return_value={'ok': True, 'status': 'ready', 'checkedAt': time.time()})), \
             patch.object(catalog, 'probe', AsyncMock(side_effect=AssertionError('Selection must not wait for inference'))), \
             patch.object(catalog, 'authorize_inventory', AsyncMock()), \
             patch.object(catalog, 'get_async_db_context', return_value=session), \
             patch.object(catalog.Models, 'update_model_by_id', AsyncMock(return_value=agent)) as update, \
             patch.object(runtime_models, 'refresh_runtime_model_cache_entry'), \
             patch.object(runtime_models, 'get_all_models', AsyncMock()), \
             patch.object(events, 'publish_event', AsyncMock()):
            result = await catalog.select_model(None, self.user, 'bd', 'id')
            self.assertTrue(result['ok'])
            form = update.call_args.args[1]
            self.assertEqual(form.base_model_id, 'moonshotai/kimi-k3')
            self.assertEqual(form.params.system, 'keep company prompt')
            self.assertEqual(form.meta.knowledge, agent.meta.knowledge)
            self.assertEqual(form.access_grants, agent.access_grants)
            self.assertTrue(form.meta.model_dump()[catalog.CATALOG_MARKER])

    async def test_stale_selection_queues_check_without_changing_agent(self):
        with patch.object(catalog, 'resolve_selection', AsyncMock(return_value=('moonshotai/kimi-k3', self.inv))), \
             patch.object(catalog, 'role_agent', AsyncMock(return_value=model())), \
             patch.object(catalog.Models, 'get_model_by_id', AsyncMock(return_value=None)), \
             patch.object(catalog, 'stored_check', AsyncMock(return_value={'ok': True, 'status': 'ready', 'checkedAt': time.time() - 90000})), \
             patch.object(catalog, 'queue_checks', AsyncMock(return_value={'ok': False, 'status': 'checking'})) as queued, \
             patch.object(catalog, 'probe', AsyncMock()) as probe, \
             patch.object(catalog.Models, 'update_model_by_id', AsyncMock()) as update:
            result = await catalog.select_model(None, self.user, 'bd', 'id')
            self.assertEqual(result['status'], 'checking')
            queued.assert_awaited_once()
            probe.assert_not_awaited()
            update.assert_not_awaited()

    def test_scan_lease_recovers_after_process_interrupt(self):
        state = {'id': 'job', 'status': 'running', 'updatedAt': time.time() - 121}
        self.assertEqual(catalog.scan_public(state)['status'], 'interrupted')
        state['updatedAt'] = time.time()
        self.assertEqual(catalog.scan_public(state)['status'], 'running')

    def test_verified_models_rank_by_tool_roundtrip_speed(self):
        models = [{'name': 'Slow', 'status': 'ready', 'preferred': True, 'elapsedSeconds': 40},
                  {'name': 'Fast', 'status': 'ready', 'preferred': True, 'elapsedSeconds': 5}]
        self.assertEqual(sorted(models, key=catalog.model_sort_key)[0]['name'], 'Fast')

    async def test_background_worker_caps_concurrency_at_two(self):
        import asyncio
        todo, active, maximum, finished = ['one', 'two', 'three'], 0, 0, []
        async def step(key, job, operation):
            if operation == 'take':
                return todo.pop(0) if todo else None
            finished.append(operation)
        async def probe(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
        with patch.object(catalog, 'scan_step', side_effect=step), patch.object(catalog, 'probe', side_effect=probe):
            catalog._probe_slots = asyncio.Semaphore(2)
            await catalog.run_scan(self.user, self.inv, {'id': 'job'})
        self.assertEqual(maximum, 2)
        self.assertEqual(finished.count('finish'), 3)

    async def test_sync_grants_only_private_read_access(self):
        other = model(id='moonshotai/kimi-k3', owner='other-company', base=None)
        with patch.object(catalog, 'inventory', AsyncMock(return_value=self.inv)), \
             patch.object(catalog.Models, 'get_model_by_id', AsyncMock(return_value=other)), \
             patch.object(catalog.AccessGrants, 'grant_access', AsyncMock()) as grant:
            await catalog.authorize_inventory(self.user)
            grant.assert_awaited_once_with('model', other.id, 'user', self.user.id, 'read')

    def test_cross_company_claims_are_rejected(self):
        with self.assertRaises(HTTPException):
            assert_crm_company_context({'company_email': 'company-a@example.com', 'company_user_id': 'a', 'webui_user_id': 'a'},
                                       company_email='company-b@example.com', company_user_id='b', webui_user_id='b')

    def test_provider_errors_are_not_reported_as_empty_output(self):
        from pathlib import Path
        from starlette.responses import Response
        source = (Path(catalog.__file__).parents[1] / 'routers/workflows.py').read_text(encoding='utf-8')
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_response_data')
        ns = {'Any': object, 'json': json}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<response test>', 'exec'), ns)
        result = ns['_response_data'](Response('', status_code=429))
        self.assertIn('error', result)
        self.assertIn('額度', result['error'])
        self.assertEqual(result['usage']['total_tokens'], 0)

    async def test_sales_cannot_select_company_model(self):
        from pathlib import Path
        from typing import Any
        source = (Path(catalog.__file__).parents[1] / 'routers/workflows.py').read_text(encoding='utf-8')
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'crm_model_catalog')
        node.decorator_list = []
        for arg in node.args.args:
            arg.annotation = None
        node.returns = None
        node.args.defaults = [ast.Constant(None)] * len(node.args.defaults)
        ns = {'HTTPException': HTTPException, '_authorize_service_or_crm': lambda *args: {'crm_user_role': 'sales'},
              '_resolve_service_user': AsyncMock(return_value=self.user), '_assert_crm_request_context': lambda *args: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<route test>', 'exec'), ns)
        form = SimpleNamespace(action='select', companyEmail='owner@example.com', modelId='id', productRole='bd')
        with self.assertRaises(HTTPException) as error:
            await ns['crm_model_catalog'](None, form)
        self.assertEqual(error.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
