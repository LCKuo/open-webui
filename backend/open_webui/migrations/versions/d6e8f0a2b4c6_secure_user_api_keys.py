"""Secure user API keys and index account lookups.

Revision ID: d6e8f0a2b4c6
Revises: a2d4f6b8c1e3
Create Date: 2026-08-17
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd6e8f0a2b4c6'
down_revision: str | None = 'a2d4f6b8c1e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if 'api_key' not in inspector.get_table_names():
        return

    index_names = {index['name'] for index in inspector.get_indexes('api_key')}
    if 'ix_api_key_user_id' not in index_names:
        op.create_index('ix_api_key_user_id', 'api_key', ['user_id'])

    api_key = sa.table(
        'api_key',
        sa.column('id', sa.Text),
        sa.column('key', sa.Text),
        sa.column('data', sa.JSON),
    )
    rows = connection.execute(
        sa.select(api_key.c.id, api_key.c.key, api_key.c.data).where(api_key.c.key.like('sk-%'))
    ).fetchall()
    for key_id, secret, existing_data in rows:
        if not secret:
            continue
        data = existing_data if isinstance(existing_data, dict) else {}
        connection.execute(
            sa.update(api_key)
            .where(api_key.c.id == key_id)
            .values(
                key=f'sha256:{hashlib.sha256(secret.encode("utf-8")).hexdigest()}',
                data={
                    **data,
                    'version': 2,
                    'name': data.get('name') or '舊版 API Key',
                    'prefix': data.get('prefix') or secret[:10],
                    'last_four': data.get('last_four') or secret[-4:],
                    'scopes': ['models:read', 'chat:write'],
                },
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if 'api_key' not in inspector.get_table_names():
        return
    index_names = {index['name'] for index in inspector.get_indexes('api_key')}
    if 'ix_api_key_user_id' in index_names:
        op.drop_index('ix_api_key_user_id', table_name='api_key')
