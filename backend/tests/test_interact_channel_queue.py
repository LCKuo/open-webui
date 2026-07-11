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

    await queue_table.complete_job(first.id)
    await queue_table.complete_job(third.id)
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
async def test_enqueue_is_idempotent_per_platform_event(queue_table):
    first = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'hello'})
    duplicate = await queue_table.enqueue_job('event-1', 'channel-1', 'user-a', 'line', {'message': 'ignored'})
    assert duplicate.id == first.id


@pytest.mark.asyncio
async def test_user_rate_limit_does_not_block_other_users(queue_table):
    channel = await queue_table.get_by_id('channel-1')
    channel.user_rate_limit_per_minute = 1
    channel.rate_limit_per_minute = 2

    first = await queue_table.claim_event(channel, 'event-1', 'user-a', 10, 0)
    limited = await queue_table.claim_event(channel, 'event-2', 'user-a', 10, 0)
    other_user = await queue_table.claim_event(channel, 'event-3', 'user-b', 10, 0)

    assert first.allowed is True
    assert limited.allowed is False
    assert limited.reason == 'user-rpm'
    assert other_user.allowed is True
