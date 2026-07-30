from types import SimpleNamespace

import pytest
from open_webui.routers.workflows import (
    _as_bool,
    _campaign_email_idempotency_material,
    _customer_contact_query_plan,
    _customer_request_context,
    _email_compose_context,
    _email_draft_contact_context,
    _email_knowledge_context,
    _email_campaign_policy,
    _crm_token_allows_workflow,
    _managed_model_id_for_use_case,
    _managed_prospecting_meta,
    _managed_prospecting_workflow_graph,
    _managed_prospecting_workflow_id,
    _parse_email_draft,
    _prioritize_web_search_fetch_results,
    _public_contacts_from_text,
    _validate_email_campaign_policy,
    _validate_email_draft_content,
    _validate_workflow_configuration,
    _workflow_multimodal_content,
)
from open_webui.semantic_query.contracts import QueryPlan
from open_webui.utils.workflow_runtime import WorkflowRuntimeError
from open_webui.utils.workflows import (
    WorkflowAccessContext,
    decide_workflow_candidates,
    normalize_workflow_meta,
    validate_workflow_agent_policy,
    validate_workflow_graph,
    workflow_acl_allows,
    workflow_agent_candidate,
    workflow_channel_acl_allows,
)


def _workflow(**overrides):
    values = {
        'id': 'workflow-a',
        'user_id': 'owner-a',
        'name': '發票付款狀態',
        'description': '查詢發票付款狀態',
        'visibility': 'shared',
        'status': 'published',
        'default_version_id': 'version-a',
        'graph': {
            'nodes': [
                {'id': 'input', 'data': {'type': 'chat_input'}},
                {'id': 'output', 'data': {'type': 'chat_output'}},
            ],
            'edges': [{'source': 'input', 'target': 'output'}],
        },
        'meta': {
            'acl': {
                'scope': 'company',
                'company_user_id': 'company-a',
                'allow_agent_selection': True,
                'intent_keywords': ['發票', '付款狀態'],
                'intent_examples': ['幫我查這張發票付了沒'],
                'required_keywords': [],
                'negative_keywords': [],
                'agent_selection_threshold': 0.64,
                'agent_selection_priority': 0,
                'agent_ambiguity_margin': 0.12,
            }
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(company_id='company-a'):
    return WorkflowAccessContext(user_id='member', role='user', company_user_id=company_id)


def test_managed_prospecting_workflow_is_deterministic_and_publishable():
    first_id = _managed_prospecting_workflow_id('company-a')
    graph = _managed_prospecting_workflow_graph('model-a')
    meta = _managed_prospecting_meta(
        owner_user_id='owner-a',
        company_user_id='company-a',
        graph=graph,
    )

    assert first_id == _managed_prospecting_workflow_id('company-a')
    assert first_id != _managed_prospecting_workflow_id('company-b')
    assert graph['purpose'] == 'prospecting_discovery'
    assert {node['data']['type'] for node in graph['nodes']} == {
        'user_input',
        'form_input',
        'web_search',
        'system_prompt',
        'agent',
        'json_parse',
        'prospect_contact_enrichment',
        'merge',
        'webhook_response',
    }
    assert next(
        node for node in graph['nodes'] if node['data']['type'] == 'agent'
    )['data']['config']['model_id'] == 'model-a'
    assert meta['managed']['read_only'] is True
    assert meta['acl']['scope'] == 'private'
    assert _validate_workflow_configuration(graph, 'private', meta, for_publish=True)['ok'] is True


def test_managed_prospecting_model_prefers_declared_use_case():
    models = [
        {'id': 'default-model'},
        {
            'id': 'prospecting-agent',
            'info': {'meta': {'managedUseCases': ['prospecting_discovery']}},
        },
    ]

    assert (
        _managed_model_id_for_use_case(
            models,
            ['default-model', 'prospecting-agent'],
            'prospecting_discovery',
        )
        == 'prospecting-agent'
    )
    assert (
        _managed_model_id_for_use_case(
            models,
            ['default-model'],
            'prospecting_discovery',
        )
        is None
    )


def test_managed_prospecting_workflow_is_implicitly_granted_only_to_its_company():
    workflow = _workflow(
        id=_managed_prospecting_workflow_id('company-a'),
        meta={
            'managed': {
                'provider': 'interact_crm',
                'key': 'interact.crm.prospecting.discovery',
            },
            'acl': {'company_user_id': 'company-a'},
        },
    )
    restricted_claims = {
        'allowed_workflow_ids': ['some-other-workflow'],
        'company_user_id': 'company-a',
    }

    assert _crm_token_allows_workflow(restricted_claims, workflow) is True
    assert _crm_token_allows_workflow(
        {**restricted_claims, 'company_user_id': 'company-b'},
        workflow,
    ) is False


def test_semantic_boolean_values_do_not_treat_false_text_as_opt_out():
    assert _as_bool(True)
    assert _as_bool('yes')
    assert not _as_bool(False)
    assert not _as_bool('false')
    assert not _as_bool('0')


def test_web_search_fetches_one_result_per_query_before_deeper_results():
    results = [
        {'url': 'query-a-1', '_query_order': 0, '_result_rank': 0},
        {'url': 'query-a-2', '_query_order': 0, '_result_rank': 1},
        {'url': 'query-b-1', '_query_order': 1, '_result_rank': 0},
        {'url': 'query-b-2', '_query_order': 1, '_result_rank': 1},
    ]

    prioritized = _prioritize_web_search_fetch_results(results, 3)

    assert [item['url'] for item in prioritized] == ['query-a-1', 'query-b-1', 'query-a-2']


def test_campaign_email_idempotency_is_company_and_connector_scoped():
    base = {
        'connector_id': 'connector-1',
        'workflow_id': 'workflow-1',
        'campaign_id': 12,
        'recipient_id': 34,
    }

    company_a = _campaign_email_idempotency_material(company_user_id='company-a', **base)
    company_b = _campaign_email_idempotency_material(company_user_id='company-b', **base)
    connector_b = _campaign_email_idempotency_material(
        company_user_id='company-a',
        **{**base, 'connector_id': 'connector-2'},
    )

    assert company_a != company_b
    assert company_a != connector_b


def test_campaign_connector_policy_enforces_limit_cooldown_and_unsubscribe():
    connector = SimpleNamespace(
        id='connector-1',
        recipient_policy={
            'max_campaign_recipients': 50,
            'campaign_cooldown_days': 21,
            'require_unsubscribe': True,
        },
    )

    assert _email_campaign_policy(connector)['campaign_cooldown_days'] == 21
    accepted = {
        'recipient_count': 50,
        'cooldown_days': 21,
        'unsubscribe_url': 'https://crm.example/unsubscribe/1?t=signed',
    }
    assert _validate_email_campaign_policy(accepted, connector)['max_campaign_recipients'] == 50

    for rejected in [
        {**accepted, 'recipient_count': 51},
        {**accepted, 'cooldown_days': 20},
        {**accepted, 'unsubscribe_url': ''},
    ]:
        with pytest.raises(WorkflowRuntimeError):
            _validate_email_campaign_policy(rejected, connector)


def test_public_contact_extraction_requires_traceable_company_context():
    official = _public_contacts_from_text(
        '聯絡人：王小明 職稱：業務經理 Email: sales@example.com noreply@example.com',
        source_url='https://example.com/contact',
        official_domain='example.com',
        company_name='範例設備股份有限公司',
    )
    assert [item['email'] for item in official] == ['sales@example.com']
    assert official[0]['name'] == '王小明'
    assert official[0]['title'] == '業務經理'
    assert official[0]['sourceUrl'] == 'https://example.com/contact'

    unrelated = _public_contacts_from_text(
        '其他公司的採購信箱 buyer@unrelated.example',
        source_url='https://directory.example/list',
        official_domain='example.com',
        company_name='範例設備股份有限公司',
    )
    assert unrelated == []


def test_customer_contact_lookup_builds_valid_semantic_filter_group():
    payload = _customer_contact_query_plan(
        'customer-contacts',
        ['customer.name', 'contact.email'],
        'customer.name',
        'Example Company',
        10,
    )

    plan = QueryPlan.model_validate(payload)

    assert plan.filters is not None
    assert plan.filters.operator == 'and'
    assert plan.filters.conditions[0].fieldId == 'customer.name'
    assert plan.filters.conditions[0].operator == 'contains'


def test_email_draft_model_context_excludes_recipient_address():
    context = _email_draft_contact_context(
        {
            'customer_id': 'customer-1',
            'customer_name': 'Example Company',
            'contact_name': 'Amy',
            'email': 'private@example.com',
            'source': {'dataset_id': 'contacts'},
        }
    )

    assert context == {
        'customer_id': 'customer-1',
        'customer_name': 'Example Company',
        'contact_name': 'Amy',
        'source': {'dataset_id': 'contacts'},
    }


def test_customer_request_context_preserves_request_and_normalizes_cc():
    assert _customer_request_context(
        {
            'customer_name': 'Example Company',
            'request': ' Arrange a product introduction ',
            'cc': [' Sales@example.com ', '', 'OWNER@EXAMPLE.COM', 'invented@example.com'],
        },
        'Please CC sales@example.com and owner@example.com.',
    ) == {
        'request': 'Arrange a product introduction',
        'cc': ['sales@example.com', 'owner@example.com'],
    }


def test_email_compose_context_keeps_contact_and_scoped_knowledge_separate():
    contact, knowledge = _email_compose_context(
        {
            'value': {
                'matched': True,
                'value': {
                    'status': 'found',
                    'customer_name': 'Example Company',
                    'contact_name': 'Amy',
                    'email': 'private@example.com',
                },
            },
            'knowledge': [
                {'content': 'Approved product statement', 'source': 'sales-guide.pdf', 'file_id': 'secret'},
                {'content': '', 'source': 'empty'},
            ],
        }
    )

    assert contact['email'] == 'private@example.com'
    assert knowledge == [{'content': 'Approved product statement', 'source': 'sales-guide.pdf'}]
    assert _email_knowledge_context({'content': 'not-a-list'}) == []


def test_email_draft_parser_accepts_common_weaker_model_field_names():
    subject, text = _parse_email_draft(
        '{"email":{"title":"產品介紹","body":"郭先生您好，以下是適合貴公司的產品介紹內容。"}}',
        '請寄一封產品介紹信',
    )

    assert subject == '產品介紹'
    assert text == '郭先生您好，以下是適合貴公司的產品介紹內容。'


def test_email_draft_parser_rejects_missing_body_instead_of_using_instruction():
    with pytest.raises(WorkflowRuntimeError, match='缺少主旨或正文'):
        _parse_email_draft('{"subject":"產品介紹"}', '請寄一封產品介紹信')


def test_email_draft_validation_blocks_user_instruction_as_message_body():
    request = '請寄一封推銷信給木綠森設計有限公司，介紹心情放鬆的產品。'

    with pytest.raises(WorkflowRuntimeError, match='阻止將操作指令當成郵件寄出'):
        _validate_email_draft_content('產品介紹', request, request)


@pytest.mark.asyncio
async def test_workflow_model_content_hydrates_stored_channel_media(monkeypatch, tmp_path):
    image_path = tmp_path / 'line-image.png'
    image_path.write_bytes(b'png-content')
    file = SimpleNamespace(
        id='file-a',
        user_id='owner-a',
        path='stored/path',
        filename='line-image.png',
        meta={'content_type': 'image/png'},
    )

    async def get_file(file_id):
        return file if file_id == 'file-a' else None

    monkeypatch.setattr('open_webui.routers.workflows.Files.get_file_by_id', get_file)
    monkeypatch.setattr('open_webui.routers.workflows.Storage.get_file', lambda path: str(image_path))

    content = await _workflow_multimodal_content(
        '請分析附件',
        [{'type': 'image', 'fileId': 'file-a', 'mimeType': 'image/png'}],
        SimpleNamespace(id='owner-a', role='user'),
    )

    assert content[0] == {'type': 'text', 'text': '請分析附件'}
    assert content[1]['type'] == 'image_url'
    assert content[1]['image_url']['url'].startswith('data:image/png;base64,')


def test_company_acl_blocks_another_company_before_intent_matching():
    workflow = _workflow()

    assert workflow_acl_allows(workflow, _context('company-a'))
    assert not workflow_acl_allows(workflow, _context('company-b'))
    assert workflow_agent_candidate(workflow, _context('company-b'), '查發票') is None


def test_channel_acl_applies_to_owner_during_external_execution():
    workflow = _workflow(
        meta={
            'acl': {
                **_workflow().meta['acl'],
                'allowed_channel_ids': ['line-a'],
                'allowed_model_ids': ['model-a'],
            }
        }
    )

    allowed = WorkflowAccessContext(
        user_id='owner-a',
        role='user',
        company_user_id='company-a',
        channel_id='line-a',
        model_id='model-a',
    )
    wrong_channel = WorkflowAccessContext(
        user_id='owner-a',
        role='user',
        company_user_id='company-a',
        channel_id='line-b',
        model_id='model-a',
    )

    assert workflow_channel_acl_allows(workflow, allowed)
    assert not workflow_channel_acl_allows(workflow, wrong_channel)
    assert not workflow_channel_acl_allows(
        _workflow(),
        WorkflowAccessContext(
            user_id='owner-a',
            role='user',
            company_user_id='company-a',
            channel_id='line-a',
            model_id='model-a',
        ),
    )


def test_workflow_metadata_cannot_match_its_own_keywords():
    workflow = _workflow()

    assert workflow_agent_candidate(workflow, _context(), '今天天氣如何') is None


def test_nfkc_chinese_phrase_matching_is_deterministic():
    candidate = workflow_agent_candidate(_workflow(), _context(), '請幫我查詢：發票付款狀態')

    assert candidate is not None
    assert candidate['score'] >= candidate['threshold']
    assert candidate['matched_keywords'] == ['發票', '付款狀態']


def test_required_and_negative_keywords_gate_selection():
    workflow = _workflow(
        meta={
            'acl': {
                **_workflow().meta['acl'],
                'required_keywords': ['台灣公司'],
                'negative_keywords': ['退款'],
            }
        }
    )

    assert workflow_agent_candidate(workflow, _context(), '查發票') is None
    assert workflow_agent_candidate(workflow, _context(), '查台灣公司發票退款') is None
    assert workflow_agent_candidate(workflow, _context(), '查台灣公司發票') is not None


def test_example_utterance_can_select_without_keyword_hit():
    workflow = _workflow(
        meta={
            'acl': {
                **_workflow().meta['acl'],
                'intent_keywords': [],
                'intent_examples': ['幫我查這張發票付了沒'],
            }
        }
    )

    candidate = workflow_agent_candidate(workflow, _context(), '幫我查這張發票付了沒')

    assert candidate is not None
    assert candidate['matched_examples'][0]['similarity'] == 1.0


def test_agent_policy_rejects_conflicting_terms_and_empty_member_scope():
    validation = validate_workflow_agent_policy(
        {
            'acl': {
                'scope': 'selected_members',
                'allow_agent_selection': True,
                'intent_keywords': ['發票'],
                'required_keywords': ['退款'],
                'negative_keywords': ['退款'],
            }
        },
        'shared',
    )

    assert not validation['ok']
    assert any('member ID' in error for error in validation['errors'])
    assert any('cannot overlap' in error for error in validation['errors'])


def test_meta_normalization_preserves_selector_defaults():
    meta = normalize_workflow_meta(
        {
            'acl': {
                'owner_user_id': 'spoofed-owner',
                'company_user_id': 'spoofed-company',
                'intent_keywords': '發票, 付款',
            }
        },
        owner_user_id='owner-a',
        company_user_id='company-a',
        visibility='shared',
    )

    assert meta['acl']['intent_keywords'] == ['發票', '付款']
    assert meta['acl']['owner_user_id'] == 'owner-a'
    assert meta['acl']['company_user_id'] == 'company-a'
    assert meta['acl']['agent_selection_threshold'] == 0.64
    assert meta['acl']['agent_ambiguity_margin'] == 0.12


def test_close_candidates_require_user_confirmation():
    first = workflow_agent_candidate(_workflow(), _context(), '查發票付款狀態')
    second = {**first, 'id': 'workflow-b', 'name': '發票付款明細', 'default_version_id': 'version-b'}

    decision = decide_workflow_candidates([first, second])

    assert decision['decision'] == 'ambiguous'
    assert decision['action'] == 'ask_user'
    assert decision['selected_workflow_id'] is None


def test_single_confident_candidate_returns_exact_published_version():
    candidate = workflow_agent_candidate(_workflow(), _context(), '查發票付款狀態')

    decision = decide_workflow_candidates([candidate])

    assert decision['decision'] == 'selected'
    assert decision['action'] == 'execute_workflow'
    assert decision['selected_workflow_id'] == 'workflow-a'
    assert decision['selected_version_id'] == 'version-a'


def test_database_node_requires_safe_builder_table_configuration():
    validation = validate_workflow_graph(
        {
            'nodes': [
                {'id': 'input', 'data': {'type': 'chat_input'}},
                {'id': 'database', 'data': {'type': 'database_query', 'config': {}}},
                {'id': 'output', 'data': {'type': 'chat_output'}},
            ],
            'edges': [
                {'source': 'input', 'target': 'database'},
                {'source': 'database', 'target': 'output'},
            ],
        }
    )

    assert not validation['ok']
    assert any('allowed table name' in error for error in validation['errors'])
