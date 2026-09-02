from contextlib import asynccontextmanager
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.modules.setdefault(
    'open_webui.utils.oauth',
    SimpleNamespace(
        encrypt_data=lambda value: value,
        decrypt_data=lambda value: value,
    ),
)

import open_webui.models.interact_channels as channel_models


async def _set_channel_limits(channel_id: str, *, user_rpm: int, channel_rpm: int) -> None:
    async with channel_models.get_async_db_context() as session:
        row = await session.get(channel_models.InteractChannel, channel_id)
        row.user_rate_limit_per_minute = user_rpm
        row.rate_limit_per_minute = channel_rpm
        await session.commit()


@pytest_asyncio.fixture
async def queue_table(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return
        async with sessions() as session:
            yield session

    monkeypatch.setattr(channel_models, 'async_engine', engine)
    monkeypatch.setattr(channel_models, 'get_async_db_context', session_context)
    monkeypatch.setattr(channel_models, '_tables_ready', False)
    table = channel_models.InteractChannelsTable()
    await table.ensure_tables()

    async with sessions() as session:
        session.add(
            channel_models.InteractChannel(
                id='channel-1',
                company_email='company@example.com',
                channel_type='line',
                channel_identifier='line-channel',
                name='LINE',
                enabled=True,
                reply_mode='ai',
                access_mode='public',
                rate_limit_per_minute=60,
                user_rate_limit_per_minute=5,
                max_concurrent_jobs=2,
                daily_user_limit=50,
                daily_bot_token_limit=100000,
                updated_at=1,
            )
        )
        await session.commit()

    yield table
    await engine.dispose()


@pytest.mark.asyncio
async def test_queue_serializes_same_user_but_runs_different_users_in_parallel(queue_table):
    first = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'first'})
    second = await queue_table.enqueue_job('event-2', 'channel-1', 'user-a', 'line', {'message': 'second'})
    third = await queue_table.enqueue_job('event-3', 'channel-1', 'user-b', 'line', {'message': 'other user'})

    claimed_first = await queue_table.claim_next_job('worker-1')
    claimed_parallel = await queue_table.claim_next_job('worker-2')

    assert claimed_first.id == first.id
    assert claimed_parallel.id == third.id
    assert claimed_parallel.id != second.id

    await queue_table.complete_job(first.id, 'worker-1')
    await queue_table.complete_job(third.id, 'worker-2')
    claimed_second = await queue_table.claim_next_job('worker-3')
    assert claimed_second.id == second.id


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_without_losing_job(queue_table, monkeypatch):
    job = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'hello'})
    claimed = await queue_table.claim_next_job('dead-worker', lease_seconds=60)
    assert claimed.id == job.id

    async with channel_models.get_async_db_context() as session:
        row = await session.get(channel_models.InteractChannelJob, job.id)
        row.lease_expires_at = 0
        await session.commit()

    recovered = await queue_table.claim_next_job('replacement-worker', lease_seconds=60)
    assert recovered.id == job.id
    assert recovered.attempts == 2


@pytest.mark.asyncio
async def test_expired_worker_cannot_mutate_reclaimed_job(queue_table):
    job = await queue_table.enqueue_job(
        'event-fenced',
        'channel-1',
        'user-a',
        'line',
        {'message': 'hello'},
    )
    await queue_table.claim_next_job('old-worker', lease_seconds=60)
    async with channel_models.get_async_db_context() as session:
        row = await session.get(channel_models.InteractChannelJob, job.id)
        row.lease_expires_at = 0
        await session.commit()
    await queue_table.claim_next_job('new-worker', lease_seconds=60)

    assert await queue_table.save_job_result(job.id, 'old-worker', {'content': 'stale'}) is False
    assert await queue_table.complete_job(job.id, 'old-worker') is False
    assert await queue_table.release_job(job.id, 'old-worker') is False
    assert await queue_table.retry_job(job.id, 'old-worker', 'stale failure') is None
    assert await queue_table.save_job_result(job.id, 'new-worker', {'content': 'fresh'}) is True
    assert await queue_table.complete_job(job.id, 'new-worker') is True


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_per_platform_event(queue_table):
    first = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'hello'})
    duplicate = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'ignored'})
    assert duplicate.id == first.id


