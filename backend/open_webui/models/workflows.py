import asyncio
import time
from typing import Any, Literal, Optional
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, Index, JSON, String, Text, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_tables_ready = False
_tables_lock = asyncio.Lock()
_publish_locks: dict[str, asyncio.Lock] = {}

WorkflowVisibility = Literal['private', 'shared', 'public_template']
WorkflowStatus = Literal['draft', 'published', 'archived']


class Workflow(Base):
    __tablename__ = 'workflow'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False, index=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    graph = Column(JSON, nullable=False)
    meta = Column(JSON, nullable=True)
    visibility = Column(Text, nullable=False, default='private')
    status = Column(Text, nullable=False, default='draft')
    default_version_id = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_workflow_user_updated', 'user_id', 'updated_at'),
        Index('ix_workflow_visibility', 'visibility'),
    )


class WorkflowVersion(Base):
    __tablename__ = 'workflow_version'

    id = Column(Text, primary_key=True)
    workflow_id = Column(Text, nullable=False, index=True)
    version = Column(BigInteger, nullable=False)
    graph = Column(JSON, nullable=False)
    meta = Column(JSON, nullable=True)
    created_by = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_workflow_version_workflow_version', 'workflow_id', 'version', unique=True),)


class WorkflowRun(Base):
    __tablename__ = 'workflow_run'

    id = Column(Text, primary_key=True)
    workflow_id = Column(Text, nullable=False, index=True)
    workflow_version_id = Column(Text, nullable=True, index=True)
    user_id = Column(Text, nullable=False, index=True)
    trigger_type = Column(Text, nullable=False, default='manual')
    status = Column(Text, nullable=False, default='queued')
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)

    __table_args__ = (Index('ix_workflow_run_workflow_created', 'workflow_id', 'created_at'),)


