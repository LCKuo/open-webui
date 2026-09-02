import json
from types import SimpleNamespace

import pytest
from open_webui.routers.interact_channels import (
    ChannelHealthRequest,
    ProvisionAccountRequest,
    _line_mobile_text,
    _line_result_messages,
    _line_workflow_resume_action,
    _wechat_media_response,
    channel_health,
    provision_account,
)
from open_webui.utils.interact_billing import estimate_reserved_tokens
from open_webui.utils.line_rich_menu import parse_line_postback_data
from open_webui.utils.workflow_runtime import (
    PROSPECTING_DISCOVERY_CONTRACT,
    WorkflowPause,
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
async def test_unconnected_guidance_node_is_not_echoed_as_fallback_output():
    async def model_runner(prompt, system_prompt, model_id, parts):
        return {'text': 'model result', 'model_id': model_id, 'usage': {}}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'model', 'data': {'type': 'chat_model', 'config': {'model_id': 'model-a'}}},
            {
                'id': 'guidance',
                'data': {
                    'type': 'user_input',
                    'config': {'launch': {'mode': 'text_input'}},
                },
            },
        ],
        'edges': [{'source': 'input', 'target': 'model'}],
    }

    result = await execute_workflow_graph(
        graph,
        {'message': 'private input'},
        model_runner=model_runner,
    )

    assert result['outputs'] == [{'type': 'text', 'text': 'model result'}]


@pytest.mark.asyncio
async def test_prompt_template_serializes_structured_chat_context_as_json():
    captured = {}

    async def model_runner(prompt, system_prompt, model_id, parts):
        captured['prompt'] = prompt
        return {'text': 'ok', 'model_id': model_id, 'usage': {}}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {
                'id': 'prompt',
                'data': {
                    'type': 'prompt_template',
                    'config': {'template': 'History: {{chat_history}}\nQuestion: {{message}}'},
                },
            },
            {'id': 'model', 'data': {'type': 'chat_model', 'config': {'model_id': 'model-a'}}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'prompt'},
            {'source': 'prompt', 'target': 'model'},
            {'source': 'model', 'target': 'output'},
        ],
    }

    await execute_workflow_graph(
        graph,
        {
            'message': 'current question',
            'context': {'chat_history': [{'role': 'user', 'content': 'earlier question'}]},
        },
        model_runner=model_runner,
    )

    assert captured['prompt'] == (
        'History: [{"role": "user", "content": "earlier question"}]\n'
        'Question: current question'
    )


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


