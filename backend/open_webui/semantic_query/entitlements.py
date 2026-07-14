from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException

from open_webui.models.interact_semantic import InteractSemantic
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def semantic_entitlements(company_user_id: str) -> dict[str, Any]:
    if not is_billing_enabled():
        raise SemanticQueryError(
            'SEMANTIC-ENTITLEMENT-UNAVAILABLE',
            'Company plan verification is not configured.',
            retryable=True,
        )
    now = time.monotonic()
    cached = _cache.get(company_user_id)
    if cached and cached[0] > now:
        return cached[1]

    lock = _locks.setdefault(company_user_id, asyncio.Lock())
    async with lock:
        cached = _cache.get(company_user_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            data = await InteractBillingClient().semantic_entitlements(company_user_id)
        except HTTPException as error:
            raise SemanticQueryError(
                'SEMANTIC-ENTITLEMENT-UNAVAILABLE',
                str(error.detail),
                retryable=error.status_code >= 500,
            ) from error
        _cache[company_user_id] = (time.monotonic() + _CACHE_TTL_SECONDS, data)
        return data


async def enforce_dataset_limit(company_user_id: str) -> dict[str, Any]:
    entitlements = await semantic_entitlements(company_user_id)
    if not entitlements.get('operational'):
        raise SemanticQueryError('SEMANTIC-PLAN-INACTIVE')
    limit = int((entitlements.get('limits') or {}).get('datasets') or 0)
    if limit <= 0:
        raise SemanticQueryError('SEMANTIC-DATASET-LIMIT')
    reserved, used = await InteractSemantic.reserve_dataset_slot(company_user_id, limit)
    if not reserved:
        raise SemanticQueryError('SEMANTIC-DATASET-LIMIT')
    return {'limit': limit, 'used': used, 'reserved': True}


async def reserve_query_entitlement(company_user_id: str) -> dict[str, Any]:
    entitlements = await semantic_entitlements(company_user_id)
    if not entitlements.get('operational'):
        raise SemanticQueryError('SEMANTIC-PLAN-INACTIVE')
    limit = int((entitlements.get('limits') or {}).get('dailyQueries') or 0)
    if limit <= 0:
        raise SemanticQueryError('QUERY-DAILY-LIMIT')
    reserved, used = await InteractSemantic.reserve_daily_query(company_user_id, limit)
    if not reserved:
        raise SemanticQueryError('QUERY-DAILY-LIMIT')
    return {'limit': limit, 'used': used}
