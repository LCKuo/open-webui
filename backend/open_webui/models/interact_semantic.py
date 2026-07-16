from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Integer, Text, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

_tables_ready = False
_tables_lock = asyncio.Lock()


def _business_day_start(now: int) -> int:
    try:
        offset = max(-43200, min(50400, int(os.environ.get('INTERACT_SEMANTIC_DAY_OFFSET_SECONDS', '28800'))))
    except ValueError:
        offset = 28800
    return now - ((now + offset) % 86400)


class InteractSchemaSnapshot(Base):
    __tablename__ = 'interact_schema_snapshot'

    id = Column(Text, primary_key=True)
    connector_id = Column(Text, nullable=False, index=True)
    company_user_id = Column(Text, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    fingerprint = Column(Text, nullable=False)
    scanner_version = Column(Text, nullable=False)
    database_product = Column(Text, nullable=False)
    database_version = Column(Text, nullable=True)
    schema_json = Column(JSON, nullable=False)
    error_code = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index('uq_interact_schema_snapshot_version', 'connector_id', 'version', unique=True),
        Index('uq_interact_schema_snapshot_fingerprint', 'connector_id', 'fingerprint', unique=True),
    )


class InteractCatalogObject(Base):
    __tablename__ = 'interact_catalog_object'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    connector_id = Column(Text, nullable=False, index=True)
    snapshot_id = Column(Text, nullable=False, index=True)
    physical_name = Column(Text, nullable=False)
    object_type = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    synonyms = Column(JSON, nullable=False, default=list)
    business_domain = Column(Text, nullable=True)
    sensitivity = Column(Text, nullable=False, default='internal')
    enabled = Column(Boolean, nullable=False, default=False)
    source_verified = Column(Boolean, nullable=False, default=False)
    updated_by = Column(Text, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('uq_interact_catalog_object_snapshot_name', 'snapshot_id', 'physical_name', unique=True),)


class InteractCatalogField(Base):
    __tablename__ = 'interact_catalog_field'

    id = Column(Text, primary_key=True)
    catalog_object_id = Column(Text, nullable=False, index=True)
    physical_name = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    synonyms = Column(JSON, nullable=False, default=list)
    physical_type = Column(Text, nullable=False)
    semantic_type = Column(Text, nullable=False, default='string')
    nullable = Column(Boolean, nullable=False, default=True)
    primary_key = Column(Boolean, nullable=False, default=False)
    readable = Column(Boolean, nullable=False, default=False)
    filterable = Column(Boolean, nullable=False, default=False)
    groupable = Column(Boolean, nullable=False, default=False)
    aggregatable = Column(Boolean, nullable=False, default=False)
    default_aggregation = Column(Text, nullable=True)
    sensitivity = Column(Text, nullable=False, default='internal')
    masking_rule = Column(Text, nullable=False, default='none')
    sample_values = Column(JSON, nullable=True)
    updated_by = Column(Text, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('uq_interact_catalog_field_object_name', 'catalog_object_id', 'physical_name', unique=True),
    )


class InteractCatalogRelationship(Base):
    __tablename__ = 'interact_catalog_relationship'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    connector_id = Column(Text, nullable=False, index=True)
    left_object_id = Column(Text, nullable=False, index=True)
    right_object_id = Column(Text, nullable=False, index=True)
    relationship_type = Column(Text, nullable=False)
    join_type = Column(Text, nullable=False, default='left')
    join_pairs = Column(JSON, nullable=False)
    source = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='suggested')
    fanout_risk = Column(Text, nullable=False, default='none')
    description = Column(Text, nullable=True)
    confirmed_by = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class InteractSemanticDataset(Base):
    __tablename__ = 'interact_semantic_dataset'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    connector_id = Column(Text, nullable=False, index=True)
    slug = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    business_domain = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default='draft')
    current_version_id = Column(Text, nullable=True)
    draft_definition = Column(JSON, nullable=False, default=dict)
    access_mode = Column(Text, nullable=False, default='company_admins')
    allowed_member_ids = Column(JSON, nullable=False, default=list)
    allowed_group_ids = Column(JSON, nullable=False, default=list)
    allowed_model_ids = Column(JSON, nullable=False, default=list)
    allowed_channel_ids = Column(JSON, nullable=False, default=list)
    allowed_workflow_ids = Column(JSON, nullable=False, default=list)
    created_by = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('uq_interact_semantic_dataset_company_slug', 'company_user_id', 'slug', unique=True),)


class InteractSemanticDatasetVersion(Base):
    __tablename__ = 'interact_semantic_dataset_version'

    id = Column(Text, primary_key=True)
    dataset_id = Column(Text, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot_id = Column(Text, nullable=False)
    definition = Column(JSON, nullable=False)
    validation = Column(JSON, nullable=False)
    published_by = Column(Text, nullable=False)
    published_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('uq_interact_semantic_dataset_version', 'dataset_id', 'version', unique=True),)


class InteractRowPolicy(Base):
    __tablename__ = 'interact_row_policy'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    dataset_id = Column(Text, nullable=False, index=True)
    name = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='draft')
    principal_type = Column(Text, nullable=False)
    principal_ids = Column(JSON, nullable=False, default=list)
    expression = Column(JSON, nullable=False)
    deny_if_unresolved = Column(Boolean, nullable=False, default=True)
    created_by = Column(Text, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class InteractSemanticQueryEvent(Base):
    __tablename__ = 'interact_semantic_query_event'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    connector_id = Column(Text, nullable=False, index=True)
    dataset_id = Column(Text, nullable=True, index=True)
    dataset_version_id = Column(Text, nullable=True)
    user_id = Column(Text, nullable=True)
    company_member_id = Column(Text, nullable=True)
    model_id = Column(Text, nullable=True)
    channel_id = Column(Text, nullable=True)
    workflow_id = Column(Text, nullable=True)
    request_id = Column(Text, nullable=False, index=True)
    status = Column(Text, nullable=False)
    intent_summary = Column(Text, nullable=True)
    plan_redacted = Column(JSON, nullable=True)
    plan_fingerprint = Column(Text, nullable=True)
    policy_decision = Column(JSON, nullable=True)
    compiled_query_hash = Column(Text, nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    error_code = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class InteractSemanticScanSchedule(Base):
    __tablename__ = 'interact_semantic_scan_schedule'

    connector_id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    next_scan_at = Column(BigInteger, nullable=False, index=True)
    lease_until = Column(BigInteger, nullable=False, default=0)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_started_at = Column(BigInteger, nullable=True)
    last_completed_at = Column(BigInteger, nullable=True)
    last_error = Column(Text, nullable=True)


class InteractSemanticDailyQuota(Base):
    __tablename__ = 'interact_semantic_daily_quota'

    id = Column(Text, primary_key=True)
    company_user_id = Column(Text, nullable=False, index=True)
    day_start = Column(BigInteger, nullable=False)
    query_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('uq_interact_semantic_daily_quota_company_day', 'company_user_id', 'day_start', unique=True),
    )


class InteractSemanticPlanUsage(Base):
    __tablename__ = 'interact_semantic_plan_usage'

    company_user_id = Column(Text, primary_key=True)
    dataset_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(BigInteger, nullable=False)


SEMANTIC_TABLES = [
    InteractSchemaSnapshot.__table__,
    InteractCatalogObject.__table__,
    InteractCatalogField.__table__,
    InteractCatalogRelationship.__table__,
    InteractSemanticDataset.__table__,
    InteractSemanticDatasetVersion.__table__,
    InteractRowPolicy.__table__,
    InteractSemanticQueryEvent.__table__,
    InteractSemanticScanSchedule.__table__,
    InteractSemanticDailyQuota.__table__,
    InteractSemanticPlanUsage.__table__,
]


def canonical_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def canonical_schema_fingerprint(schema: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(schema, default=str))
    normalized.pop('scanned_at', None)
    collection_key = 'objects' if isinstance(normalized.get('objects'), list) else 'tables'
    objects = normalized.get(collection_key)
    if isinstance(objects, list):
        for item in objects:
            if not isinstance(item, dict):
                continue
            for key in ('columns', 'foreign_keys', 'foreignKeys', 'indexes', 'unique_keys', 'uniqueKeys'):
                if isinstance(item.get(key), list):
                    item[key] = sorted(
                        item[key],
                        key=lambda value: json.dumps(value, sort_keys=True, default=str),
                    )
        normalized[collection_key] = sorted(
            objects,
            key=lambda item: str(
                (item or {}).get('physicalName') or (item or {}).get('name') or (item or {}).get('table') or ''
            ),
        )
    return canonical_fingerprint(normalized)


def _synced_description(
    previous_catalog_value: str | None,
    previous_scan_value: str | None,
    current_scan_value: str | None,
) -> str | None:
    previous_catalog = str(previous_catalog_value or '').strip()
    previous_scan = str(previous_scan_value or '').strip()
    current_scan = str(current_scan_value or '').strip()
    if previous_catalog and previous_catalog != previous_scan:
        return previous_catalog
    return current_scan or previous_catalog or None


def row_dict(row: Any, *, omit: set[str] | None = None) -> dict[str, Any]:
    if row is None:
        return {}
    omitted = omit or set()
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in omitted}


