"""Enterprise workspace-model activation limits.

Website owns the entitlement; WebUI owns the model inventory and enforcement.
Every mutation that can add an active workspace model must run inside this guard.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import HTTPException, status
from open_webui.models.models import Models
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from sqlalchemy.ext.asyncio import AsyncSession

_company_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_locks_guard = asyncio.Lock()


@dataclass(frozen=True)
class ModelCapacity:
    company_user_id: str
    active_count: int
    active_limit: int
    owner_user_ids: tuple[str, ...]


async def _company_lock(company_user_id: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _company_locks.get(company_user_id)
        if lock is None:
            lock = asyncio.Lock()
            _company_locks[company_user_id] = lock
        return lock


def _entitlement_values(identity, user_id: str) -> tuple[str, int, list[str]]:
    company_user = identity.company_user or {}
    entitlement = identity.model_entitlement or {}
    company_user_id = str(company_user.get('id') or '').strip()
    if not company_user_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='無法確認企業模型額度，請稍後再試或聯繫管理員。',
        )

    raw_limit = entitlement.get('activeModelLimit', company_user.get('activeModelLimit', 1))
    try:
        active_limit = max(0, int(raw_limit))
    except (TypeError, ValueError):
        active_limit = 1

    raw_owner_ids = entitlement.get('ownerUserIds')
    owner_user_ids = [
        str(value).strip() for value in (raw_owner_ids if isinstance(raw_owner_ids, list) else []) if str(value).strip()
    ]
    owner_user_ids.append(user_id)
    return company_user_id, active_limit, list(dict.fromkeys(owner_user_ids))


@asynccontextmanager
async def enforce_model_activation_limit(
    *,
    user,
    additions: int,
    entitlement_user=None,
    db: AsyncSession | None = None,
) -> AsyncIterator[ModelCapacity | None]:
    """Serialize and validate active-model additions for a non-admin company user."""

    if user.role == 'admin' or additions <= 0:
        yield None
        return

    if not is_billing_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='企業模型額度服務尚未設定，暫時無法啟用模型。',
        )

    target_user = entitlement_user or user
    try:
        identity = await InteractBillingClient().resolve_identity(target_user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='目前無法連線企業模型額度服務，模型尚未啟用，請稍後再試。',
        ) from exc

    company_user_id, active_limit, owner_user_ids = _entitlement_values(identity, target_user.id)
    lock = await _company_lock(company_user_id)
    async with lock:
        active_count = await Models.count_active_workspace_models_by_user_ids(owner_user_ids, db=db)
        capacity = ModelCapacity(
            company_user_id=company_user_id,
            active_count=active_count,
            active_limit=active_limit,
            owner_user_ids=tuple(owner_user_ids),
        )
        if active_count + additions > active_limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f'已達企業可啟用模型上限（{active_count}/{active_limit}）。'
                    '請先停用其他模型，或請管理員至 InteractCloudService 調整上限。'
                ),
            )
        yield capacity