@pytest.mark.asyncio
async def test_semantic_query_node_uses_the_registered_runtime():
    captured = {}

    async def node_runner(node_type, config, incoming, workflow_input):
        captured.update(node_type=node_type, config=config)
        return {'ok': True, 'rows': [{'salesperson': 'Amy', 'revenue': 1200}]}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {
                'id': 'query',
                'data': {
                    'type': 'semantic_query',
                    'config': {'dataset_id': 'sales', 'plan': {'datasetId': 'sales'}},
                },
            },
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'query'},
            {'source': 'query', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(graph, {'message': 'sales ranking'}, node_runner=node_runner)

    assert captured['node_type'] == 'semantic_query'
    assert captured['config']['dataset_id'] == 'sales'
    assert result['outputs'][0]['value']['rows'][0]['salesperson'] == 'Amy'


@pytest.mark.asyncio
async def test_knowledge_query_node_uses_the_registered_acl_runtime():
    captured = {}

    async def node_runner(node_type, config, incoming, workflow_input):
        captured.update(
            node_type=node_type,
            config=config,
            incoming=incoming,
            message=workflow_input['message'],
        )
        return [{'content': 'Refunds take three business days.', 'source': 'policy.pdf'}]

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {
                'id': 'knowledge',
                'data': {
                    'type': 'knowledge_query',
                    'config': {'knowledge_ids': ['kb-policy'], 'count': 3},
                },
            },
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'knowledge'},
            {'source': 'knowledge', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(
        graph,
        {'message': 'How long do refunds take?'},
        node_runner=node_runner,
    )

    assert captured['node_type'] == 'knowledge_query'
    assert captured['config']['knowledge_ids'] == ['kb-policy']
    assert captured['message'] == 'How long do refunds take?'
    assert result['outputs'][0]['value']['source'] == 'policy.pdf'


@pytest.mark.asyncio
async def test_web_search_node_uses_secure_runtime_and_preserves_json_output():
    captured = {}

    async def node_runner(node_type, config, incoming, workflow_input):
        captured.update(node_type=node_type, data=workflow_input['data'])
        return {
            'queries': workflow_input['data']['search_queries'],
            'results': [{'title': 'Acme', 'url': 'https://acme.example'}],
        }

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {'id': 'search', 'data': {'type': 'web_search', 'config': {'max_queries': 3}}},
            {'id': 'output', 'data': {'type': 'webhook_response'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'search'},
            {'source': 'search', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(
        graph,
        {'data': {'search_queries': ['custom parts manufacturer Taiwan']}},
        node_runner=node_runner,
    )

    assert captured['node_type'] == 'web_search'
    assert captured['data']['search_queries'] == ['custom parts manufacturer Taiwan']
    assert result['outputs'][0]['type'] == 'json'
    assert result['outputs'][0]['value']['results'][0]['title'] == 'Acme'


@pytest.mark.asyncio
async def test_json_parse_accepts_fenced_model_output_and_rejects_invalid_json():
    async def model_runner(prompt, system_prompt, model_id, parts):
        return {
            'text': '```json\n{"version":"1","candidates":[]}\n```',
            'model_id': model_id,
            'usage': {},
        }

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {'id': 'model', 'data': {'type': 'agent', 'config': {'model_id': 'model-a'}}},
            {'id': 'parse', 'data': {'type': 'json_parse'}},
            {'id': 'output', 'data': {'type': 'webhook_response'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'model'},
            {'source': 'model', 'target': 'parse'},
            {'source': 'parse', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(graph, {'data': {}}, model_runner=model_runner)
    assert result['outputs'][0]['value'] == {'version': '1', 'candidates': []}

    invalid_graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {
                'id': 'prompt',
                'data': {
                    'type': 'prompt_template',
                    'config': {'template': 'not json'},
                },
            },
            {'id': 'parse', 'data': {'type': 'json_parse'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'prompt'},
            {'source': 'prompt', 'target': 'parse'},
        ],
    }
    with pytest.raises(WorkflowRuntimeError, match='valid JSON'):
        await execute_workflow_graph(invalid_graph, {'message': 'not json'})


@pytest.mark.asyncio
async def test_prospecting_contract_retries_empty_output_and_normalizes_schema_drift():
    calls = 0

    async def model_runner(prompt, system_prompt, model_id, parts):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'text': '', 'model_id': model_id, 'usage': {'total_tokens': 1}}
        return {
            'text': '''{
                "version": 1,
                "candidates": [{
                    "name": "範例自動化有限公司",
                    "taxId": "22099131",
                    "industry": "自動化設備",
                    "website": "https://example.com",
                    "businessActivities": [{
                        "category": "equipment",
                        "label": "客製自動化設備",
                        "evidenceUrl": "https://example.com",
                        "evidenceExcerpt": "提供客製自動化設備設計與製造服務。",
                        "confidence": 82
                    }],
                    "suggestedEntryPoints": [{
                        "name": "PEEK 耐磨件",
                        "rationale": "客製自動化設備可能使用滑塊、軸套或耐磨導引件。",
                        "supportingFacts": ["公開網站證實設計及製造客製自動化設備"],
                        "assumptions": ["是否使用 PEEK 或其他工程塑料仍需確認"],
                        "evidenceUrls": ["https://example.com"],
                        "confidence": 68
                    }],
                    "structuralNeedScore": 60,
                    "capabilityFitScore": 70,
                    "timingScore": 20,
                    "evidenceScore": 60,
                    "commercialFitScore": 55,
                    "fitSummary": "官方網站顯示該公司設計及製造客製自動化設備。",
                    "uncertaintyNotes": "尚未確認目前採購時程。",
                    "evidence": [{
                        "type": "website",
                        "title": "產品服務",
                        "url": "https://example.com",
                        "excerpt": "提供客製自動化設備設計與製造服務。",
                        "supportsNeed": true,
                        "confidence": 80
                    }]
                }],
                "notes": [{"note": "僅使用公開來源。"}],
                "profileSuggestions": [{
                    "action": "add",
                    "field": "productKeywords",
                    "term": "客製自動化設備",
                    "reason": "公開來源顯示候選公司持續提供客製自動化設備。",
                    "confidence": 88,
                    "evidenceUrls": ["https://example.com"]
                }]
            }''',
            'model_id': model_id,
            'usage': {'total_tokens': 2},
        }

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {
                'id': 'model',
                'data': {
                    'type': 'agent',
                    'config': {
                        'model_id': 'model-a',
                        'output_contract': PROSPECTING_DISCOVERY_CONTRACT,
                        'max_attempts': 2,
                    },
                },
            },
            {
                'id': 'parse',
                'data': {
                    'type': 'json_parse',
                    'config': {'output_contract': PROSPECTING_DISCOVERY_CONTRACT},
                },
            },
            {'id': 'output', 'data': {'type': 'webhook_response'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'model'},
            {'source': 'model', 'target': 'parse'},
            {'source': 'parse', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(graph, {'message': 'find prospects'}, model_runner=model_runner)

    assert calls == 2
    assert result['outputs'][0]['value']['version'] == '1'
    assert result['outputs'][0]['value']['notes'] == ['僅使用公開來源。']
    assert result['outputs'][0]['value']['candidates'][0]['excluded'] is False
    assert result['outputs'][0]['value']['candidates'][0]['taxId'] == '22099131'
    assert result['outputs'][0]['value']['candidates'][0]['businessActivities'][0]['category'] == 'equipment'
    assert result['outputs'][0]['value']['candidates'][0]['suggestedEntryPoints'][0]['name'] == 'PEEK 耐磨件'
    assert result['outputs'][0]['value']['profileSuggestions'][0]['term'] == '客製自動化設備'


@pytest.mark.asyncio
async def test_runtime_exposes_usage_when_contract_retries_still_fail():
    async def model_runner(prompt, system_prompt, model_id, parts):
        return {
            'text': 'not-json',
            'model_id': model_id,
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12},
        }

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {
                'id': 'model',
                'data': {
                    'type': 'agent',
                    'config': {
                        'model_id': 'model-a',
                        'output_contract': PROSPECTING_DISCOVERY_CONTRACT,
                        'max_attempts': 2,
                    },
                },
            },
        ],
        'edges': [{'source': 'input', 'target': 'model'}],
    }
    meter = {}

    with pytest.raises(WorkflowRuntimeError, match='after 2 attempts'):
        await execute_workflow_graph(
            graph,
            {'message': 'find prospects'},
            model_runner=model_runner,
            execution_meter=meter,
        )

    assert meter['model_calls'] == 2
    assert meter['usage'] == {
        'prompt_tokens': 20,
        'completion_tokens': 4,
        'total_tokens': 24,
    }


@pytest.mark.asyncio
async def test_prospecting_contract_excludes_low_fit_candidates_before_enrichment():
    async def model_runner(prompt, system_prompt, model_id, parts):
        return {
            'text': '''{
                "version": "1",
                "candidates": [{
                    "name": "範例電鍍有限公司",
                    "structuralNeedScore": 20,
                    "capabilityFitScore": 15,
                    "timingScore": 20,
                    "evidenceScore": 35,
                    "commercialFitScore": 20,
                    "fitSummary": "該公司提供表面處理代工，並非目標設備製造商。",
                    "uncertaintyNotes": "未發現自有設備設計或製造能力。",
                    "excluded": false,
                    "evidence": [{
                        "title": "公司服務",
                        "url": "https://plating.example.com",
                        "excerpt": "提供金屬表面處理與電鍍代工服務。",
                        "supportsNeed": false,
                        "confidence": 60
                    }]
                }],
                "notes": []
            }''',
            'model_id': model_id,
            'usage': {},
        }

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'form_input'}},
            {'id': 'model', 'data': {'type': 'agent', 'config': {
                'model_id': 'model-a',
                'output_contract': PROSPECTING_DISCOVERY_CONTRACT,
            }}},
            {'id': 'output', 'data': {'type': 'webhook_response'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'model'},
            {'source': 'model', 'target': 'output'},
        ],
    }

    result = await execute_workflow_graph(graph, {'message': 'find prospects'}, model_runner=model_runner)
    candidate = result['outputs'][0]['value']
    parsed = json.loads(candidate['text'])
    assert parsed['candidates'][0]['excluded'] is True
    assert '能力適配度不足' in parsed['candidates'][0]['exclusionReason']


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


