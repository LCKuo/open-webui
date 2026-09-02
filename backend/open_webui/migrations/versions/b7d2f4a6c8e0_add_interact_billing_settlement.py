"""Add durable Interact billing settlements.

Revision ID: b7d2f4a6c8e0
Revises: a9c4e7f1b2d3
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b7d2f4a6c8e0'
down_revision: str | None = 'a9c4e7f1b2d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if 'interact_billing_settlement' in inspector.get_table_names():
        return

    op.create_table(
        'interact_billing_settlement',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.BigInteger(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_interact_billing_settlement_request_id',
        'interact_billing_settlement',
        ['request_id'],
        unique=True,
    )
    op.create_index(
        'ix_interact_billing_settlement_next_attempt_at',
        'interact_billing_settlement',
        ['next_attempt_at'],
    )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if 'interact_billing_settlement' not in inspector.get_table_names():
        return
    op.drop_index(
        'ix_interact_billing_settlement_next_attempt_at',
        table_name='interact_billing_settlement',
    )
    op.drop_index(
        'ix_interact_billing_settlement_request_id',
        table_name='interact_billing_settlement',
    )
    op.drop_table('interact_billing_settlement')
