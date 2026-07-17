from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import open_webui.models.workflows as workflow_models
import open_webui.routers.workflows as workflow_routes
from open_webui.models.workflows import (
    Base,
    Workflow,
    WorkflowForm,
    WorkflowRunForm,
    WorkflowTable,
    WorkflowVersion,
    WorkflowRun,
)


@pytest.mark.asyncio
async def test_archive_and_activate_preserve_history_and_restore_published_contract(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[Workflow.__table__, WorkflowVersion.__table__, WorkflowRun.__table__],
            )
        )

    @asynccontextmanager
    async def test_db_context(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(workflow_models, 'get_async_db_context', test_db_context)
    monkeypatch.setattr(workflow_models, '_tables_ready', True)

    workflows = WorkflowTable()
    published_graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [{'source': 'input', 'target': 'output'}],
    }
    workflow = await workflows.insert(
        'owner-a',
        WorkflowForm(
            name='Lifecycle test',
            graph=published_graph,
            meta={'launch': {'mode': 'instant'}},
        ),
    )
    version = await workflows.publish_version(workflow.id, 'owner-a')
    run = await workflows.insert_run(
        workflow.id,
        'owner-a',
        WorkflowRunForm(workflow_version_id=version.id, trigger_type='manual_test'),
    )
    await workflows.complete_run(run.id, 'success', output={'ok': True})

    archived = await workflows.archive(workflow.id)

    assert archived.status == 'archived'
    assert archived.default_version_id == version.id
    assert len(await workflows.get_versions(workflow.id)) == 1
    assert len(await workflows.get_runs(workflow.id)) == 1
    assert (await workflows.search('owner-a', workflow_status='archived')).total == 1
    assert (await workflows.search('owner-a', workflow_status='active')).total == 0

    async with session_factory() as session:
        row = await session.get(Workflow, workflow.id)
        row.graph = {'nodes': [], 'edges': []}
        row.meta = {'unreviewed': True}
        await session.commit()

    activated = await workflows.activate_default_version(workflow.id)

    assert activated.status == 'published'
    assert activated.default_version_id == version.id
    assert activated.graph == published_graph
    assert activated.meta == {'launch': {'mode': 'instant'}}
    assert len(await workflows.get_versions(workflow.id)) == 1
    assert len(await workflows.get_runs(workflow.id)) == 1
    assert (await workflows.search('owner-a', workflow_status='archived')).total == 0
    assert (await workflows.search('owner-a', workflow_status='active')).total == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_reactivate_legacy_guided_version_materializes_editable_input_node(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[Workflow.__table__, WorkflowVersion.__table__, WorkflowRun.__table__],
            )
        )

    @asynccontextmanager
    async def test_db_context(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(workflow_models, 'get_async_db_context', test_db_context)
    monkeypatch.setattr(workflow_models, '_tables_ready', True)
    workflows = WorkflowTable()
    graph = {
        'nodes': [
            {'id': 'input', 'data': {'type': 'chat_input'}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [{'source': 'input', 'target': 'output'}],
    }
    meta = {'launch': {'mode': 'text_input'}}
    workflow = await workflows.insert(
        'owner-a',
        WorkflowForm(name='Legacy guided workflow', graph=graph, meta=meta),
    )
    version = await workflows.publish_version(workflow.id, 'owner-a')
    await workflows.archive(workflow.id)

    activated = await workflows.activate_default_version(workflow.id)

    assert activated.default_version_id == version.id
    assert len(await workflows.get_versions(workflow.id)) == 1
    assert any(
        node.get('data', {}).get('type') == 'user_input'
        for node in activated.graph['nodes']
    )
    immutable_version = await workflows.get_version_by_id(version.id)
    assert not any(
        node.get('data', {}).get('type') == 'user_input'
        for node in immutable_version.graph['nodes']
    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_line_instant_options_use_only_current_published_version(monkeypatch):
    graph = {
        'nodes': [
            {'id': 'trigger', 'data': {'type': 'schedule_trigger'}},
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [{'source': 'trigger', 'target': 'output'}],
    }
    published = SimpleNamespace(
        id='published',
        user_id='owner',
        name='立即產生摘要',
        description='test',
        graph=graph,
        meta={
            'launch': {'mode': 'instant', 'buttonLabel': '產生摘要'},
            'acl': {
                'scope': 'private',
                'allowed_channel_ids': ['line-channel'],
                'allowed_model_ids': ['model'],
            },
        },
        visibility='private',
        status='published',
        default_version_id='version-current',
        updated_at=2,
    )
    archived = SimpleNamespace(
        **{
            **published.__dict__,
            'id': 'archived',
            'name': '已停用',
            'status': 'archived',
            'default_version_id': 'version-archived',
            'updated_at': 3,
        }
    )
    guided_graph = {
        'nodes': [
            {
                'id': 'guide',
                'data': {
                    'type': 'user_input',
                    'config': {
                        'launch': {
                            'mode': 'form_input',
                            'instruction': '請輸入部門。',
                            'inputSchema': {
                                'type': 'object',
                                'properties': {'department': {'type': 'string', 'title': '部門'}},
                                'required': ['department'],
                                'additionalProperties': False,
                            },
                        }
                    },
                },
            },
            {'id': 'output', 'data': {'type': 'chat_output'}},
        ],
        'edges': [{'source': 'guide', 'target': 'output'}],
    }
    guided = SimpleNamespace(
        **{
            **published.__dict__,
            'id': 'guided',
            'name': '部門報表',
            'graph': guided_graph,
            'default_version_id': 'version-guided',
            'updated_at': 4,
        }
    )
    versions = {
        'version-current': SimpleNamespace(
            id='version-current', workflow_id='published', graph=graph, meta=published.meta
        ),
        'version-archived': SimpleNamespace(
            id='version-archived', workflow_id='archived', graph=graph, meta=archived.meta
        ),
        'version-guided': SimpleNamespace(
            id='version-guided', workflow_id='guided', graph=guided_graph, meta=guided.meta
        ),
    }

    async def workflow_context(*args, **kwargs):
        return workflow_routes.WorkflowAccessContext(
            user_id='owner',
            role='user',
            company_user_id='owner',
            channel_id='line-channel',
            model_id='model',
        )

    async def search(*args, **kwargs):
        assert kwargs['workflow_status'] == 'published'
        return SimpleNamespace(items=[archived, published, guided])

    async def get_version(version_id):
        return versions.get(version_id)

    monkeypatch.setattr(workflow_routes, '_workflow_context_for_user', workflow_context)
    monkeypatch.setattr(workflow_routes.Workflows, 'search', search)
    monkeypatch.setattr(workflow_routes.Workflows, 'get_version_by_id', get_version)

    user = SimpleNamespace(id='owner', role='user')
    options = await workflow_routes.list_instant_workflows_for_user_context(
        user,
        channel_id='line-channel',
        model_id='model',
    )
    channel_options = await workflow_routes.list_channel_workflows_for_user_context(
        user,
        channel_id='line-channel',
        model_id='model',
    )
    current = await workflow_routes.resolve_instant_workflow_for_user_context(
        user,
        'published',
        'version-current',
        channel_id='line-channel',
        model_id='model',
    )
    stale = await workflow_routes.resolve_instant_workflow_for_user_context(
        user,
        'published',
        'version-old',
        channel_id='line-channel',
        model_id='model',
    )

    assert [option['id'] for option in options] == ['published']
    assert [option['id'] for option in channel_options] == ['guided', 'published']
    assert channel_options[0]['launchMode'] == 'form_input'
    assert channel_options[0]['instruction'] == '請輸入部門。'
    assert current and current['versionId'] == 'version-current'
    assert stale is None


@pytest.mark.asyncio
async def test_invalid_lifecycle_transitions_are_rejected(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[Workflow.__table__, WorkflowVersion.__table__, WorkflowRun.__table__],
            )
        )

    @asynccontextmanager
    async def test_db_context(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(workflow_models, 'get_async_db_context', test_db_context)
    monkeypatch.setattr(workflow_models, '_tables_ready', True)

    workflows = WorkflowTable()
    workflow = await workflows.insert(
        'owner-a',
        WorkflowForm(name='Draft lifecycle test', graph={'nodes': [], 'edges': []}),
    )

    with pytest.raises(ValueError, match='published workflow'):
        await workflows.archive(workflow.id)
    with pytest.raises(ValueError, match='disabled workflow'):
        await workflows.activate_default_version(workflow.id)

    await engine.dispose()
