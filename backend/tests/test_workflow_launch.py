from types import SimpleNamespace

from open_webui.utils.workflow_launch import (
    apply_launch_defaults,
    normalize_launch_contract,
    validate_launch_contract,
    validate_launch_input,
    workflow_requires_confirmation,
)
from open_webui.utils.workflows import WorkflowAccessContext, normalize_workflow_meta, workflow_agent_candidate


def _graph(start='chat_input', middle='prompt_template'):
    return {
        'nodes': [
            {'id': 'start', 'data': {'type': start}},
            {'id': 'step', 'data': {'type': middle}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [
            {'source': 'start', 'target': 'step'},
            {'source': 'step', 'target': 'output'},
        ],
    }


def _launch(mode, **overrides):
    return {
        'launch': {
            'mode': mode,
            'inputSchema': {
                'type': 'object',
                'properties': {},
                'required': [],
                'additionalProperties': False,
            },
            'defaultInput': {},
            **overrides,
        }
    }


def test_legacy_workflows_infer_launch_mode_without_mutating_graph():
    graph = _graph(start='file_upload')
    contract = normalize_launch_contract({}, graph)

    assert contract['mode'] == 'file_input'
    assert graph['nodes'][0]['data']['type'] == 'file_upload'


def test_instant_mode_requires_defaults_for_every_required_field():
    meta = _launch(
        'instant',
        inputSchema={
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'title': '名次'}},
            'required': ['limit'],
            'additionalProperties': False,
        },
    )

    invalid = validate_launch_contract(meta, _graph(), for_publish=True)
    meta['launch']['defaultInput'] = {'limit': 5}
    valid = validate_launch_contract(meta, _graph(), for_publish=True)

    assert not invalid['ok']
    assert any('預設值' in error for error in invalid['errors'])
    assert valid['ok']


def test_form_input_validates_required_type_range_and_enum():
    meta = _launch(
        'form_input',
        inputSchema={
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                'department': {'type': 'string', 'enum': ['sales', 'support']},
            },
            'required': ['limit'],
            'additionalProperties': False,
        },
    )
    contract = normalize_launch_contract(meta, _graph(start='form_input'))

    missing = validate_launch_input(contract, _graph(start='form_input'), {'data': {}})
    invalid = validate_launch_input(
        contract,
        _graph(start='form_input'),
        {'data': {'limit': 20, 'department': 'finance'}},
    )
    valid = validate_launch_input(
        contract,
        _graph(start='form_input'),
        {'data': {'limit': 5, 'department': 'sales'}},
    )

    assert missing['missing_fields'] == ['limit']
    assert not invalid['ok']
    assert len(invalid['errors']) == 2
    assert valid['ok']


def test_file_input_enforces_count_size_and_mime_type():
    contract = normalize_launch_contract(
        _launch(
            'file_input',
            fileRules={'allowedMimeTypes': ['image/*'], 'maxFiles': 1, 'maxSizeMB': 1},
        ),
        _graph(start='file_upload'),
    )

    missing = validate_launch_input(contract, _graph(start='file_upload'), {})
    invalid = validate_launch_input(
        contract,
        _graph(start='file_upload'),
        {
            'files': [
                {'name': 'report.pdf', 'content_type': 'application/pdf', 'size': 2_000_000},
                {'name': 'image.png', 'content_type': 'image/png', 'size': 10},
            ]
        },
    )

    assert missing['missing_fields'] == ['files']
    assert not invalid['ok']
    assert any('數量' in error for error in invalid['errors'])
    assert any('格式' in error for error in invalid['errors'])
    assert any('MB' in error for error in invalid['errors'])


def test_file_input_rejects_invalid_size_without_crashing():
    contract = normalize_launch_contract(
        _launch('file_input'),
        _graph(start='file_upload'),
    )

    result = validate_launch_input(
        contract,
        _graph(start='file_upload'),
        {'files': [{'name': 'bad.pdf', 'content_type': 'application/pdf', 'size': 'unknown'}]},
    )

    assert not result['ok']
    assert any('檔案大小格式' in error for error in result['errors'])


def test_contract_rejects_open_schema_and_non_numeric_bounds():
    meta = _launch(
        'form_input',
        inputSchema={
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'minimum': 'one'}},
            'required': [],
            'additionalProperties': True,
        },
    )

    result = validate_launch_contract(meta, _graph(start='form_input'), for_publish=True)

    assert not result['ok']
    assert any('未定義欄位' in error for error in result['errors'])
    assert any('minimum' in error for error in result['errors'])


def test_risky_workflow_requires_explicit_confirmation():
    graph = _graph(middle='http_request')
    contract = normalize_launch_contract(_launch('instant'), graph)

    assert workflow_requires_confirmation(contract, graph)
    assert not validate_launch_input(contract, graph, {}, confirmed=False)['ok']
    assert validate_launch_input(contract, graph, {}, confirmed=True)['ok']


def test_defaults_are_merged_without_overwriting_supplied_values():
    contract = normalize_launch_contract(
        _launch('instant', defaultInput={'limit': 5, 'department': 'all'}),
        _graph(),
    )

    payload = apply_launch_defaults(contract, {'data': {'limit': 3}})

    assert payload['data'] == {'limit': 3, 'department': 'all'}


def test_meta_normalization_versions_the_launch_contract():
    meta = normalize_workflow_meta(
        {},
        owner_user_id='owner-a',
        company_user_id='company-a',
        visibility='private',
        graph=_graph(start='form_input'),
    )

    assert meta['launch']['version'] == 1
    assert meta['launch']['mode'] == 'form_input'


def test_agent_selector_does_not_auto_execute_form_or_file_launches():
    workflow = SimpleNamespace(
        id='workflow-a',
        user_id='owner-a',
        name='Sales report',
        description='Build a sales report',
        visibility='shared',
        status='published',
        default_version_id='version-a',
        graph=_graph(start='form_input'),
        meta={
            **_launch('form_input'),
            'acl': {
                'scope': 'company',
                'company_user_id': 'company-a',
                'allow_agent_selection': True,
                'intent_keywords': ['sales report'],
                'intent_examples': [],
            },
        },
    )
    context = WorkflowAccessContext(user_id='member-a', role='user', company_user_id='company-a')

    assert workflow_agent_candidate(workflow, context, 'sales report') is None
