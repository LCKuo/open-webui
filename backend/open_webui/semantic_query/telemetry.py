from __future__ import annotations

from typing import Any

from opentelemetry import metrics

_meter = metrics.get_meter('open_webui.semantic_query')
_requests = _meter.create_counter('semantic_query_requests_total')
_duration = _meter.create_histogram('semantic_query_duration_ms', unit='ms')
_rows = _meter.create_histogram('semantic_query_rows_returned', unit='rows')
_denials = _meter.create_counter('semantic_query_denials_total')
_timeouts = _meter.create_counter('semantic_query_timeouts_total')
_cost_rejections = _meter.create_counter('semantic_query_cost_rejections_total')
_plan_failures = _meter.create_counter('query_plan_validation_failures_total')
_schema_scans = _meter.create_counter('schema_scan_requests_total')
_schema_scan_duration = _meter.create_histogram('schema_scan_duration_ms', unit='ms')
_schema_drift = _meter.create_counter('schema_drift_objects_total')


def record_query_event(event: dict[str, Any]) -> None:
    status = str(event.get('status') or 'error')
    code = str(event.get('error_code') or '')
    attributes = {
        'status': status,
        'database': str(event.get('connector_type') or 'unknown'),
        'dataset': str(event.get('dataset_id') or 'unknown'),
    }
    _requests.add(1, attributes)
    _duration.record(max(0, int(event.get('duration_ms') or 0)), attributes)
    _rows.record(max(0, int(event.get('row_count') or 0)), attributes)
    if code.endswith('NOT-ALLOWED') or code.startswith(('DB-', 'ROW-POLICY-')):
        _denials.add(1, {'code': code, 'database': attributes['database']})
    if 'TIMEOUT' in code or 'timed out' in str(event.get('error_detail') or '').lower():
        _timeouts.add(1, {'database': attributes['database']})
    if code in {'QUERY-COST-LIMIT', 'QUERY-COST-REVIEW-REQUIRED', 'QUERY-RESULT-LIMIT'}:
        _cost_rejections.add(1, {'code': code, 'database': attributes['database']})
    if code in {'QUERY-PLAN-INVALID', 'QUERY-TYPE-MISMATCH'}:
        _plan_failures.add(1, {'code': code})


def record_schema_scan(
    *,
    database: str,
    status: str,
    duration_ms: int,
    drift: dict[str, Any] | None = None,
) -> None:
    attributes = {'database': database or 'unknown', 'status': status}
    _schema_scans.add(1, attributes)
    _schema_scan_duration.record(max(0, duration_ms), attributes)
    if drift:
        severity = str(drift.get('severity') or 'none')
        changed = sum(
            len(drift.get(key) or [])
            for key in (
                'addedObjects',
                'removedObjects',
                'changedObjects',
                'addedFields',
                'removedFields',
                'changedFields',
            )
        )
        if changed:
            _schema_drift.add(changed, {'database': database or 'unknown', 'severity': severity})
