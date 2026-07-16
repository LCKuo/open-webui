from types import SimpleNamespace

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
