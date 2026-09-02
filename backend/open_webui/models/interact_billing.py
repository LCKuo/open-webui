from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from sqlalchemy import BigInteger, Column, Integer, JSON, Text, delete, select, update


_tables_ready = False
_tables_lock = asyncio.Lock()


class InteractBillingSettlement(Base):
    __tablename__ = 'interact_billing_settlement'

    id = Column(Text, primary_key=True)
    request_id = Column(Text, nullable=False, unique=True, index=True)
    path = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(BigInteger, nullable=False, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class InteractBillingSettlementsTable:
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
                        tables=[InteractBillingSettlement.__table__],
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    async def enqueue(self, path: str, payload: dict[str, Any], error: str) -> None:
        await self.ensure_tables()
        request_id = str(payload.get('request_id') or '').strip()
        if not request_id:
            raise ValueError('A billing settlement request_id is required.')

        now = int(time.time())
        async with get_async_db_context() as db:
            existing = (
                await db.execute(
                    select(InteractBillingSettlement).where(
                        InteractBillingSettlement.request_id == request_id
                    )
                )
            ).scalar_one_or_none()
            if existing:
                await db.execute(
                    update(InteractBillingSettlement)
                    .where(InteractBillingSettlement.id == existing.id)
                    .values(
                        path=path,
                        payload=payload,
                        next_attempt_at=now,
                        last_error=error[:2000],
                        updated_at=now,
                    )
                )
            else:
                db.add(
                    InteractBillingSettlement(
                        id=str(uuid4()),
                        request_id=request_id,
                        path=path,
                        payload=payload,
                        attempts=0,
                        next_attempt_at=now,
                        last_error=error[:2000],
                        created_at=now,
                        updated_at=now,
                    )
                )
            await db.commit()

    async def due(self, limit: int = 20) -> list[dict[str, Any]]:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(InteractBillingSettlement)
                        .where(InteractBillingSettlement.next_attempt_at <= int(time.time()))
                        .order_by(InteractBillingSettlement.created_at.asc())
                        .limit(max(1, min(limit, 100)))
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    'id': row.id,
                    'request_id': row.request_id,
                    'path': row.path,
                    'payload': row.payload,
                    'attempts': row.attempts,
                }
                for row in rows
            ]

    async def mark_completed(self, settlement_id: str) -> None:
        await self.ensure_tables()
        async with get_async_db_context() as db:
            await db.execute(
                delete(InteractBillingSettlement).where(
                    InteractBillingSettlement.id == settlement_id
                )
            )
            await db.commit()

    async def mark_failed(self, settlement_id: str, attempts: int, error: str) -> None:
        await self.ensure_tables()
        next_attempts = max(1, attempts + 1)
        retry_delay = min(900, 15 * (2 ** min(next_attempts - 1, 6)))
        now = int(time.time())
        async with get_async_db_context() as db:
            await db.execute(
                update(InteractBillingSettlement)
                .where(InteractBillingSettlement.id == settlement_id)
                .values(
                    attempts=next_attempts,
                    next_attempt_at=now + retry_delay,
                    last_error=error[:2000],
                    updated_at=now,
                )
            )
            await db.commit()


InteractBillingSettlements = InteractBillingSettlementsTable()
