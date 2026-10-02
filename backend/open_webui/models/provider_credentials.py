"""Encrypted, user-owned OpenAI-compatible provider connections."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.oauth import decrypt_data, encrypt_data
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Text, UniqueConstraint, delete, select
from sqlalchemy.ext.asyncio import AsyncSession


class UserProviderCredential(Base):
    __tablename__ = 'user_provider_credential'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    connection_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)
    base_url = Column(Text, nullable=False)
    auth_type = Column(Text, nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    key_last4 = Column(Text, nullable=False)
    last_verified_at = Column(BigInteger, nullable=True)
    verification_status = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'connection_id', name='uq_user_provider_credential_connection'),
        Index('ix_user_provider_credential_user_id', 'user_id'),
    )


class UserProviderCredentialModel(BaseModel):
    id: str
    user_id: str
    connection_id: str
    name: str
    provider: str
    base_url: str
    auth_type: str
    key_last4: str
    last_verified_at: int | None = None
    verification_status: str | None = None
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


def normalize_provider_url(base_url: str) -> str:
    """Normalize routing-significant URL parts."""
    parsed = urlsplit(str(base_url or '').strip())
    path = parsed.path.rstrip('/')
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, '', ''))


def provider_connection_id(base_url: str, api_config: dict | None = None) -> str:
    """Return a stable identity for one user-owned provider endpoint."""
    config = api_config if isinstance(api_config, dict) else {}
    identity = {
        'url': normalize_provider_url(base_url),
        'auth_type': config.get('auth_type') or 'bearer',
        'provider': config.get('provider') or '',
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _encrypt_api_key(api_key: str) -> str:
    return encrypt_data({'value': api_key})


def _decrypt_api_key(value: str) -> str:
    return str(decrypt_data(value).get('value') or '')


class UserProviderCredentialsTable:
    async def list_by_user_id(
        self,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> list[UserProviderCredentialModel]:
        async with get_async_db_context(db) as session:
            rows = (
                (
                    await session.execute(
                        select(UserProviderCredential)
                        .where(UserProviderCredential.user_id == user_id)
                        .order_by(UserProviderCredential.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [UserProviderCredentialModel.model_validate(row) for row in rows]

    async def has_for_user(self, user_id: str, db: AsyncSession | None = None) -> bool:
        async with get_async_db_context(db) as session:
            row = (
                await session.execute(
                    select(UserProviderCredential.id)
                    .where(UserProviderCredential.user_id == user_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row is not None

    async def get_by_connection_id(
        self,
        user_id: str,
        connection_id: str,
        db: AsyncSession | None = None,
    ) -> UserProviderCredentialModel | None:
        async with get_async_db_context(db) as session:
            row = (
                (
                    await session.execute(
                        select(UserProviderCredential).where(
                            UserProviderCredential.user_id == user_id,
                            UserProviderCredential.connection_id == connection_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            return UserProviderCredentialModel.model_validate(row) if row else None

    async def get_api_keys_by_user_id(
        self,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> dict[str, str]:
        async with get_async_db_context(db) as session:
            rows = (
                (await session.execute(select(UserProviderCredential).where(UserProviderCredential.user_id == user_id)))
                .scalars()
                .all()
            )
            return {row.connection_id: secret for row in rows if (secret := _decrypt_api_key(row.api_key_encrypted))}

    async def get_api_key(
        self,
        user_id: str,
        connection_id: str,
        db: AsyncSession | None = None,
    ) -> str | None:
        async with get_async_db_context(db) as session:
            row = (
                (
                    await session.execute(
                        select(UserProviderCredential).where(
                            UserProviderCredential.user_id == user_id,
                            UserProviderCredential.connection_id == connection_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if not row:
                return None
            return _decrypt_api_key(row.api_key_encrypted) or None

    async def upsert(
        self,
        user_id: str,
        *,
        name: str,
        provider: str,
        base_url: str,
        auth_type: str,
        api_key: str | None,
        db: AsyncSession | None = None,
    ) -> UserProviderCredentialModel:
        normalized_url = normalize_provider_url(base_url)
        connection_id = provider_connection_id(
            normalized_url,
            {'provider': provider, 'auth_type': auth_type},
        )
        clean_key = str(api_key or '').strip()
        if auth_type != 'none' and not clean_key:
            raise ValueError('API key is required')

        now = int(time.time())
        async with get_async_db_context(db) as session:
            row = (
                (
                    await session.execute(
                        select(UserProviderCredential).where(
                            UserProviderCredential.user_id == user_id,
                            UserProviderCredential.connection_id == connection_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = UserProviderCredential(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    connection_id=connection_id,
                    created_at=now,
                )
                session.add(row)

            row.name = name
            row.provider = provider
            row.base_url = normalized_url
            row.auth_type = auth_type
            row.api_key_encrypted = _encrypt_api_key(clean_key)
            row.key_last4 = clean_key[-4:] if clean_key else ''
            row.last_verified_at = None
            row.verification_status = None
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return UserProviderCredentialModel.model_validate(row)

    async def get_runtime_connections(
        self,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> list[dict]:
        async with get_async_db_context(db) as session:
            rows = (
                (
                    await session.execute(
                        select(UserProviderCredential)
                        .where(UserProviderCredential.user_id == user_id)
                        .order_by(UserProviderCredential.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    'url': row.base_url,
                    'key': _decrypt_api_key(row.api_key_encrypted),
                    'config': {
                        'enable': True,
                        'auth_type': row.auth_type,
                        'prefix_id': 'own-'
                        + hashlib.sha256(f'{row.user_id}:{row.connection_id}'.encode()).hexdigest()[:10],
                        'connection_type': 'external',
                        'user_supplied': True,
                        'personal_provider': row.provider,
                        'personal_owner_id': row.user_id,
                        'personal_connection_id': row.connection_id,
                        'personal_connection_name': row.name,
                    },
                }
                for row in rows
            ]

    async def set_verification_status(
        self,
        user_id: str,
        connection_id: str,
        verification_status: str,
        db: AsyncSession | None = None,
    ) -> UserProviderCredentialModel | None:
        now = int(time.time())
        async with get_async_db_context(db) as session:
            row = (
                (
                    await session.execute(
                        select(UserProviderCredential).where(
                            UserProviderCredential.user_id == user_id,
                            UserProviderCredential.connection_id == connection_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            row.last_verified_at = now
            row.verification_status = verification_status
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return UserProviderCredentialModel.model_validate(row)

    async def delete(
        self,
        user_id: str,
        connection_id: str,
        db: AsyncSession | None = None,
    ) -> bool:
        async with get_async_db_context(db) as session:
            result = await session.execute(
                delete(UserProviderCredential).where(
                    UserProviderCredential.user_id == user_id,
                    UserProviderCredential.connection_id == connection_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def delete_all_by_user_id(
        self,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> None:
        async with get_async_db_context(db) as session:
            await session.execute(delete(UserProviderCredential).where(UserProviderCredential.user_id == user_id))
            await session.commit()


UserProviderCredentials = UserProviderCredentialsTable()
