"""add LINE identity links and role-aware rich menus

Revision ID: 8a7b3d1e5f20
Revises: 7f6a2c9d4e10
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8a7b3d1e5f20'
down_revision: str | None = '7f6a2c9d4e10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create(
    name: str,
    *columns,
    indexes: list[tuple[str, list[str], bool]] | None = None,
) -> None:
    inspector = sa.inspect(op.get_bind())
    if name not in inspector.get_table_names():
        op.create_table(name, *columns)
    inspector = sa.inspect(op.get_bind())
    existing = {item['name'] for item in inspector.get_indexes(name)}
    for index_name, fields, unique in indexes or []:
        if index_name not in existing:
            op.create_index(index_name, name, fields, unique=unique)


def upgrade() -> None:
    _create(
        'interact_line_identity_link',
        sa.Column('state_hash', sa.Text(), primary_key=True),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('external_user_hash', sa.Text(), nullable=False),
        sa.Column('external_user_id', sa.Text(), nullable=False),
        sa.Column('link_token_hash', sa.Text(), nullable=False),
        sa.Column('nonce_hash', sa.Text(), nullable=True, unique=True),
        sa.Column('company_user_id', sa.Text(), nullable=True),
        sa.Column('company_email', sa.Text(), nullable=True),
        sa.Column('company_member_id', sa.Text(), nullable=True),
        sa.Column('member_email', sa.Text(), nullable=True),
        sa.Column('member_role', sa.Text(), nullable=True),
        sa.Column('member_status', sa.Text(), nullable=True),
        sa.Column('group_ids_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.Text(), nullable=False, server_default='issued'),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('consumed_at', sa.BigInteger(), nullable=True),
        indexes=[
            (
                'ix_interact_line_identity_link_channel',
                ['channel_id', 'status', 'expires_at'],
                False,
            ),
            (
                'ix_interact_line_identity_link_user',
                ['channel_id', 'external_user_hash'],
                False,
            ),
        ],
    )

    _create(
        'interact_line_identity_binding',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('external_user_hash', sa.Text(), nullable=False),
        sa.Column('external_user_id', sa.Text(), nullable=False),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('company_email', sa.Text(), nullable=False),
        sa.Column('company_member_id', sa.Text(), nullable=True),
        sa.Column('member_email', sa.Text(), nullable=False),
        sa.Column('member_role', sa.Text(), nullable=False),
        sa.Column('member_status', sa.Text(), nullable=False, server_default='active'),
        sa.Column('group_ids_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('role_verified_at', sa.BigInteger(), nullable=False),
        sa.Column('linked_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'channel_id',
            'external_user_hash',
            name='uq_interact_line_identity_channel_user',
        ),
        indexes=[
            (
                'ix_interact_line_identity_company_member',
                ['company_user_id', 'company_member_id'],
                False,
            ),
            (
                'ix_interact_line_identity_email',
                ['company_user_id', 'member_email'],
                False,
            ),
        ],
    )

    _create(
        'interact_channel_rich_menu_variant',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('audience', sa.Text(), nullable=False),
        sa.Column('tab', sa.Text(), nullable=False),
        sa.Column('rich_menu_id', sa.Text(), nullable=True),
        sa.Column('alias_id', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('synced_at', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'channel_id',
            'audience',
            'tab',
            name='uq_interact_rich_menu_variant',
        ),
        sa.UniqueConstraint(
            'channel_id',
            'alias_id',
            name='uq_interact_rich_menu_variant_alias',
        ),
        indexes=[
            (
                'ix_interact_rich_menu_variant_channel',
                ['channel_id', 'audience'],
                False,
            )
        ],
    )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        'interact_channel_rich_menu_variant',
        'interact_line_identity_binding',
        'interact_line_identity_link',
    ):
        if table in existing:
            op.drop_table(table)
