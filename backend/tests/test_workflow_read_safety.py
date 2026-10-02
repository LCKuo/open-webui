import asyncio
from contextlib import asynccontextmanager
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import open_webui.models.workflows as models


@pytest.mark.asyncio
async def test_first_read_in_another_process_does_not_interrupt_running_jobs(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: models.Base.metadata.create_all(
            sync, tables=[models.Workflow.__table__, models.WorkflowVersion.__table__, models.WorkflowRun.__table__]))
    async with sessions() as db:
        db.add(models.WorkflowRun(id='active-job', workflow_id='workflow', user_id='user',
                                  trigger_type='crm_agent', status='running', created_at=1))
        db.add(models.WorkflowRun(id='finished-job', workflow_id='workflow', user_id='user',
                                  trigger_type='crm_agent', status='success', created_at=1,
                                  completed_at=2, output={'ok': True}))
        await db.commit()

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with sessions() as session:
                yield session

    monkeypatch.setattr(models, 'get_async_db_context', context)
    monkeypatch.setattr(models, 'async_engine', engine)
    monkeypatch.setattr(models, '_tables_ready', False)
    monkeypatch.setattr(models, '_tables_lock', asyncio.Lock())
    table = models.WorkflowTable()
    try:
        running = await table.get_run('active-job')
        assert running.status == 'running'
        assert running.error is None
        assert running.completed_at is None
        assert (await table.get_run('finished-job')).output == {'ok': True}
        # A second independently initialized reader must also leave the job alone.
        monkeypatch.setattr(models, '_tables_ready', False)
        assert (await models.WorkflowTable().get_run('active-job')).status == 'running'
    finally:
        await engine.dispose()