def test_line_adapter_renders_workflow_approval_as_signed_resume_actions():
    messages = _line_result_messages(
        {
            'outputs': [
                {
                    'type': 'card',
                    'title': '確認寄送郵件',
                    'body': '核准後才會寄送。',
                    'data': {
                        'workflow_id': 'workflow-1',
                        'run_id': 'run-1',
                        'revision': 2,
                        'prompt': {'preview': {'收件人': ['client@example.com'], '主旨': '通知'}},
                    },
                    'actions': [
                        {'label': '確認寄送', 'decision': 'approved'},
                        {'label': '取消', 'decision': 'rejected'},
                    ],
                }
            ]
        }
    )

    assert messages[0]['type'] == 'flex'
    buttons = messages[0]['contents']['footer']['contents']
    assert buttons[0]['action']['label'] == '確認寄送'
    parsed = _line_workflow_resume_action(parse_line_postback_data(buttons[0]['action']['data']))
    assert parsed == {
        'workflowId': 'workflow-1',
        'runId': 'run-1',
        'decision': 'approved',
        'revision': 2,
        'value': None,
    }


@pytest.mark.asyncio
async def test_user_choice_checkpoint_resumes_with_original_candidate_data():
    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {
                'id': 'choice',
                'data': {
                    'type': 'user_choice',
                    'config': {
                        'choices_from_path': 'data.candidates',
                        'choice_label_path': 'name',
                        'choice_value_path': 'id',
                    },
                },
            },
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'choice'},
            {'source': 'choice', 'target': 'output'},
        ],
    }
    payload = {'data': {'candidates': [{'id': 'customer-1', 'name': '第一公司'}]}}

    with pytest.raises(WorkflowPause) as paused:
        await execute_workflow_graph(graph, payload)

    result = await execute_workflow_graph(
        graph,
        payload,
        resume_state=paused.value.state,
        resume={'node_id': 'choice', 'decision': 'selected', 'value': 'customer-1'},
    )

    choice = result['outputs'][0]['value']
    assert choice['choice'] == 'customer-1'
    assert choice['selected']['name'] == '第一公司'


