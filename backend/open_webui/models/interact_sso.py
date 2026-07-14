from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from typing import Any
from uuid import uuid4

from open_webui.internal.db import Base, async_engine, get_async_db_context
from sqlalchemy import BigInteger, Column, Text, delete, select, update

_tables_ready = False
_tables_lock = asyncio.Lock()


class InteractSsoTicket(Base):
    __tablename__ = 'interact_sso_ticket'

    id = Column(Text, primary_key=True)
    token_hash = Column(Text, nullable=False, unique=True, index=True)
    user_id = Column(Text, nullable=False, index=True)
    company_user_id = Column(Text, nullable=False, index=True)
    target_path = Column(Text, nullable=False)
    return_url = Column(Text, nullable=True)
    expires_at = Column(BigInteger, nullable=False, index=True)
    used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InteractSsoTicketsTable:
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
                        tables=[InteractSsoTicket.__table__],
                        checkfirst=True,
                    )
                )
            _tables_ready = True

    async def issue(
        self,
        user_id: str,
        company_user_id: str,
        target_path: str,
        return_url: str | None,
        ttl_seconds: int = 90,
    ) -> str:
        await self.ensure_tables()
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        async with get_async_db_context() as db:
            await db.execute(
                delete(InteractSsoTicket).where(
                    (InteractSsoTicket.expires_at < now - 86400)
                    | ((InteractSsoTicket.used_at.is_not(None)) & (InteractSsoTicket.used_at < now - 86400))
                )
            )
            db.add(InteractSsoTicket(
                id=str(uuid4()),
                token_hash=_hash_token(token),
                user_id=user_id,
                company_user_id=company_user_id,
                target_path=target_path,
                return_url=return_url,
                expires_at=now + max(30, min(ttl_seconds, 180)),
                used_at=None,
                created_at=now,
            ))
            await db.commit()
        return token

    async def consume(self, token: str) -> dict[str, Any] | None:
        await self.ensure_tables()
        now = int(time.time())
        token_hash = _hash_token(token)
        async with get_async_db_context() as db:
            row = (await db.execute(
                select(InteractSsoTicket).where(
                    InteractSsoTicket.token_hash == token_hash,
                    InteractSsoTicket.used_at.is_(None),
                    InteractSsoTicket.expires_at >= now,
                )
            )).scalar_one_or_none()
            if not row:
                return None
            claimed = await db.execute(
                update(InteractSsoTicket)
                .where(
                    InteractSsoTicket.id == row.id,
                    InteractSsoTicket.used_at.is_(None),
                )
                .values(used_at=now)
            )
            if not claimed.rowcount:
                await db.rollback()
                return None
            await db.commit()
            return {
                'user_id': row.user_id,
                'company_user_id': row.company_user_id,
                'target_path': row.target_path,
                'return_url': row.return_url,
            }


InteractSsoTickets = InteractSsoTicketsTable()