@pytest.mark.asyncio
async def test_crm_employee_account_link_persists_product_identity(queue_table, monkeypatch):
    monkeypatch.setattr(channel_models, '_encrypt_secret', lambda value: value)
    monkeypatch.setattr(channel_models, '_decrypt_secret', lambda value: value)
    await queue_table.create_line_identity_link(
        state='state-value',
        channel_id='channel-1',
        external_user_id='line-user',
        link_token='line-link-token',
    )
    prepared = await queue_table.prepare_line_identity_nonce(
        state='state-value',
        channel_id='channel-1',
        link_token='line-link-token',
        nonce='line-nonce',
        company_user_id='company-1',
        company_email='billing@example.com',
        company_member_id=None,
        member_email='employee@example.com',
        member_role='member',
        member_status='active',
        group_ids=[],
        identity_source='product',
        product_key='crm',
        product_instance_id='crm-instance-a',
        product_user_id='7',
        product_team_codes=['am'],
    )
    binding = await queue_table.consume_line_identity_link(
        nonce='line-nonce',
        channel_id='channel-1',
        external_user_id='line-user',
    )

    assert prepared is True
    assert binding.identity_source == 'product'
    assert binding.product_key == 'crm'
    assert binding.product_instance_id == 'crm-instance-a'
    assert binding.product_user_id == '7'
    assert binding.product_team_codes == ['am']
    assert binding.access_subject_id == 'product:crm:crm-instance-a:7'


@pytest.mark.asyncio
async def test_user_rate_limit_does_not_block_other_users(queue_table):
    channel = await queue_table.get_by_id('channel-1')
    channel.user_rate_limit_per_minute = 1
    channel.rate_limit_per_minute = 2
    await _set_channel_limits('channel-1', user_rpm=1, channel_rpm=2)

    first = await queue_table.claim_event(channel, 'event-1', 'user-a', 10, 0)
    limited = await queue_table.claim_event(channel, 'event-2', 'user-a', 10, 0)
    other_user = await queue_table.claim_event(channel, 'event-3', 'user-b', 10, 0)

    assert first.allowed is True
    assert limited.allowed is False
    assert limited.reason == 'user-rpm'
    assert other_user.allowed is True


@pytest.mark.asyncio
async def test_guided_interactions_do_not_consume_request_quota_until_execution(queue_table):
    channel = await queue_table.get_by_id('channel-1')
    channel.user_rate_limit_per_minute = 1
    channel.rate_limit_per_minute = 10
    await _set_channel_limits('channel-1', user_rpm=1, channel_rpm=10)

    for index in range(8):
        interaction = await queue_table.claim_event(
            channel,
            f'guided-{index}',
            'user-a',
            0,
            0,
            quota_exempt=True,
        )
        assert interaction.allowed is True

    first_execution = await queue_table.claim_event(channel, 'execution-1', 'user-a', 10, 0)
    second_execution = await queue_table.claim_event(channel, 'execution-2', 'user-a', 10, 0)

    assert first_execution.allowed is True
    assert second_execution.allowed is False
    assert second_execution.reason == 'user-rpm'