@pytest.mark.asyncio
async def test_approval_checkpoint_rejects_changed_payload_hash():
    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'approval', 'data': {'type': 'approval_gate'}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'approval'},
            {'source': 'approval', 'target': 'output'},
        ],
    }

    with pytest.raises(WorkflowPause) as paused:
        await execute_workflow_graph(graph, {'message': 'send this'})

    with pytest.raises(WorkflowRuntimeError, match='changed'):
        await execute_workflow_graph(
            graph,
            {'message': 'send this'},
            resume_state=paused.value.state,
            resume={
                'node_id': 'approval',
                'decision': 'approved',
                'payload_hash': 'tampered',
            },
        )


@pytest.mark.asyncio
async def test_resumed_runtime_reports_only_the_current_stage_for_billing():
    calls = 0

    async def model_runner(prompt, system_prompt, model_id, parts):
        nonlocal calls
        calls += 1
        usage = (
            {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}
            if calls == 1
            else {
                'input_tokens': 6,
                'output_tokens': 3,
                'total_tokens': 9,
                'output_tokens_details': {'reasoning_tokens': 2},
            }
        )
        return {'text': f'model output {calls}', 'model_id': model_id, 'usage': usage}

    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'draft', 'data': {'type': 'agent', 'config': {'model_id': 'model-a'}}},
            {'id': 'approval', 'data': {'type': 'approval_gate'}},
            {'id': 'finalize', 'data': {'type': 'agent', 'config': {'model_id': 'model-a'}}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'input', 'target': 'draft'},
            {'source': 'draft', 'target': 'approval'},
            {'source': 'approval', 'target': 'finalize'},
            {'source': 'finalize', 'target': 'output'},
        ],
    }

    with pytest.raises(WorkflowPause) as paused:
        await execute_workflow_graph(graph, {'message': 'prepare and approve'}, model_runner=model_runner)

    assert paused.value.state['execution_usage'] == {
        'prompt_tokens': 10,
        'completion_tokens': 4,
        'total_tokens': 14,
    }
    assert paused.value.state['model_calls'] == 1

    result = await execute_workflow_graph(
        graph,
        {'message': 'prepare and approve'},
        model_runner=model_runner,
        resume_state=paused.value.state,
        resume={
            'node_id': 'approval',
            'decision': 'approved',
            'payload_hash': paused.value.payload_hash,
        },
    )

    assert result['usage'] == {
        'prompt_tokens': 10,
        'completion_tokens': 4,
        'total_tokens': 23,
        'input_tokens': 6,
        'output_tokens': 3,
        'output_tokens_details': {'reasoning_tokens': 2},
    }
    assert result['execution_usage'] == {
        'input_tokens': 6,
        'output_tokens': 3,
        'total_tokens': 9,
        'output_tokens_details': {'reasoning_tokens': 2},
    }
    assert result['model_calls'] == 1


def test_line_adapter_converts_generic_markdown_table_to_mobile_list():
    converted = _line_mobile_text(
        '''| 排名 | 員工編號 | 員工姓名 | 跟進事件數 |
|:---:|:---|:---|---:|
| 1 | EMP002 | 林雅婷 | 5 |
| 2 | EMP003 | 陳俊宇 | 5 |

**說明：**
- 所有員工目前皆為 5 次。'''
    )

    assert '|' not in converted
    assert '1. 林雅婷' in converted
    assert '員工編號：EMP002' in converted
    assert '跟進事件數：5' in converted
    assert '- 所有員工目前皆為 5 次。' in converted
    assert _line_mobile_text('**查詢完成**') == '查詢完成'


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