class WorkflowForm(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    graph: dict[str, Any] = Field(default_factory=lambda: {'nodes': [], 'edges': []})
    meta: Optional[dict[str, Any]] = None
    visibility: WorkflowVisibility = 'private'
    status: WorkflowStatus = 'draft'


class WorkflowPatchForm(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    graph: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
    visibility: Optional[WorkflowVisibility] = None
    status: Optional[WorkflowStatus] = None


class WorkflowRunForm(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    trigger_type: str = 'manual'
    workflow_version_id: Optional[str] = None


class WorkflowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    graph: dict[str, Any]
    meta: Optional[dict[str, Any]] = None
    visibility: str
    status: str
    default_version_id: Optional[str] = None
    created_at: int
    updated_at: int


class WorkflowVersionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    version: int
    graph: dict[str, Any]
    meta: Optional[dict[str, Any]] = None
    created_by: str
    created_at: int


class WorkflowRunModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version_id: Optional[str] = None
    user_id: str
    trigger_type: str
    status: str
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: int
    completed_at: Optional[int] = None


class WorkflowListResponse(BaseModel):
    items: list[WorkflowModel]
    total: int


class WorkflowValidateResponse(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowTable:
    async def ensure_tables(self) -> None:
        global _tables_ready
        if _tables_ready:
            return
        async with _tables_lock:
            if _tables_ready:
                return
            async with async_engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection,
                        tables=[Workflow.__table__, WorkflowVersion.__table__, WorkflowRun.__table__],
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    async def insert(
        self,
        user_id: str,
        form: WorkflowForm,
        db: Optional[AsyncSession] = None,
    ) -> WorkflowModel:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            row = Workflow(
                id=str(uuid4()),
                user_id=user_id,
                name=form.name.strip(),
                description=form.description,
                graph=form.graph,
                meta=form.meta,
                visibility=form.visibility,
                status=form.status,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return WorkflowModel.model_validate(row)

    async def get_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[WorkflowModel]:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            row = await db.get(Workflow, id)
            return WorkflowModel.model_validate(row) if row else None

    async def search(
        self,
        user_id: str,
        query: Optional[str] = None,
        visibility: Optional[str] = None,
        skip: int = 0,
        limit: int = 30,
        include_public_templates: bool = True,
        db: Optional[AsyncSession] = None,
    ) -> WorkflowListResponse:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            access_filter = Workflow.user_id == user_id
            if include_public_templates:
                access_filter = or_(access_filter, Workflow.visibility == 'public_template')

            stmt = select(Workflow).where(access_filter)

            if query:
                search = f'%{query}%'
                stmt = stmt.filter(or_(Workflow.name.ilike(search), cast(Workflow.graph, String).ilike(search)))

            if visibility and visibility != 'all':
                stmt = stmt.filter(Workflow.visibility == visibility)

            stmt = stmt.order_by(Workflow.updated_at.desc())
            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar() or 0

            if skip:
                stmt = stmt.offset(skip)
            if limit:
                stmt = stmt.limit(limit)

            result = await db.execute(stmt)
            return WorkflowListResponse(
                items=[WorkflowModel.model_validate(row) for row in result.scalars().all()],
                total=total,
            )

    async def update_by_id(
        self,
        id: str,
        form: WorkflowPatchForm,
        db: Optional[AsyncSession] = None,
    ) -> Optional[WorkflowModel]:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            row = await db.get(Workflow, id)
            if not row:
                return None

            payload = form.model_dump(exclude_unset=True)
            for key, value in payload.items():
                if key == 'name' and isinstance(value, str):
                    value = value.strip()
                setattr(row, key, value)
            row.updated_at = int(time.time_ns())

            await db.commit()
            await db.refresh(row)
            return WorkflowModel.model_validate(row)

    async def publish_version(
        self,
        workflow_id: str,
        created_by: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[WorkflowVersionModel]:
        await self.ensure_tables()
        publish_lock = _publish_locks.setdefault(workflow_id, asyncio.Lock())
        async with publish_lock:
            return await self._publish_version_locked(workflow_id, created_by, db=db)

    async def _publish_version_locked(
        self,
        workflow_id: str,
        created_by: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[WorkflowVersionModel]:
        async with get_async_db_context(db) as db:
            workflow = await db.get(Workflow, workflow_id)
            if not workflow:
                return None

            result = await db.execute(
                select(func.max(WorkflowVersion.version)).where(WorkflowVersion.workflow_id == workflow_id)
            )
            next_version = (result.scalar() or 0) + 1
            now = int(time.time_ns())
            version = WorkflowVersion(
                id=str(uuid4()),
                workflow_id=workflow_id,
                version=next_version,
                graph=workflow.graph,
                meta=workflow.meta,
                created_by=created_by,
                created_at=now,
            )
            db.add(version)
            await db.flush()

            workflow.default_version_id = version.id
            workflow.status = 'published'
            workflow.updated_at = now
            await db.commit()
            await db.refresh(version)
            return WorkflowVersionModel.model_validate(version)

    async def get_versions(
        self,
        workflow_id: str,
        db: Optional[AsyncSession] = None,
    ) -> list[WorkflowVersionModel]:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow_id)
                .order_by(WorkflowVersion.version.desc())
            )
            return [WorkflowVersionModel.model_validate(row) for row in result.scalars().all()]

    async def insert_run(
        self,
        workflow_id: str,
        user_id: str,
        form: WorkflowRunForm,
        db: Optional[AsyncSession] = None,
    ) -> WorkflowRunModel:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            if form.workflow_version_id:
                version = await db.get(WorkflowVersion, form.workflow_version_id)
                if not version or version.workflow_id != workflow_id:
                    raise ValueError('Workflow version does not belong to this workflow.')

            now = int(time.time_ns())
            row = WorkflowRun(
                id=str(uuid4()),
                workflow_id=workflow_id,
                workflow_version_id=form.workflow_version_id,
                user_id=user_id,
                trigger_type=form.trigger_type,
                status='running',
                input=form.input,
                created_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return WorkflowRunModel.model_validate(row)

    async def complete_run(
        self,
        run_id: str,
        status: str,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[WorkflowRunModel]:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            row = await db.get(WorkflowRun, run_id)
            if not row:
                return None
            row.status = status
            row.output = output
            row.error = error
            row.completed_at = int(time.time_ns())
            await db.commit()
            await db.refresh(row)
            return WorkflowRunModel.model_validate(row)

    async def get_runs(
        self,
        workflow_id: str,
        limit: int = 20,
        db: Optional[AsyncSession] = None,
    ) -> list[WorkflowRunModel]:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(limit)
            )
            return [WorkflowRunModel.model_validate(row) for row in result.scalars().all()]

    async def delete(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        await self.ensure_tables()
        async with get_async_db_context(db) as db:
            row = await db.get(Workflow, id)
            if not row:
                return False
            await db.execute(delete(WorkflowVersion).where(WorkflowVersion.workflow_id == id))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.workflow_id == id))
            await db.delete(row)
            await db.commit()
            return True


Workflows = WorkflowTable()
