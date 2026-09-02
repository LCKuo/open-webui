"""add product-scoped LINE employee identity

Revision ID: a9c4e7f1b2d3
Revises: f8a2c4e6b8d0
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c4e7f1b2d3'
down_revision: str | None = 'f8a2c4e6b8d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_columns(table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns(table_name)}
    additions = [
        ('identity_source', sa.Text(), False, 'company_portal'),
        ('product_key', sa.Text(), True, None),
        ('product_instance_id', sa.Text(), True, None),
        ('product_user_id', sa.Text(), True, None),
        ('product_team_codes_json', sa.Text(), False, '[]'),
    ]
    for name, column_type, nullable, default in additions:
        if name in columns:
            continue
        op.add_column(
            table_name,
            sa.Column(name, column_type, nullable=nullable, server_default=default),
        )


def upgrade() -> None:
    _add_columns('interact_line_identity_link')
    _add_columns('interact_line_identity_binding')
    inspector = sa.inspect(op.get_bind())
    if 'interact_line_identity_binding' in inspector.get_table_names():
        indexes = {index['name'] for index in inspector.get_indexes('interact_line_identity_binding')}
        if 'ix_interact_line_identity_product_user' not in indexes:
            op.create_index(
                'ix_interact_line_identity_product_user',
                'interact_line_identity_binding',
                ['product_key', 'product_instance_id', 'product_user_id'],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if 'interact_line_identity_binding' in inspector.get_table_names():
        indexes = {index['name'] for index in inspector.get_indexes('interact_line_identity_binding')}
        if 'ix_interact_line_identity_product_user' in indexes:
            op.drop_index('ix_interact_line_identity_product_user', table_name='interact_line_identity_binding')
    for table_name in ('interact_line_identity_binding', 'interact_line_identity_link'):
        inspector = sa.inspect(op.get_bind())
        if table_name not in inspector.get_table_names():
            continue
        columns = {column['name'] for column in inspector.get_columns(table_name)}
        for name in (
            'product_team_codes_json',
            'product_user_id',
            'product_instance_id',
            'product_key',
            'identity_source',
        ):
            if name in columns:
                op.drop_column(table_name, name)
