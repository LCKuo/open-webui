"""add enterprise email workflow tables

Revision ID: 9c4e6f8a1b20
Revises: 8a7b3d1e5f20
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9c4e6f8a1b20'
down_revision: str | None = '8a7b3d1e5f20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create(name: str, *columns, indexes: list[tuple[str, list[str], bool]] | None = None) -> None:
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
        'interact_email_connector',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('company_email', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False, server_default='resend'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.Text(), nullable=False, server_default='unconfigured'),
        sa.Column('api_key_encrypted', sa.Text()),
        sa.Column('key_last4', sa.Text()),
        sa.Column('webhook_secret_encrypted', sa.Text()),
        sa.Column('from_name', sa.Text()),
        sa.Column('from_address', sa.Text(), nullable=False),
        sa.Column('reply_to', sa.Text()),
        sa.Column('verified_domain', sa.Text()),
        sa.Column('access_mode', sa.Text(), nullable=False, server_default='company_admins'),
        sa.Column('allowed_member_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_group_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_workflow_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_channel_ids', sa.JSON(), nullable=False),
        sa.Column('cc_policy', sa.JSON(), nullable=False),
        sa.Column('recipient_policy', sa.JSON(), nullable=False),
        sa.Column('daily_send_limit', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('max_recipients_per_send', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('last_test_at', sa.BigInteger()),
        sa.Column('last_error', sa.Text()),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_email_connector_company_user_id', ['company_user_id'], False),
            ('ix_interact_email_connector_company_updated', ['company_user_id', 'updated_at'], False),
            ('ix_interact_email_connector_company_enabled', ['company_user_id', 'enabled'], False),
        ],
    )
    _create(
        'interact_email_delivery',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('workflow_id', sa.Text()),
        sa.Column('workflow_run_id', sa.Text()),
        sa.Column('requested_by', sa.Text(), nullable=False),
        sa.Column('channel_id', sa.Text()),
        sa.Column('provider_message_id', sa.Text()),
        sa.Column('idempotency_key', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='queued'),
        sa.Column('recipient_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('recipient_domains', sa.JSON(), nullable=False),
        sa.Column('to_encrypted', sa.Text(), nullable=False),
        sa.Column('cc_encrypted', sa.Text()),
        sa.Column('subject_encrypted', sa.Text(), nullable=False),
        sa.Column('content_encrypted', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.Text(), nullable=False),
        sa.Column('error_code', sa.Text()),
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('sent_at', sa.BigInteger()),
        indexes=[
            ('ix_interact_email_delivery_company_user_id', ['company_user_id'], False),
            ('ix_interact_email_delivery_connector_id', ['connector_id'], False),
            ('ix_interact_email_delivery_workflow_id', ['workflow_id'], False),
            ('ix_interact_email_delivery_workflow_run_id', ['workflow_run_id'], False),
            ('ix_interact_email_delivery_provider_message_id', ['provider_message_id'], False),
            ('ix_interact_email_delivery_idempotency_key', ['idempotency_key'], True),
            ('ix_interact_email_delivery_company_created', ['company_user_id', 'created_at'], False),
            ('ix_interact_email_delivery_connector_created', ['connector_id', 'created_at'], False),
        ],
    )
    _create(
        'interact_email_event',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('delivery_id', sa.Text()),
        sa.Column('provider_event_id', sa.Text(), nullable=False),
        sa.Column('provider_message_id', sa.Text()),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('occurred_at', sa.BigInteger(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_email_event_company_user_id', ['company_user_id'], False),
            ('ix_interact_email_event_delivery_id', ['delivery_id'], False),
            ('ix_interact_email_event_provider_event_id', ['provider_event_id'], True),
            ('ix_interact_email_event_provider_message_id', ['provider_message_id'], False),
        ],
    )
    _create(
        'workflow_checkpoint',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('workflow_run_id', sa.Text(), nullable=False),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('workflow_id', sa.Text(), nullable=False),
        sa.Column('node_id', sa.Text(), nullable=False),
        sa.Column('wait_type', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='waiting'),
        sa.Column('state_encrypted', sa.Text(), nullable=False),
        sa.Column('prompt', sa.JSON(), nullable=False),
        sa.Column('payload_hash', sa.Text()),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('consumed_at', sa.BigInteger()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_workflow_checkpoint_workflow_run_id', ['workflow_run_id'], True),
            ('ix_workflow_checkpoint_company_user_id', ['company_user_id'], False),
            ('ix_workflow_checkpoint_workflow_id', ['workflow_id'], False),
        ],
    )
    _create(
        'workflow_approval',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('workflow_run_id', sa.Text(), nullable=False),
        sa.Column('checkpoint_id', sa.Text(), nullable=False),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('node_id', sa.Text(), nullable=False),
        sa.Column('payload_hash', sa.Text(), nullable=False),
        sa.Column('decision', sa.Text(), nullable=False),
        sa.Column('decided_by', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text()),
        sa.Column('decided_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_workflow_approval_workflow_run_id', ['workflow_run_id'], False),
            ('ix_workflow_approval_checkpoint_id', ['checkpoint_id'], False),
            ('ix_workflow_approval_company_user_id', ['company_user_id'], False),
        ],
    )


def downgrade() -> None:
    for table_name in [
        'workflow_approval',
        'workflow_checkpoint',
        'interact_email_event',
        'interact_email_delivery',
        'interact_email_connector',
    ]:
        if table_name in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table_name)