@pytest.mark.asyncio
async def test_promoting_guided_event_enforces_quota_once(queue_table):
    channel = await queue_table.get_by_id('channel-1')
    channel.user_rate_limit_per_minute = 1
    channel.rate_limit_per_minute = 10
    await _set_channel_limits('channel-1', user_rpm=1, channel_rpm=10)

    regular = await queue_table.claim_event(channel, 'regular', 'user-a', 10, 0)
    guided = await queue_table.claim_event(
        channel,
        'guided',
        'user-a',
        0,
        0,
        quota_exempt=True,
    )
    promoted = await queue_table.promote_event_quota(
        guided.event_id,
        channel,
        'user-a',
        10,
        0,
    )

    assert regular.allowed is True
    assert guided.allowed is True
    assert promoted.allowed is False
    assert promoted.reason == 'user-rpm'


@pytest.mark.asyncio
async def test_failed_guided_enqueue_can_release_promoted_quota(queue_table):
    channel = await queue_table.get_by_id('channel-1')
    channel.user_rate_limit_per_minute = 1
    channel.rate_limit_per_minute = 10
    await _set_channel_limits('channel-1', user_rpm=1, channel_rpm=10)

    guided = await queue_table.claim_event(
        channel,
        'guided',
        'user-a',
        0,
        0,
        quota_exempt=True,
    )
    promoted = await queue_table.promote_event_quota(
        guided.event_id,
        channel,
        'user-a',
        10,
        0,
    )
    released = await queue_table.release_event_quota(guided.event_id)
    retry = await queue_table.claim_event(channel, 'retry', 'user-a', 10, 0)

    assert promoted.allowed is True
    assert released is True
    assert retry.allowed is True


@pytest.mark.asyncio
async def test_workflow_session_revision_prevents_double_advance(queue_table):
    session = await queue_table.start_workflow_session(
        channel_id='channel-1',
        external_user_id='user-a',
        conversation_id='conversation-a',
        workflow_id='workflow-a',
        workflow_version_id='version-a',
        session_input={'data': {}},
        current_field='first',
    )

    first = await queue_table.update_workflow_session(
        session.id,
        status='collecting',
        session_input={'data': {'first': 'one'}},
        current_field='second',
        expected_revision=session.revision,
    )
    stale = await queue_table.update_workflow_session(
        session.id,
        status='collecting',
        session_input={'data': {'first': 'overwritten'}},
        current_field='second',
        expected_revision=session.revision,
    )

    assert first is not None
    assert first.revision == session.revision + 1
    assert stale is None


@pytest.mark.asyncio
async def test_starting_another_workflow_replaces_session_with_new_revision(queue_table):
    first = await queue_table.start_workflow_session(
        channel_id='channel-1',
        external_user_id='user-a',
        conversation_id='conversation-a',
        workflow_id='workflow-a',
        workflow_version_id='version-a',
        current_field='first',
    )
    replacement = await queue_table.start_workflow_session(
        channel_id='channel-1',
        external_user_id='user-a',
        conversation_id='conversation-a',
        workflow_id='workflow-b',
        workflow_version_id='version-b',
        current_field='second',
    )

    assert replacement.id == first.id
    assert replacement.workflow_id == 'workflow-b'
    assert replacement.current_field == 'second'
    assert replacement.revision == first.revision + 1


@pytest.mark.asyncio
async def test_rich_menu_database_lease_blocks_parallel_sync_and_keeps_lock(queue_table):
    state = await queue_table.configure_rich_menu('channel-1', enabled=True)

    first = await queue_table.claim_rich_menu_sync('channel-1', state.revision)
    second = await queue_table.claim_rich_menu_sync('channel-1', state.revision)
    marked = await queue_table.mark_rich_menu_pending('channel-1')
    locked = await queue_table.get_rich_menu('channel-1')
    third = await queue_table.claim_rich_menu_sync('channel-1', locked.revision)
    stale_finish = await queue_table.update_rich_menu_status(
        'channel-1',
        status='active',
        sync_token=first,
        expected_revision=state.revision,
    )
    released = await queue_table.release_rich_menu_sync('channel-1', first)

    assert isinstance(first, str)
    assert second is None
    assert marked is True
    assert locked.status == 'pending'
    assert third is None
    assert stale_finish is None
    assert released is True