class InteractSemanticStore:
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
                        tables=SEMANTIC_TABLES,
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    async def create_snapshot(
        self,
        connector_id: str,
        company_user_id: str,
        schema: dict[str, Any],
        created_by: str,
    ) -> tuple[dict[str, Any], bool]:
        await self.ensure_tables()
        fingerprint = canonical_schema_fingerprint(schema)
        async with get_async_db_context() as db:
            existing = (
                await db.execute(
                    select(InteractSchemaSnapshot).where(
                        InteractSchemaSnapshot.connector_id == connector_id,
                        InteractSchemaSnapshot.fingerprint == fingerprint,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return row_dict(existing), False
            version = (
                await db.execute(
                    select(func.max(InteractSchemaSnapshot.version)).where(
                        InteractSchemaSnapshot.connector_id == connector_id
                    )
                )
            ).scalar() or 0
            now = int(time.time())
            row = InteractSchemaSnapshot(
                id=str(uuid4()),
                connector_id=connector_id,
                company_user_id=company_user_id,
                version=version + 1,
                status='ready',
                fingerprint=fingerprint,
                scanner_version='semantic-v2',
                database_product=str(schema.get('connector_type') or schema.get('database', {}).get('product') or ''),
                database_version=(schema.get('database') or {}).get('version'),
                schema_json=schema,
                created_by=created_by,
                created_at=now,
                completed_at=now,
            )
            db.add(row)
            await db.flush()
            await self._seed_catalog(db, row, created_by)
            await db.commit()
            return row_dict(row), True

    async def claim_schema_scan(
        self,
        connector_id: str,
        company_user_id: str,
        now: int,
        *,
        force: bool = False,
    ) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            if not await db.get(InteractSemanticScanSchedule, connector_id):
                db.add(
                    InteractSemanticScanSchedule(
                        connector_id=connector_id,
                        company_user_id=company_user_id,
                        next_scan_at=0,
                        lease_until=0,
                        consecutive_failures=0,
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
            conditions = [
                InteractSemanticScanSchedule.connector_id == connector_id,
                InteractSemanticScanSchedule.company_user_id == company_user_id,
                InteractSemanticScanSchedule.lease_until <= now,
            ]
            if not force:
                conditions.append(InteractSemanticScanSchedule.next_scan_at <= now)
            result = await db.execute(
                update(InteractSemanticScanSchedule)
                .where(*conditions)
                .values(lease_until=now + 900, last_started_at=now)
            )
            await db.commit()
            return bool(result.rowcount)

    async def finish_schema_scan(
        self,
        connector_id: str,
        *,
        success: bool,
        error: str | None = None,
        now: int | None = None,
    ) -> None:
        await self.ensure_tables()
        completed_at = now or int(time.time())
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticScanSchedule, connector_id)
            if not row:
                return
            if success:
                row.consecutive_failures = 0
                row.next_scan_at = completed_at + 86400
                row.last_error = None
            else:
                row.consecutive_failures += 1
                retry_delays = (3600, 14400, 86400)
                row.next_scan_at = completed_at + retry_delays[min(row.consecutive_failures - 1, 2)]
                row.last_error = str(error or 'Schema scan failed.')[:1000]
            row.lease_until = 0
            row.last_completed_at = completed_at
            await db.commit()

    async def _seed_catalog(  # noqa: C901
        self, db, snapshot: InteractSchemaSnapshot, actor: str
    ) -> None:
        schema = snapshot.schema_json or {}
        objects = schema.get('objects') or schema.get('tables') or []
        now = int(time.time())
        previous_snapshot = (
            await db.execute(
                select(InteractSchemaSnapshot)
                .where(
                    InteractSchemaSnapshot.connector_id == snapshot.connector_id,
                    InteractSchemaSnapshot.company_user_id == snapshot.company_user_id,
                    InteractSchemaSnapshot.id != snapshot.id,
                )
                .order_by(InteractSchemaSnapshot.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        previous_snapshot_id = previous_snapshot.id if previous_snapshot else None
        previous_schema_objects = {
            str(item.get('physicalName') or item.get('name') or item.get('table') or ''): item
            for item in (
                (previous_snapshot.schema_json or {}).get('objects')
                or (previous_snapshot.schema_json or {}).get('tables')
                or []
            )
            if str(item.get('physicalName') or item.get('name') or item.get('table') or '')
        }
        previous_objects = (
            (
                await db.execute(
                    select(InteractCatalogObject).where(InteractCatalogObject.snapshot_id == previous_snapshot_id)
                )
            )
            .scalars()
            .all()
            if previous_snapshot_id
            else []
        )
        previous_object_by_physical = {item.physical_name: item for item in previous_objects}
        previous_fields = (
            (
                await db.execute(
                    select(InteractCatalogField).where(
                        InteractCatalogField.catalog_object_id.in_([item.id for item in previous_objects])
                    )
                )
            )
            .scalars()
            .all()
            if previous_objects
            else []
        )
        previous_object_physical = {item.id: item.physical_name for item in previous_objects}
        previous_field_by_physical = {
            (previous_object_physical[item.catalog_object_id], item.physical_name): item for item in previous_fields
        }
        previous_field_location = {
            item.id: (previous_object_physical[item.catalog_object_id], item.physical_name) for item in previous_fields
        }
        previous_relationships = (
            (
                await db.execute(
                    select(InteractCatalogRelationship).where(
                        InteractCatalogRelationship.connector_id == snapshot.connector_id,
                        InteractCatalogRelationship.company_user_id == snapshot.company_user_id,
                        InteractCatalogRelationship.left_object_id.in_([item.id for item in previous_objects]),
                    )
                )
            )
            .scalars()
            .all()
            if previous_objects
            else []
        )
        previous_relationship_by_signature: dict[tuple[Any, ...], InteractCatalogRelationship] = {}
        for relationship in previous_relationships:
            pairs = []
            for pair in relationship.join_pairs or []:
                left_location = previous_field_location.get(str(pair.get('leftFieldId') or ''))
                right_location = previous_field_location.get(str(pair.get('rightFieldId') or ''))
                if not left_location or not right_location:
                    pairs = []
                    break
                pairs.append((left_location[1], right_location[1]))
            signature = (
                previous_object_physical.get(relationship.left_object_id),
                previous_object_physical.get(relationship.right_object_id),
                tuple(pairs),
            )
            if all(signature) and (relationship.status == 'confirmed' or relationship.source == 'admin_defined'):
                previous_relationship_by_signature[signature] = relationship
        by_physical: dict[str, InteractCatalogObject] = {}
        field_ids: dict[tuple[str, str], str] = {}
        carried_relationship_signatures: set[tuple[Any, ...]] = set()
        for item in objects:
            physical = str(item.get('physicalName') or item.get('name') or item.get('table') or '').strip()
            if not physical:
                schema_name = str(item.get('schema') or '').strip()
                table_name = str(item.get('table_name') or '').strip()
                physical = '.'.join(part for part in (schema_name, table_name) if part)
            if not physical:
                continue
            previous_object = previous_object_by_physical.get(physical)
            scanned_description = str(item.get('description') or '').strip() or None
            previous_scanned_object = previous_schema_objects.get(physical) or {}
            obj = InteractCatalogObject(
                id=str(uuid4()),
                company_user_id=snapshot.company_user_id,
                connector_id=snapshot.connector_id,
                snapshot_id=snapshot.id,
                physical_name=physical,
                object_type='view' if 'view' in str(item.get('type') or '').lower() else 'table',
                display_name=previous_object.display_name if previous_object else physical.split('.')[-1],
                description=_synced_description(
                    previous_object.description if previous_object else None,
                    previous_scanned_object.get('description'),
                    scanned_description,
                ),
                synonyms=list(previous_object.synonyms or []) if previous_object else [],
                business_domain=previous_object.business_domain if previous_object else None,
                sensitivity=previous_object.sensitivity if previous_object else 'internal',
                enabled=previous_object.enabled if previous_object else False,
                source_verified=previous_object.source_verified if previous_object else False,
                updated_by=actor,
                updated_at=now,
            )
            db.add(obj)
            by_physical[physical] = obj
            primary_keys = set(item.get('primary_key') or item.get('primaryKey') or [])
            for column in item.get('columns') or []:
                name = str(column.get('name') or column.get('column_name') or column.get('physicalName') or '').strip()
                if not name:
                    continue
                physical_type = str(column.get('type') or column.get('data_type') or 'text')
                semantic_type = self._infer_semantic_type(name, physical_type)
                previous_field = previous_field_by_physical.get((physical, name))
                scanned_field_description = str(column.get('description') or '').strip() or None
                previous_scanned_field = next(
                    (
                        previous_column
                        for previous_column in previous_scanned_object.get('columns') or []
                        if str(previous_column.get('name') or previous_column.get('physicalName') or '') == name
                    ),
                    {},
                )
                field_id = str(uuid4())
                field_ids[(physical, name)] = field_id
                db.add(
                    InteractCatalogField(
                        id=field_id,
                        catalog_object_id=obj.id,
                        physical_name=name,
                        display_name=previous_field.display_name if previous_field else name,
                        description=_synced_description(
                            previous_field.description if previous_field else None,
                            previous_scanned_field.get('description'),
                            scanned_field_description,
                        ),
                        synonyms=list(previous_field.synonyms or []) if previous_field else [],
                        physical_type=physical_type,
                        semantic_type=previous_field.semantic_type if previous_field else semantic_type,
                        nullable=str(column.get('nullable', column.get('is_nullable', 'YES'))).upper()
                        not in {'NO', 'FALSE'},
                        primary_key=bool(column.get('primary_key') or name in primary_keys),
                        readable=previous_field.readable if previous_field else False,
                        filterable=previous_field.filterable if previous_field else False,
                        groupable=previous_field.groupable if previous_field else False,
                        aggregatable=previous_field.aggregatable if previous_field else False,
                        default_aggregation=previous_field.default_aggregation if previous_field else None,
                        sensitivity=(
                            previous_field.sensitivity
                            if previous_field
                            else 'restricted'
                            if semantic_type == 'pii'
                            else 'internal'
                        ),
                        masking_rule=(
                            previous_field.masking_rule
                            if previous_field
                            else 'redact'
                            if semantic_type == 'pii'
                            else 'none'
                        ),
                        sample_values=None,
                        updated_by=actor,
                        updated_at=now,
                    )
                )
        await db.flush()
        for item in objects:
            physical = str(item.get('physicalName') or item.get('name') or item.get('table') or '').strip()
            if not physical:
                schema_name = str(item.get('schema') or '').strip()
                table_name = str(item.get('table_name') or '').strip()
                physical = '.'.join(part for part in (schema_name, table_name) if part)
            left = by_physical.get(physical)
            if not left:
                continue
            for fk in item.get('foreign_keys') or item.get('foreignKeys') or []:
                ref_physical = str(
                    fk.get('referenced_table')
                    or fk.get('referencedTable')
                    or fk.get('to_table')
                    or fk.get('references_table')
                    or ''
                ).strip()
                ref_schema = str(fk.get('referenced_schema') or fk.get('referencedSchema') or '').strip()
                if ref_schema and '.' not in ref_physical:
                    ref_physical = f'{ref_schema}.{ref_physical}'
                right = by_physical.get(ref_physical)
                local_columns = fk.get('columns') or [fk.get('column') or fk.get('column_name')]
                remote_columns = fk.get('referenced_columns') or [
                    fk.get('referenced_column') or fk.get('referencedColumn') or fk.get('references_column')
                ]
                pairs = [
                    {
                        'leftFieldId': field_ids.get((physical, str(local))),
                        'rightFieldId': field_ids.get((ref_physical, str(remote))),
                    }
                    for local, remote in zip(local_columns, remote_columns)
                ]
                pairs = [pair for pair in pairs if pair['leftFieldId'] and pair['rightFieldId']]
                if right and pairs:
                    signature = (
                        physical,
                        ref_physical,
                        tuple((str(local), str(remote)) for local, remote in zip(local_columns, remote_columns)),
                    )
                    previous_relationship = previous_relationship_by_signature.get(signature)
                    db.add(
                        InteractCatalogRelationship(
                            id=str(uuid4()),
                            company_user_id=snapshot.company_user_id,
                            connector_id=snapshot.connector_id,
                            left_object_id=left.id,
                            right_object_id=right.id,
                            relationship_type=(
                                previous_relationship.relationship_type if previous_relationship else 'many_to_one'
                            ),
                            join_type=previous_relationship.join_type if previous_relationship else 'left',
                            join_pairs=pairs,
                            source='foreign_key',
                            status=previous_relationship.status if previous_relationship else 'suggested',
                            fanout_risk=previous_relationship.fanout_risk if previous_relationship else 'low',
                            description=previous_relationship.description if previous_relationship else None,
                            confirmed_by=actor if previous_relationship else None,
                            updated_at=now,
                        )
                    )
                    carried_relationship_signatures.add(signature)

        for signature, previous_relationship in previous_relationship_by_signature.items():
            if signature in carried_relationship_signatures:
                continue
            left_physical, right_physical, physical_pairs = signature
            left = by_physical.get(str(left_physical))
            right = by_physical.get(str(right_physical))
            pairs = [
                {
                    'leftFieldId': field_ids.get((str(left_physical), str(left_name))),
                    'rightFieldId': field_ids.get((str(right_physical), str(right_name))),
                }
                for left_name, right_name in physical_pairs
            ]
            pairs = [pair for pair in pairs if pair['leftFieldId'] and pair['rightFieldId']]
            if not left or not right or len(pairs) != len(physical_pairs):
                continue
            db.add(
                InteractCatalogRelationship(
                    id=str(uuid4()),
                    company_user_id=snapshot.company_user_id,
                    connector_id=snapshot.connector_id,
                    left_object_id=left.id,
                    right_object_id=right.id,
                    relationship_type=previous_relationship.relationship_type,
                    join_type=previous_relationship.join_type,
                    join_pairs=pairs,
                    source=previous_relationship.source,
                    status=previous_relationship.status,
                    fanout_risk=previous_relationship.fanout_risk,
                    description=previous_relationship.description,
                    confirmed_by=actor,
                    updated_at=now,
                )
            )

    @staticmethod
    def _infer_semantic_type(name: str, physical_type: str) -> str:
        lowered = name.lower()
        db_type = physical_type.lower()
        if any(token in lowered for token in ('email', 'phone', 'tax_id', 'ssn', 'line_id')):
            return 'pii'
        if 'date' in db_type or 'time' in db_type or lowered.endswith('_at') or lowered.endswith('_date'):
            return 'datetime'
        if any(token in lowered for token in ('amount', 'price', 'revenue', 'cost', 'total')):
            return 'money'
        if any(token in db_type for token in ('int', 'numeric', 'decimal', 'float', 'double', 'real')):
            return 'number'
        if 'bool' in db_type:
            return 'boolean'
        if lowered == 'id' or lowered.endswith('_id'):
            return 'identifier'
        return 'string'

    async def list_snapshots(self, connector_id: str, company_user_id: str) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractSchemaSnapshot)
                        .where(
                            InteractSchemaSnapshot.connector_id == connector_id,
                            InteractSchemaSnapshot.company_user_id == company_user_id,
                        )
                        .order_by(InteractSchemaSnapshot.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [row_dict(row) for row in rows]

    async def get_snapshot(self, snapshot_id: str, company_user_id: str) -> dict[str, Any] | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSchemaSnapshot, snapshot_id)
            if not row or row.company_user_id != company_user_id:
                return None
            return row_dict(row)

    async def catalog(self, connector_id: str, company_user_id: str, snapshot_id: str | None = None) -> dict[str, Any]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            if not snapshot_id:
                snapshot_id = (
                    await db.execute(
                        select(InteractSchemaSnapshot.id)
                        .where(
                            InteractSchemaSnapshot.connector_id == connector_id,
                            InteractSchemaSnapshot.company_user_id == company_user_id,
                            InteractSchemaSnapshot.status == 'ready',
                        )
                        .order_by(InteractSchemaSnapshot.version.desc())
                        .limit(1)
                    )
                ).scalar()
            if not snapshot_id:
                return {'snapshotId': None, 'objects': [], 'relationships': []}
            objects = (
                (
                    await db.execute(
                        select(InteractCatalogObject).where(
                            InteractCatalogObject.connector_id == connector_id,
                            InteractCatalogObject.company_user_id == company_user_id,
                            InteractCatalogObject.snapshot_id == snapshot_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            object_ids = [row.id for row in objects]
            fields = (
                (
                    await db.execute(
                        select(InteractCatalogField).where(InteractCatalogField.catalog_object_id.in_(object_ids))
                    )
                )
                .scalars()
                .all()
                if object_ids
                else []
            )
            relationships = (
                (
                    await db.execute(
                        select(InteractCatalogRelationship).where(
                            InteractCatalogRelationship.connector_id == connector_id,
                            InteractCatalogRelationship.company_user_id == company_user_id,
                            InteractCatalogRelationship.left_object_id.in_(object_ids),
                        )
                    )
                )
                .scalars()
                .all()
                if object_ids
                else []
            )
            fields_by_object: dict[str, list[dict[str, Any]]] = {}
            for field in fields:
                fields_by_object.setdefault(field.catalog_object_id, []).append(row_dict(field))
            return {
                'snapshotId': snapshot_id,
                'objects': [{**row_dict(obj), 'fields': fields_by_object.get(obj.id, [])} for obj in objects],
                'relationships': [row_dict(row) for row in relationships],
            }

    async def rebase_dataset_drafts(
        self,
        connector_id: str,
        company_user_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
    ) -> int:
        old_catalog = await self.catalog(connector_id, company_user_id, from_snapshot_id)
        new_catalog = await self.catalog(connector_id, company_user_id, to_snapshot_id)

        def catalog_indexes(catalog: dict[str, Any]):
            objects = {item['id']: item['physical_name'] for item in catalog.get('objects') or []}
            fields = {
                field['id']: (objects[item['id']], field['physical_name'])
                for item in catalog.get('objects') or []
                for field in item.get('fields') or []
            }
            relationships = {}
            for relationship in catalog.get('relationships') or []:
                pairs = tuple(
                    (
                        (fields.get(pair.get('leftFieldId')) or ('', ''))[1],
                        (fields.get(pair.get('rightFieldId')) or ('', ''))[1],
                    )
                    for pair in relationship.get('join_pairs') or []
                )
                relationships[relationship['id']] = (
                    objects.get(relationship['left_object_id']),
                    objects.get(relationship['right_object_id']),
                    pairs,
                )
            return fields, relationships

        old_fields, old_relationships = catalog_indexes(old_catalog)
        new_fields, new_relationships = catalog_indexes(new_catalog)
        new_field_by_location = {location: field_id for field_id, location in new_fields.items()}
        new_relationship_by_signature = {
            signature: relationship_id for relationship_id, signature in new_relationships.items()
        }
        field_mapping = {field_id: new_field_by_location.get(location) for field_id, location in old_fields.items()}
        relationship_mapping = {
            relationship_id: new_relationship_by_signature.get(signature)
            for relationship_id, signature in old_relationships.items()
        }

        rebased = 0
        async with get_async_db_context() as db:
            datasets = (
                (
                    await db.execute(
                        select(InteractSemanticDataset).where(
                            InteractSemanticDataset.connector_id == connector_id,
                            InteractSemanticDataset.company_user_id == company_user_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for dataset in datasets:
                definition = json.loads(json.dumps(dataset.draft_definition or {}))
                if str(definition.get('snapshotId') or '') != from_snapshot_id:
                    continue
                field_ids = [
                    str(item.get('fieldId') or '')
                    for section in ('dimensions', 'measures')
                    for item in definition.get(section) or []
                ]
                relationship_ids = [str(item) for item in definition.get('relationshipIds') or []]
                if any(not field_mapping.get(field_id) for field_id in field_ids) or any(
                    not relationship_mapping.get(relationship_id) for relationship_id in relationship_ids
                ):
                    continue
                for section in ('dimensions', 'measures'):
                    for item in definition.get(section) or []:
                        item['fieldId'] = field_mapping[str(item['fieldId'])]
                definition['relationshipIds'] = [
                    relationship_mapping[relationship_id] for relationship_id in relationship_ids
                ]
                old_root = next(
                    (
                        item['physical_name']
                        for item in old_catalog.get('objects') or []
                        if item['id'] == definition.get('rootObjectId')
                    ),
                    None,
                )
                new_root = next(
                    (item['id'] for item in new_catalog.get('objects') or [] if item['physical_name'] == old_root),
                    None,
                )
                if not new_root:
                    continue
                definition['rootObjectId'] = new_root
                definition['snapshotId'] = to_snapshot_id
                dataset.draft_definition = definition
                dataset.updated_at = int(time.time())
                rebased += 1
            await db.commit()
        return rebased

    async def update_catalog_object(self, object_id: str, company_user_id: str, patch: dict[str, Any], actor: str):
        await self.ensure_tables()
        allowed = {
            'display_name',
            'description',
            'synonyms',
            'business_domain',
            'sensitivity',
            'enabled',
            'source_verified',
        }
        async with get_async_db_context() as db:
            row = await db.get(InteractCatalogObject, object_id)
            if not row or row.company_user_id != company_user_id:
                return None
            for key in allowed & patch.keys():
                setattr(row, key, patch[key])
            row.updated_by = actor
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return row_dict(row)

    async def set_catalog_objects_enabled(
        self,
        connector_id: str,
        company_user_id: str,
        snapshot_id: str,
        object_ids: list[str],
        enabled: bool,
        actor: str,
    ) -> dict[str, Any]:
        await self.ensure_tables()
        unique_ids = list(dict.fromkeys(object_ids))
        async with get_async_db_context() as db:
            latest_snapshot_id = (
                await db.execute(
                    select(InteractSchemaSnapshot.id)
                    .where(
                        InteractSchemaSnapshot.connector_id == connector_id,
                        InteractSchemaSnapshot.company_user_id == company_user_id,
                        InteractSchemaSnapshot.status == 'ready',
                    )
                    .order_by(InteractSchemaSnapshot.version.desc())
                    .limit(1)
                )
            ).scalar()
            if not latest_snapshot_id or snapshot_id != latest_snapshot_id:
                return {'status': 'stale_snapshot', 'snapshot_id': latest_snapshot_id, 'updated_count': 0}

            matched_ids = set(
                (
                    await db.execute(
                        select(InteractCatalogObject.id).where(
                            InteractCatalogObject.id.in_(unique_ids),
                            InteractCatalogObject.connector_id == connector_id,
                            InteractCatalogObject.company_user_id == company_user_id,
                            InteractCatalogObject.snapshot_id == snapshot_id,
                        )
                    )
                ).scalars()
            )
            if matched_ids != set(unique_ids):
                return {'status': 'invalid_objects', 'snapshot_id': snapshot_id, 'updated_count': 0}

            now = int(time.time())
            await db.execute(
                update(InteractCatalogObject)
                .where(
                    InteractCatalogObject.id.in_(unique_ids),
                    InteractCatalogObject.connector_id == connector_id,
                    InteractCatalogObject.company_user_id == company_user_id,
                    InteractCatalogObject.snapshot_id == snapshot_id,
                )
                .values(enabled=enabled, updated_by=actor, updated_at=now)
            )
            await db.commit()
            return {
                'status': 'updated',
                'snapshot_id': snapshot_id,
                'updated_count': len(unique_ids),
            }

    async def bulk_catalog_authorization(
        self,
        connector_id: str,
        company_user_id: str,
        snapshot_id: str,
        object_ids: list[str],
        authorized: bool,
        actor: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_tables()
        unique_ids = list(dict.fromkeys(object_ids))
        async with get_async_db_context() as db:
            latest_snapshot_id = (
                await db.execute(
                    select(InteractSchemaSnapshot.id)
                    .where(
                        InteractSchemaSnapshot.connector_id == connector_id,
                        InteractSchemaSnapshot.company_user_id == company_user_id,
                        InteractSchemaSnapshot.status == 'ready',
                    )
                    .order_by(InteractSchemaSnapshot.version.desc())
                    .limit(1)
                )
            ).scalar()
            if not latest_snapshot_id or snapshot_id != latest_snapshot_id:
                return {
                    'status': 'stale_snapshot',
                    'snapshot_id': latest_snapshot_id,
                    'object_count': 0,
                    'field_count': 0,
                    'affected_datasets': [],
                }

            object_query = select(InteractCatalogObject).where(
                InteractCatalogObject.connector_id == connector_id,
                InteractCatalogObject.company_user_id == company_user_id,
                InteractCatalogObject.snapshot_id == snapshot_id,
            )
            if unique_ids:
                object_query = object_query.where(InteractCatalogObject.id.in_(unique_ids))
            objects = (await db.execute(object_query)).scalars().all()
            matched_ids = {item.id for item in objects}
            if (unique_ids and matched_ids != set(unique_ids)) or not objects:
                return {
                    'status': 'invalid_objects',
                    'snapshot_id': snapshot_id,
                    'object_count': 0,
                    'field_count': 0,
                    'affected_datasets': [],
                }

            fields = (
                (
                    await db.execute(
                        select(InteractCatalogField).where(
                            InteractCatalogField.catalog_object_id.in_(list(matched_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            impacted_objects = {item.physical_name for item in objects}
            impacted_fields = {
                f'{obj.physical_name}.{field.physical_name}'
                for obj in objects
                for field in fields
                if field.catalog_object_id == obj.id
            }
            affected_rows: list[InteractSemanticDataset] = []
            if not authorized:
                published_rows = (
                    (
                        await db.execute(
                            select(InteractSemanticDataset).where(
                                InteractSemanticDataset.connector_id == connector_id,
                                InteractSemanticDataset.company_user_id == company_user_id,
                                InteractSemanticDataset.status.in_(['published', 'degraded']),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for dataset in published_rows:
                    physical_objects, physical_fields = await self._dataset_physical_dependencies(
                        db,
                        dataset,
                        connector_id,
                        company_user_id,
                    )
                    if physical_objects.intersection(impacted_objects) or physical_fields.intersection(impacted_fields):
                        affected_rows.append(dataset)

            if apply:
                now = int(time.time())
                await db.execute(
                    update(InteractCatalogObject)
                    .where(InteractCatalogObject.id.in_(list(matched_ids)))
                    .values(enabled=authorized, updated_by=actor, updated_at=now)
                )
                if fields:
                    await db.execute(
                        update(InteractCatalogField)
                        .where(InteractCatalogField.id.in_([item.id for item in fields]))
                        .values(
                            readable=authorized,
                            filterable=authorized,
                            groupable=authorized,
                            aggregatable=authorized,
                            updated_by=actor,
                            updated_at=now,
                        )
                    )
                for dataset in affected_rows:
                    dataset.status = 'blocked'
                    dataset.updated_at = now
                await db.commit()

            return {
                'status': 'updated' if apply else 'preview',
                'snapshot_id': snapshot_id,
                'object_count': len(objects),
                'field_count': len(fields),
                'affected_datasets': [
                    {'id': item.id, 'name': item.name, 'status': item.status} for item in affected_rows
                ],
            }

    async def update_catalog_field(self, field_id: str, company_user_id: str, patch: dict[str, Any], actor: str):
        await self.ensure_tables()
        allowed = {
            'display_name',
            'description',
            'synonyms',
            'semantic_type',
            'readable',
            'filterable',
            'groupable',
            'aggregatable',
            'default_aggregation',
            'sensitivity',
            'masking_rule',
        }
        async with get_async_db_context() as db:
            row = await db.get(InteractCatalogField, field_id)
            if not row:
                return None
            obj = await db.get(InteractCatalogObject, row.catalog_object_id)
            if not obj or obj.company_user_id != company_user_id:
                return None
            for key in allowed & patch.keys():
                setattr(row, key, patch[key])
            row.updated_by = actor
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return {
                **row_dict(row),
                'connector_id': obj.connector_id,
                'object_physical_name': obj.physical_name,
            }

    async def upsert_relationship(self, company_user_id: str, connector_id: str, data: dict[str, Any], actor: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractCatalogRelationship, data.get('id')) if data.get('id') else None
            if row and row.company_user_id != company_user_id:
                return None
            now = int(time.time())
            if not row:
                row = InteractCatalogRelationship(
                    id=str(uuid4()),
                    company_user_id=company_user_id,
                    connector_id=connector_id,
                    left_object_id=data['left_object_id'],
                    right_object_id=data['right_object_id'],
                    relationship_type=data['relationship_type'],
                    join_type=data.get('join_type', 'left'),
                    join_pairs=data['join_pairs'],
                    source=data.get('source', 'admin_defined'),
                    status=data.get('status', 'confirmed'),
                    fanout_risk=data.get('fanout_risk', 'low'),
                    description=data.get('description'),
                    confirmed_by=actor,
                    updated_at=now,
                )
                db.add(row)
            else:
                for key in ('relationship_type', 'join_type', 'join_pairs', 'status', 'fanout_risk', 'description'):
                    if key in data:
                        setattr(row, key, data[key])
                if row.status == 'confirmed':
                    row.confirmed_by = actor
                row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return row_dict(row)

    async def get_relationship(self, relationship_id: str, company_user_id: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractCatalogRelationship, relationship_id)
            if not row or row.company_user_id != company_user_id:
                return None
            return row_dict(row)

    async def list_datasets(self, company_user_id: str, published_only: bool = False) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            stmt = select(InteractSemanticDataset).where(InteractSemanticDataset.company_user_id == company_user_id)
            if published_only:
                stmt = stmt.where(InteractSemanticDataset.status.in_(['published', 'degraded']))
            rows = (await db.execute(stmt.order_by(InteractSemanticDataset.updated_at.desc()))).scalars().all()
            return [row_dict(row) for row in rows]

    async def count_datasets(self, company_user_id: str) -> int:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            return int(
                (
                    await db.execute(
                        select(func.count(InteractSemanticDataset.id)).where(
                            InteractSemanticDataset.company_user_id == company_user_id
                        )
                    )
                ).scalar()
                or 0
            )

    async def count_events_since(self, company_user_id: str, since: int) -> int:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            return int(
                (
                    await db.execute(
                        select(func.count(InteractSemanticQueryEvent.id)).where(
                            InteractSemanticQueryEvent.company_user_id == company_user_id,
                            InteractSemanticQueryEvent.created_at >= since,
                        )
                    )
                ).scalar()
                or 0
            )

    async def reserve_daily_query(self, company_user_id: str, limit: int) -> tuple[bool, int]:
        """Atomically reserve a daily query slot; failed attempts also count."""
        await self.ensure_tables()
        now = int(time.time())
        day_start = _business_day_start(now)
        row_id = f'{company_user_id}:{day_start}'
        async with get_async_db_context() as db:
            updated = await db.execute(
                update(InteractSemanticDailyQuota)
                .where(
                    InteractSemanticDailyQuota.id == row_id,
                    InteractSemanticDailyQuota.query_count < limit,
                )
                .values(
                    query_count=InteractSemanticDailyQuota.query_count + 1,
                    updated_at=now,
                )
            )
            if updated.rowcount:
                await db.commit()
                current = await db.get(InteractSemanticDailyQuota, row_id)
                return True, int(current.query_count)

            existing = await db.get(InteractSemanticDailyQuota, row_id)
            if existing:
                await db.rollback()
                return False, int(existing.query_count)

            prior_count = int(
                (
                    await db.execute(
                        select(func.count(InteractSemanticQueryEvent.id)).where(
                            InteractSemanticQueryEvent.company_user_id == company_user_id,
                            InteractSemanticQueryEvent.created_at >= day_start,
                        )
                    )
                ).scalar()
                or 0
            )
            if prior_count >= limit:
                await db.rollback()
                return False, prior_count

            db.add(
                InteractSemanticDailyQuota(
                    id=row_id,
                    company_user_id=company_user_id,
                    day_start=day_start,
                    query_count=prior_count + 1,
                    updated_at=now,
                )
            )
            try:
                await db.commit()
                return True, prior_count + 1
            except IntegrityError:
                await db.rollback()

        return await self.reserve_daily_query(company_user_id, limit)

    async def reserve_dataset_slot(self, company_user_id: str, limit: int) -> tuple[bool, int]:
        await self.ensure_tables()
        now = int(time.time())
        async with get_async_db_context() as db:
            updated = await db.execute(
                update(InteractSemanticPlanUsage)
                .where(
                    InteractSemanticPlanUsage.company_user_id == company_user_id,
                    InteractSemanticPlanUsage.dataset_count < limit,
                )
                .values(
                    dataset_count=InteractSemanticPlanUsage.dataset_count + 1,
                    updated_at=now,
                )
            )
            if updated.rowcount:
                await db.commit()
                current = await db.get(InteractSemanticPlanUsage, company_user_id)
                return True, int(current.dataset_count)

            existing = await db.get(InteractSemanticPlanUsage, company_user_id)
            if existing:
                await db.rollback()
                return False, int(existing.dataset_count)

            actual_count = int(
                (
                    await db.execute(
                        select(func.count(InteractSemanticDataset.id)).where(
                            InteractSemanticDataset.company_user_id == company_user_id
                        )
                    )
                ).scalar()
                or 0
            )
            if actual_count >= limit:
                await db.rollback()
                return False, actual_count
            db.add(
                InteractSemanticPlanUsage(
                    company_user_id=company_user_id,
                    dataset_count=actual_count + 1,
                    updated_at=now,
                )
            )
            try:
                await db.commit()
                return True, actual_count + 1
            except IntegrityError:
                await db.rollback()
        return await self.reserve_dataset_slot(company_user_id, limit)

    async def release_dataset_slot(self, company_user_id: str) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            await db.execute(
                update(InteractSemanticPlanUsage)
                .where(
                    InteractSemanticPlanUsage.company_user_id == company_user_id,
                    InteractSemanticPlanUsage.dataset_count > 0,
                )
                .values(
                    dataset_count=InteractSemanticPlanUsage.dataset_count - 1,
                    updated_at=int(time.time()),
                )
            )
            await db.commit()

    async def governance_summary(self, company_user_id: str) -> dict[str, Any]:
        await self.ensure_tables()
        now = int(time.time())
        day_start = _business_day_start(now)
        month_start = now - 30 * 86400

        def status_columns():
            denied = or_(
                InteractSemanticQueryEvent.error_code.like('%-NOT-ALLOWED'),
                InteractSemanticQueryEvent.error_code.in_(
                    [
                        'DB-COMPANY-CONTEXT-MISSING',
                        'DB-CONNECTOR-DISABLED',
                        'QUERY-DAILY-LIMIT',
                        'SEMANTIC-PLAN-INACTIVE',
                    ]
                ),
            )
            return (
                func.count(InteractSemanticQueryEvent.id).label('total'),
                func.sum(case((InteractSemanticQueryEvent.status == 'success', 1), else_=0)).label('success'),
                func.sum(case((denied, 1), else_=0)).label('denied'),
                func.sum(case((InteractSemanticQueryEvent.error_code == 'DB-TIMEOUT', 1), else_=0)).label('timeout'),
                func.avg(InteractSemanticQueryEvent.duration_ms).label('average_duration_ms'),
                func.max(InteractSemanticQueryEvent.created_at).label('last_query_at'),
            )

        def metric(row: Any) -> dict[str, Any]:
            total = int(row.total or 0)
            success = int(row.success or 0)
            denied = int(row.denied or 0)
            timeout = int(row.timeout or 0)
            return {
                'total': total,
                'success': success,
                'denied': denied,
                'timeout': timeout,
                'failed': max(0, total - success - denied - timeout),
                'successRate': round(success / total * 100, 1) if total else None,
                'deniedRate': round(denied / total * 100, 1) if total else None,
                'averageDurationMs': round(float(row.average_duration_ms or 0)),
                'lastQueryAt': int(row.last_query_at) if row.last_query_at else None,
            }

        async with get_async_db_context() as db:
            datasets = (
                (
                    await db.execute(
                        select(InteractSemanticDataset)
                        .where(InteractSemanticDataset.company_user_id == company_user_id)
                        .order_by(InteractSemanticDataset.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            snapshots = (
                (
                    await db.execute(
                        select(InteractSchemaSnapshot)
                        .where(InteractSchemaSnapshot.company_user_id == company_user_id)
                        .order_by(InteractSchemaSnapshot.connector_id, InteractSchemaSnapshot.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            latest_snapshots: dict[str, dict[str, Any]] = {}
            for snapshot in snapshots:
                latest_snapshots.setdefault(snapshot.connector_id, row_dict(snapshot))

            today = (
                await db.execute(
                    select(*status_columns()).where(
                        InteractSemanticQueryEvent.company_user_id == company_user_id,
                        InteractSemanticQueryEvent.created_at >= day_start,
                    )
                )
            ).one()
            month = (
                await db.execute(
                    select(*status_columns()).where(
                        InteractSemanticQueryEvent.company_user_id == company_user_id,
                        InteractSemanticQueryEvent.created_at >= month_start,
                    )
                )
            ).one()
            per_connector_rows = (
                await db.execute(
                    select(InteractSemanticQueryEvent.connector_id, *status_columns())
                    .where(
                        InteractSemanticQueryEvent.company_user_id == company_user_id,
                        InteractSemanticQueryEvent.created_at >= month_start,
                    )
                    .group_by(InteractSemanticQueryEvent.connector_id)
                )
            ).all()
            quota_row = await db.get(InteractSemanticDailyQuota, f'{company_user_id}:{day_start}')

        usage_today = metric(today)
        usage_today['quotaUsed'] = int(quota_row.query_count) if quota_row else usage_today['total']
        return {
            'datasets': [row_dict(row) for row in datasets],
            'latestSnapshots': latest_snapshots,
            'usageToday': usage_today,
            'usage30d': metric(month),
            'connectorUsage30d': {str(row.connector_id): metric(row) for row in per_connector_rows if row.connector_id},
            'generatedAt': now,
        }

    async def _dataset_physical_dependencies(
        self,
        db,
        dataset: InteractSemanticDataset,
        connector_id: str,
        company_user_id: str,
    ) -> tuple[set[str], set[str]]:
        version = await db.get(InteractSemanticDatasetVersion, dataset.current_version_id)
        if not version:
            return set(), set()
        objects = (
            (
                await db.execute(
                    select(InteractCatalogObject).where(
                        InteractCatalogObject.connector_id == connector_id,
                        InteractCatalogObject.company_user_id == company_user_id,
                        InteractCatalogObject.snapshot_id == version.snapshot_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        object_map = {item.id: item for item in objects}
        fields = (
            (
                await db.execute(
                    select(InteractCatalogField).where(InteractCatalogField.catalog_object_id.in_(list(object_map)))
                )
            )
            .scalars()
            .all()
            if object_map
            else []
        )
        field_map = {item.id: item for item in fields}
        definition = version.definition or {}
        referenced_ids = {
            str(item.get('fieldId') or '')
            for section in ('dimensions', 'measures')
            for item in definition.get(section) or []
        }
        referenced_ids.update(
            str(condition.get('fieldId') or '')
            for measure in definition.get('measures') or []
            for condition in measure.get('filters') or []
        )
        relationship_ids = set(definition.get('relationshipIds') or [])
        if relationship_ids:
            relationships = (
                (
                    await db.execute(
                        select(InteractCatalogRelationship).where(
                            InteractCatalogRelationship.connector_id == connector_id,
                            InteractCatalogRelationship.company_user_id == company_user_id,
                            InteractCatalogRelationship.id.in_(relationship_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for relationship in relationships:
                for pair in relationship.join_pairs or []:
                    referenced_ids.update(
                        {
                            str(pair.get('leftFieldId') or ''),
                            str(pair.get('rightFieldId') or ''),
                        }
                    )
        physical_objects: set[str] = set()
        physical_fields: set[str] = set()
        root_object = object_map.get(str(definition.get('rootObjectId') or ''))
        if root_object:
            physical_objects.add(root_object.physical_name)
        for field_id in referenced_ids:
            field = field_map.get(field_id)
            obj = object_map.get(field.catalog_object_id) if field else None
            if field and obj:
                physical_objects.add(obj.physical_name)
                physical_fields.add(f'{obj.physical_name}.{field.physical_name}')
        return physical_objects, physical_fields

    async def mark_connector_drift(
        self,
        connector_id: str,
        company_user_id: str,
        status: str,
        impacted_objects: set[str] | None = None,
        impacted_fields: set[str] | None = None,
    ) -> int:
        if status not in {'degraded', 'blocked'}:
            raise ValueError('Invalid drift status.')
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractSemanticDataset).where(
                            InteractSemanticDataset.connector_id == connector_id,
                            InteractSemanticDataset.company_user_id == company_user_id,
                            InteractSemanticDataset.status.in_(['published', 'degraded']),
                        )
                    )
                )
                .scalars()
                .all()
            )
            affected = 0
            for row in rows:
                if impacted_objects is not None or impacted_fields is not None:
                    physical_objects, physical_fields = await self._dataset_physical_dependencies(
                        db,
                        row,
                        connector_id,
                        company_user_id,
                    )
                    if not (
                        physical_objects.intersection(impacted_objects or set())
                        or physical_fields.intersection(impacted_fields or set())
                    ):
                        continue
                row.status = status
                row.updated_at = int(time.time())
                affected += 1
            await db.commit()
            return affected

    async def get_dataset(self, dataset_id: str, company_user_id: str | None = None):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticDataset, dataset_id)
            if not row or (company_user_id and row.company_user_id != company_user_id):
                return None
            return row_dict(row)

    async def save_dataset(self, company_user_id: str, data: dict[str, Any], actor: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticDataset, data.get('id')) if data.get('id') else None
            if row and row.company_user_id != company_user_id:
                return None
            now = int(time.time())
            if not row:
                row = InteractSemanticDataset(
                    id=str(uuid4()),
                    company_user_id=company_user_id,
                    connector_id=data['connector_id'],
                    slug=data['slug'],
                    name=data['name'],
                    description=data.get('description', ''),
                    business_domain=data.get('business_domain'),
                    status='draft',
                    current_version_id=None,
                    draft_definition=data.get('definition') or {},
                    access_mode=data.get('access_mode', 'company_admins'),
                    allowed_member_ids=data.get('allowed_member_ids') or [],
                    allowed_group_ids=data.get('allowed_group_ids') or [],
                    allowed_model_ids=data.get('allowed_model_ids') or [],
                    allowed_channel_ids=data.get('allowed_channel_ids') or [],
                    allowed_workflow_ids=data.get('allowed_workflow_ids') or [],
                    created_by=actor,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                definition_changed = (
                    'definition' in data and (data.get('definition') or {}) != (row.draft_definition or {})
                )
                for key in (
                    'slug',
                    'name',
                    'description',
                    'business_domain',
                    'draft_definition',
                    'access_mode',
                    'allowed_member_ids',
                    'allowed_group_ids',
                    'allowed_model_ids',
                    'allowed_channel_ids',
                    'allowed_workflow_ids',
                ):
                    source_key = 'definition' if key == 'draft_definition' else key
                    if source_key in data:
                        setattr(row, key, data[source_key])
                if definition_changed and row.status in {'published', 'degraded', 'blocked'}:
                    row.status = 'draft'
                row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return row_dict(row)

    async def publish_dataset(self, dataset_id: str, company_user_id: str, actor: str, validation: dict[str, Any]):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticDataset, dataset_id)
            if not row or row.company_user_id != company_user_id:
                return None
            if not validation.get('ok'):
                raise ValueError('Dataset validation failed.')
            snapshot_id = str((row.draft_definition or {}).get('snapshotId') or '')
            version = (
                await db.execute(
                    select(func.max(InteractSemanticDatasetVersion.version)).where(
                        InteractSemanticDatasetVersion.dataset_id == dataset_id
                    )
                )
            ).scalar() or 0
            published = InteractSemanticDatasetVersion(
                id=str(uuid4()),
                dataset_id=dataset_id,
                version=version + 1,
                snapshot_id=snapshot_id,
                definition=json.loads(json.dumps(row.draft_definition)),
                validation=validation,
                published_by=actor,
                published_at=int(time.time()),
            )
            db.add(published)
            await db.flush()
            row.current_version_id = published.id
            row.status = 'published'
            row.updated_at = int(time.time())
            await db.commit()
            return row_dict(published)

    async def get_dataset_version(self, version_id: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticDatasetVersion, version_id)
            return row_dict(row) if row else None

    async def activate_dataset_version(
        self,
        dataset_id: str,
        version_id: str,
        company_user_id: str,
    ) -> dict[str, Any] | None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            dataset = await db.get(InteractSemanticDataset, dataset_id)
            version = await db.get(InteractSemanticDatasetVersion, version_id)
            if (
                not dataset
                or dataset.company_user_id != company_user_id
                or not version
                or version.dataset_id != dataset_id
            ):
                return None
            dataset.current_version_id = version.id
            dataset.draft_definition = json.loads(json.dumps(version.definition))
            dataset.status = 'published'
            dataset.updated_at = int(time.time())
            await db.commit()
            await db.refresh(dataset)
            return row_dict(dataset)

    async def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractSemanticDatasetVersion)
                        .where(InteractSemanticDatasetVersion.dataset_id == dataset_id)
                        .order_by(InteractSemanticDatasetVersion.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [row_dict(row) for row in rows]

    async def list_policies(self, dataset_id: str, company_user_id: str) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractRowPolicy).where(
                            InteractRowPolicy.dataset_id == dataset_id,
                            InteractRowPolicy.company_user_id == company_user_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [row_dict(row) for row in rows]

    async def save_policy(self, company_user_id: str, dataset_id: str, data: dict[str, Any], actor: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractRowPolicy, data.get('id')) if data.get('id') else None
            if row and (row.company_user_id != company_user_id or row.dataset_id != dataset_id):
                return None
            now = int(time.time())
            if not row:
                row = InteractRowPolicy(
                    id=str(uuid4()),
                    company_user_id=company_user_id,
                    dataset_id=dataset_id,
                    name=data['name'],
                    status=data.get('status', 'draft'),
                    principal_type=data['principal_type'],
                    principal_ids=data.get('principal_ids') or [],
                    expression=data['expression'],
                    deny_if_unresolved=data.get('deny_if_unresolved', True),
                    created_by=actor,
                    updated_at=now,
                )
                db.add(row)
            else:
                for key in ('name', 'status', 'principal_type', 'principal_ids', 'expression', 'deny_if_unresolved'):
                    if key in data:
                        setattr(row, key, data[key])
                row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return row_dict(row)

    async def delete_policy(self, policy_id: str, company_user_id: str, dataset_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractRowPolicy, policy_id)
            if not row or row.company_user_id != company_user_id or row.dataset_id != dataset_id:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def record_event(self, data: dict[str, Any]) -> None:
        await self.ensure_tables()
        fields = {column.name for column in InteractSemanticQueryEvent.__table__.columns}
        payload = {key: value for key, value in data.items() if key in fields}
        payload.setdefault('id', str(uuid4()))
        payload.setdefault('created_at', int(time.time()))
        payload.setdefault('row_count', 0)
        if payload.get('error_detail'):
            payload['error_detail'] = str(payload['error_detail'])[:2000]
        async with get_async_db_context() as db:
            db.add(InteractSemanticQueryEvent(**payload))
            await db.commit()

    async def list_events(self, company_user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractSemanticQueryEvent)
                        .where(InteractSemanticQueryEvent.company_user_id == company_user_id)
                        .order_by(InteractSemanticQueryEvent.created_at.desc())
                        .limit(max(1, min(limit, 500)))
                    )
                )
                .scalars()
                .all()
            )
            return [row_dict(row) for row in rows]

    async def get_event_by_request_id(self, request_id: str, company_user_id: str):
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(InteractSemanticQueryEvent)
                    .where(
                        InteractSemanticQueryEvent.request_id == request_id,
                        InteractSemanticQueryEvent.company_user_id == company_user_id,
                    )
                    .order_by(InteractSemanticQueryEvent.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row_dict(row) if row else None

    async def delete_dataset(self, dataset_id: str, company_user_id: str) -> bool:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            row = await db.get(InteractSemanticDataset, dataset_id)
            if not row or row.company_user_id != company_user_id:
                return False
            await db.execute(delete(InteractRowPolicy).where(InteractRowPolicy.dataset_id == dataset_id))
            await db.execute(
                delete(InteractSemanticDatasetVersion).where(InteractSemanticDatasetVersion.dataset_id == dataset_id)
            )
            await db.delete(row)
            await db.execute(
                update(InteractSemanticPlanUsage)
                .where(
                    InteractSemanticPlanUsage.company_user_id == company_user_id,
                    InteractSemanticPlanUsage.dataset_count > 0,
                )
                .values(
                    dataset_count=InteractSemanticPlanUsage.dataset_count - 1,
                    updated_at=int(time.time()),
                )
            )
            await db.commit()
            return True


InteractSemantic = InteractSemanticStore()
