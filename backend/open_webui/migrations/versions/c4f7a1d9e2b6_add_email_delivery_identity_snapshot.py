"""add email delivery identity snapshot

Revision ID: c4f7a1d9e2b6
Revises: b7d2f4a6c8e0
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4f7a1d9e2b6'
down_revision: str | None = 'b7d2f4a6c8e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = 'interact_email_delivery'


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns(TABLE_NAME)}
    if 'from_address' not in existing:
        op.add_column(TABLE_NAME, sa.Column('from_address', sa.Text(), nullable=True))
    if 'reply_to' not in existing:
        op.add_column(TABLE_NAME, sa.Column('reply_to', sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns(TABLE_NAME)}
    if 'reply_to' in existing:
        op.drop_column(TABLE_NAME, 'reply_to')
    if 'from_address' in existing:
        op.drop_column(TABLE_NAME, 'from_address')
