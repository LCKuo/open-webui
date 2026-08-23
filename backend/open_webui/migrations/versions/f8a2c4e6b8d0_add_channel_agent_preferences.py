"""add channel agent bindings and LINE agent preferences

Revision ID: f8a2c4e6b8d0
Revises: e7f9a1c2d3b4
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f8a2c4e6b8d0'
down_revision: str | None = 'e7f9a1c2d3b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    # Interact ORM tables are created after Alembic on a fresh installation.
    # Existing installations already have the table and still need the columns.
    if 'interact_channel' in table_names:
        channel_columns = {
            column['name'] for column in inspector.get_columns('interact_channel')
        }
        if 'agent_bindings_json' not in channel_columns:
            op.add_column(
                'interact_channel',
                sa.Column('agent_bindings_json', sa.Text(), nullable=False, server_default='[]'),
            )
        if 'liff_id' not in channel_columns:
            op.add_column('interact_channel', sa.Column('liff_id', sa.Text(), nullable=True))

    inspector = sa.inspect(op.get_bind())
    if 'interact_line_agent_preference' not in inspector.get_table_names():
        op.create_table(
            'interact_line_agent_preference',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('channel_id', sa.Text(), nullable=False),
            sa.Column('external_user_hash', sa.Text(), nullable=False),
            sa.Column('model_id', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.UniqueConstraint(
                'channel_id',
                'external_user_hash',
                name='uq_interact_line_agent_preference_user',
            ),
        )
        op.create_index(
            'ix_interact_line_agent_preference_channel',
            'interact_line_agent_preference',
            ['channel_id', 'model_id'],
            unique=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if 'interact_line_agent_preference' in inspector.get_table_names():
        op.drop_table('interact_line_agent_preference')
    if 'interact_channel' not in inspector.get_table_names():
        return
    channel_columns = {
        column['name'] for column in inspector.get_columns('interact_channel')
    }
    if 'liff_id' in channel_columns:
        op.drop_column('interact_channel', 'liff_id')
    if 'agent_bindings_json' in channel_columns:
        op.drop_column('interact_channel', 'agent_bindings_json')
