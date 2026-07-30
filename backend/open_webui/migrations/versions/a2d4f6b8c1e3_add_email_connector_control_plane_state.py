"""add email connector control plane state

Revision ID: a2d4f6b8c1e3
Revises: 9c4e6f8a1b20
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a2d4f6b8c1e3'
down_revision: str | None = '9c4e6f8a1b20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = 'interact_email_connector'


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {
        column['name'] for column in inspector.get_columns(TABLE_NAME)
    }
    additions = [
        sa.Column(
            'managed_by',
            sa.Text(),
            nullable=False,
            server_default='company_portal',
        ),
        sa.Column(
            'control_plane_revision',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
        sa.Column(
            'control_plane_status',
            sa.Text(),
            nullable=False,
            server_default='active',
        ),
        sa.Column('quarantined_at', sa.BigInteger()),
        sa.Column('last_control_plane_seen_at', sa.BigInteger()),
    ]
    for column in additions:
        if column.name not in existing:
            op.add_column(TABLE_NAME, column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = {
        column['name'] for column in inspector.get_columns(TABLE_NAME)
    }
    for name in [
        'last_control_plane_seen_at',
        'quarantined_at',
        'control_plane_status',
        'control_plane_revision',
        'managed_by',
    ]:
        if name in existing:
            op.drop_column(TABLE_NAME, name)
