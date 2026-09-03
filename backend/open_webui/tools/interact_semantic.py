from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from open_webui.semantic_query.contracts import QueryPlan
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.service import (
    accessible_dataset_manifests,
    execute_query,
    runtime_context,
)


def _result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _trusted_product_runtime(request: Request | None) -> bool:
    return bool(request and getattr(request.state, 'interact_channel_runtime', False) is True)


def _error_result(error: SemanticQueryError) -> str:
    payload: dict[str, Any] = {'ok': False, 'error': error.public()}
    if error.code in {'QUERY-PLAN-INVALID', 'QUERY-TYPE-MISMATCH'}:
        payload['queryPlanSchema'] = QueryPlan.model_json_schema()
        payload['repairHint'] = (
            'Use only QueryPlan schema properties; groupBy is not supported. '
            'Select up to 8 dimension IDs from the catalog. Grouping is inferred from dimensions. '
            'Boolean filters require JSON true/false, not strings or numbers. '
            'Numeric filters require JSON numbers. datasetId must be the catalog datasetId, not a name or slug. '
            'Correct the plan and retry this semantic tool; do not use raw database queries as a fallback.'
        )
    return _result(payload)


async def interact_semantic_catalog(
    query: str = '',
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Find published company datasets before answering a structured-data question.
    Use this instead of guessing table or column names. It returns only semantic dataset,
    dimension, measure, and metric IDs authorized for the current company, user, model,
    channel, and workflow. Candidates are ranked by selectorScore. Compare whenToUse,
    notFor, grain, and examples; if the top candidates describe different business meanings
    or all scores are low, ask one short clarification question instead of guessing.
    If no dataset is returned, do not bypass the result with interact_database_query. Explain
    that no authorized semantic dataset is available, or ask an administrator to publish or
    grant one.

    :param query: Short business intent, for example "monthly sales ranking by salesperson".
    :return: JSON containing authorized semantic dataset manifests.
    """
    try:
        context = await runtime_context(__user__, __metadata__, trusted_product_runtime=_trusted_product_runtime(__request__))
        datasets = await accessible_dataset_manifests(context, query)
        return _result(
            {
                'ok': True,
                'datasets': datasets,
                'queryPlanSchema': QueryPlan.model_json_schema(),
                'selectionPolicy': {
                    'rawDatabaseFallbackAllowed': False,
                    'whenEmpty': (
                        'No authorized semantic dataset is available. Do not query raw tables; '
                        'explain the authorization or publication requirement.'
                    ),
                },
            }
        )
    except SemanticQueryError as error:
        return _error_result(error)
    except Exception as error:
        semantic_error = SemanticQueryError('QUERY-EXECUTION-FAILED', str(error))
        return _result({'ok': False, 'error': semantic_error.public()})


async def interact_semantic_query(
    plan: dict[str, Any],
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute an authorized semantic Query Plan. Call interact_semantic_catalog first.
    Never submit SQL, physical table names, column names, or join expressions. The plan may
    use only IDs returned by the catalog tool. Example:
     {"version":"1","datasetId":"sales","dimensions":["salesperson"],
      "measures":["revenue"],"timeRange":{"dimensionId":"closed_at",
      "preset":"this_month","timezone":"Asia/Taipei"},
      "filters":{"operator":"and","conditions":[{"fieldId":"region",
      "operator":"eq","value":"north"}]},
      "orderBy":[{"fieldId":"revenue","direction":"desc"}],"limit":5}
    Use only properties in queryPlanSchema returned by the catalog. Do not send groupBy,
    table, sql, or columns. dimensions already define grouping and allow at most 8 IDs.
    For a boolean dimension such as active, filters.conditions[].value must be JSON true
    or false, never "true", "false", 1, or 0. Numeric fields use JSON numbers.
    For a simple list use datasetId, dimensions, filters, and limit; measures are optional.

    :param plan: Strict Query Plan v1 using published semantic IDs only.
    :return: JSON result rows or a stable error code with corrective guidance.
    """
    try:
        context = await runtime_context(__user__, __metadata__, trusted_product_runtime=_trusted_product_runtime(__request__))
        return _result(await execute_query(plan, context))
    except SemanticQueryError as error:
        return _error_result(error)
    except Exception as error:
        semantic_error = SemanticQueryError('QUERY-EXECUTION-FAILED', str(error))
        return _result({'ok': False, 'error': semantic_error.public()})
