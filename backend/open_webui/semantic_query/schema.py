from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from open_webui.models.interact_data_connectors import (
    InteractDataConnectorModel,
    InteractDataConnectors,
)
from open_webui.models.interact_semantic import InteractSemantic
from open_webui.semantic_query.errors import ERROR_MESSAGES, SemanticQueryError
from open_webui.semantic_query.telemetry import record_schema_scan
from open_webui.tools.interact_database import (
    _database_error_code,
    _filter_schema_scan_for_policy,
    scan_data_connector_schema,
)

log = logging.getLogger(__name__)


def schema_scan_error(error: Exception) -> SemanticQueryError:
    database_code = _database_error_code(error)
    code = database_code if database_code in ERROR_MESSAGES else 'QUERY-EXECUTION-FAILED'
    return SemanticQueryError(
        code,
        retryable=code in {'DB-CONNECTION-FAILED', 'DB-TIMEOUT'},
    )


def _schema_index(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get('name') or item.get('table') or ''): item
        for item in schema.get('tables') or schema.get('objects') or []
        if str(item.get('name') or item.get('table') or '')
    }


def snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    left, right = _schema_index(before), _schema_index(after)
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    changed = []
    added_fields: list[str] = []
    removed_fields: list[str] = []
    changed_fields: list[str] = []
    for name in sorted(set(left).intersection(right)):
        left_columns = {str(item.get('name')): item for item in left[name].get('columns') or []}
        right_columns = {str(item.get('name')): item for item in right[name].get('columns') or []}
        added_fields.extend(f'{name}.{field}' for field in sorted(set(right_columns) - set(left_columns)))
        removed_fields.extend(f'{name}.{field}' for field in sorted(set(left_columns) - set(right_columns)))
        for field in sorted(set(left_columns).intersection(right_columns)):
            left_type = str(left_columns[field].get('type') or '').lower()
            right_type = str(right_columns[field].get('type') or '').lower()
            if left_type != right_type:
                changed_fields.append(f'{name}.{field}')
        if any(item.startswith(f'{name}.') for item in [*removed_fields, *changed_fields]) or left[name].get(
            'foreign_keys'
        ) != right[name].get('foreign_keys'):
            changed.append(name)
    severity = 'breaking' if removed or removed_fields or changed else 'additive' if added or added_fields else 'none'
    return {
        'severity': severity,
        'addedObjects': added,
        'removedObjects': removed,
        'changedObjects': changed,
        'addedFields': added_fields,
        'removedFields': removed_fields,
        'changedFields': changed_fields,
    }


async def scan_connector_snapshot(
    connector: InteractDataConnectorModel,
    actor: str,
    max_tables: int = 200,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        scanned = await scan_data_connector_schema(connector.id, max_tables)
        schema = {
            **scanned,
            'tables': _filter_schema_scan_for_policy(connector, scanned, None),
        }
        snapshot, created = await InteractSemantic.create_snapshot(
            connector.id,
            connector.company_user_id,
            schema,
            actor,
        )
        drift = None
        if created:
            snapshots = await InteractSemantic.list_snapshots(connector.id, connector.company_user_id)
            if len(snapshots) > 1:
                drift = snapshot_diff(snapshots[1]['schema_json'], snapshot['schema_json'])
                if drift['severity'] == 'breaking':
                    drift['affectedDatasets'] = await InteractSemantic.mark_connector_drift(
                        connector.id,
                        connector.company_user_id,
                        'blocked',
                        impacted_objects=set(drift['removedObjects'] + drift['changedObjects']),
                        impacted_fields=set(drift['removedFields'] + drift['changedFields']),
                    )
                else:
                    drift['affectedDatasets'] = 0
                drift['rebasedDrafts'] = await InteractSemantic.rebase_dataset_drafts(
                    connector.id,
                    connector.company_user_id,
                    snapshots[1]['id'],
                    snapshot['id'],
                )
        record_schema_scan(
            database=connector.connector_type,
            status='success',
            duration_ms=int((time.monotonic() - started) * 1000),
            drift=drift,
        )
        return {'ok': True, 'created': created, 'snapshot': snapshot, 'drift': drift}
    except Exception:
        record_schema_scan(
            database=connector.connector_type,
            status='error',
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise


async def semantic_schema_scheduler_loop() -> None:
    try:
        poll_seconds = max(30, int(os.environ.get('SEMANTIC_SCHEMA_SCAN_POLL_SECONDS', '60')))
    except ValueError:
        poll_seconds = 60
    while True:
        try:
            now = int(time.time())
            for connector in await InteractDataConnectors.list_all():
                if not connector.enabled or connector.id == 'webui_local':
                    continue
                if not await InteractSemantic.claim_schema_scan(connector.id, connector.company_user_id, now):
                    continue
                try:
                    await scan_connector_snapshot(connector, 'system:schema-scheduler', 500)
                    await InteractSemantic.finish_schema_scan(connector.id, success=True)
                except Exception as error:
                    safe_error = schema_scan_error(error)
                    await InteractSemantic.finish_schema_scan(
                        connector.id,
                        success=False,
                        error=f'{safe_error.code}: {safe_error.public()["message"]}',
                    )
                    log.warning(
                        'Scheduled semantic schema scan failed for connector %s with code %s',
                        connector.id,
                        safe_error.code,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Semantic schema scheduler iteration failed.')
        await asyncio.sleep(poll_seconds)
