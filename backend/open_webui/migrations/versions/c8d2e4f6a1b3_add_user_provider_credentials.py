"""add encrypted user provider credentials

Revision ID: c8d2e4f6a1b3
Revises: c4f7a1d9e2b6
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c8d2e4f6a1b3'
down_revision: str | None = 'c4f7a1d9e2b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = 'user_provider_credential'


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME in inspector.get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('connection_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('base_url', sa.Text(), nullable=False),
        sa.Column('auth_type', sa.Text(), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('key_last4', sa.Text(), nullable=False),
        sa.Column('last_verified_at', sa.BigInteger(), nullable=True),
        sa.Column('verification_status', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'connection_id',
            name='uq_user_provider_credential_connection',
        ),
    )
    op.create_index(
        'ix_user_provider_credential_user_id',
        TABLE_NAME,
        ['user_id'],
        unique=False,
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME in inspector.get_table_names():
        op.drop_table(TABLE_NAME)
