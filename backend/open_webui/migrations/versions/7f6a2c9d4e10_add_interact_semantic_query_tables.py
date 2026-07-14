"""add interact semantic query tables

Revision ID: 7f6a2c9d4e10
Revises: 42e2978c7933
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7f6a2c9d4e10'
down_revision: str | None = '42e2978c7933'
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
        'interact_schema_snapshot',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('fingerprint', sa.Text(), nullable=False),
        sa.Column('scanner_version', sa.Text(), nullable=False),
        sa.Column('database_product', sa.Text(), nullable=False),
        sa.Column('database_version', sa.Text(), nullable=True),
        sa.Column('schema_json', sa.JSON(), nullable=False),
        sa.Column('error_code', sa.Text(), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('completed_at', sa.BigInteger(), nullable=True),
        indexes=[
            ('ix_interact_schema_snapshot_connector_id', ['connector_id'], False),
            ('ix_interact_schema_snapshot_company_user_id', ['company_user_id'], False),
            ('uq_interact_schema_snapshot_version', ['connector_id', 'version'], True),
            ('uq_interact_schema_snapshot_fingerprint', ['connector_id', 'fingerprint'], True),
        ],
    )
    _create(
        'interact_catalog_object',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('snapshot_id', sa.Text(), nullable=False),
        sa.Column('physical_name', sa.Text(), nullable=False),
        sa.Column('object_type', sa.Text(), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('synonyms', sa.JSON(), nullable=False),
        sa.Column('business_domain', sa.Text()),
        sa.Column('sensitivity', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('source_verified', sa.Boolean(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_catalog_object_company_user_id', ['company_user_id'], False),
            ('ix_interact_catalog_object_connector_id', ['connector_id'], False),
            ('ix_interact_catalog_object_snapshot_id', ['snapshot_id'], False),
            ('uq_interact_catalog_object_snapshot_name', ['snapshot_id', 'physical_name'], True),
        ],
    )
    _create(
        'interact_catalog_field',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('catalog_object_id', sa.Text(), nullable=False),
        sa.Column('physical_name', sa.Text(), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('synonyms', sa.JSON(), nullable=False),
        sa.Column('physical_type', sa.Text(), nullable=False),
        sa.Column('semantic_type', sa.Text(), nullable=False),
        sa.Column('nullable', sa.Boolean(), nullable=False),
        sa.Column('primary_key', sa.Boolean(), nullable=False),
        sa.Column('readable', sa.Boolean(), nullable=False),
        sa.Column('filterable', sa.Boolean(), nullable=False),
        sa.Column('groupable', sa.Boolean(), nullable=False),
        sa.Column('aggregatable', sa.Boolean(), nullable=False),
        sa.Column('default_aggregation', sa.Text()),
        sa.Column('sensitivity', sa.Text(), nullable=False),
        sa.Column('masking_rule', sa.Text(), nullable=False),
        sa.Column('sample_values', sa.JSON()),
        sa.Column('updated_by', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_catalog_field_catalog_object_id', ['catalog_object_id'], False),
            ('uq_interact_catalog_field_object_name', ['catalog_object_id', 'physical_name'], True),
        ],
    )
    _create(
        'interact_catalog_relationship',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('left_object_id', sa.Text(), nullable=False),
        sa.Column('right_object_id', sa.Text(), nullable=False),
        sa.Column('relationship_type', sa.Text(), nullable=False),
        sa.Column('join_type', sa.Text(), nullable=False),
        sa.Column('join_pairs', sa.JSON(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('fanout_risk', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('confirmed_by', sa.Text()),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_catalog_relationship_company_user_id', ['company_user_id'], False),
            ('ix_interact_catalog_relationship_connector_id', ['connector_id'], False),
            ('ix_interact_catalog_relationship_left_object_id', ['left_object_id'], False),
            ('ix_interact_catalog_relationship_right_object_id', ['right_object_id'], False),
        ],
    )
    _create(
        'interact_semantic_dataset',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('business_domain', sa.Text()),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('current_version_id', sa.Text()),
        sa.Column('draft_definition', sa.JSON(), nullable=False),
        sa.Column('access_mode', sa.Text(), nullable=False),
        sa.Column('allowed_member_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_group_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_model_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_channel_ids', sa.JSON(), nullable=False),
        sa.Column('allowed_workflow_ids', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_semantic_dataset_company_user_id', ['company_user_id'], False),
            ('ix_interact_semantic_dataset_connector_id', ['connector_id'], False),
            ('uq_interact_semantic_dataset_company_slug', ['company_user_id', 'slug'], True),
        ],
    )
    _create(
        'interact_semantic_dataset_version',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('dataset_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Text(), nullable=False),
        sa.Column('definition', sa.JSON(), nullable=False),
        sa.Column('validation', sa.JSON(), nullable=False),
        sa.Column('published_by', sa.Text(), nullable=False),
        sa.Column('published_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_semantic_dataset_version_dataset_id', ['dataset_id'], False),
            ('uq_interact_semantic_dataset_version', ['dataset_id', 'version'], True),
        ],
    )
    _create(
        'interact_row_policy',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('dataset_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('principal_type', sa.Text(), nullable=False),
        sa.Column('principal_ids', sa.JSON(), nullable=False),
        sa.Column('expression', sa.JSON(), nullable=False),
        sa.Column('deny_if_unresolved', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_row_policy_company_user_id', ['company_user_id'], False),
            ('ix_interact_row_policy_dataset_id', ['dataset_id'], False),
        ],
    )
    _create(
        'interact_semantic_query_event',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('connector_id', sa.Text(), nullable=False),
        sa.Column('dataset_id', sa.Text()),
        sa.Column('dataset_version_id', sa.Text()),
        sa.Column('user_id', sa.Text()),
        sa.Column('company_member_id', sa.Text()),
        sa.Column('model_id', sa.Text()),
        sa.Column('channel_id', sa.Text()),
        sa.Column('workflow_id', sa.Text()),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('intent_summary', sa.Text()),
        sa.Column('plan_redacted', sa.JSON()),
        sa.Column('plan_fingerprint', sa.Text()),
        sa.Column('policy_decision', sa.JSON()),
        sa.Column('compiled_query_hash', sa.Text()),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer()),
        sa.Column('error_code', sa.Text()),
        sa.Column('error_detail', sa.Text()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_semantic_query_event_company_user_id', ['company_user_id'], False),
            ('ix_interact_semantic_query_event_connector_id', ['connector_id'], False),
            ('ix_interact_semantic_query_event_dataset_id', ['dataset_id'], False),
            ('ix_interact_semantic_query_event_request_id', ['request_id'], False),
        ],
    )
    _create(
        'interact_semantic_scan_schedule',
        sa.Column('connector_id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('next_scan_at', sa.BigInteger(), nullable=False),
        sa.Column('lease_until', sa.BigInteger(), nullable=False),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('last_started_at', sa.BigInteger()),
        sa.Column('last_completed_at', sa.BigInteger()),
        sa.Column('last_error', sa.Text()),
        indexes=[
            ('ix_interact_semantic_scan_schedule_company_user_id', ['company_user_id'], False),
            ('ix_interact_semantic_scan_schedule_next_scan_at', ['next_scan_at'], False),
        ],
    )
    _create(
        'interact_semantic_daily_quota',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('day_start', sa.BigInteger(), nullable=False),
        sa.Column('query_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_semantic_daily_quota_company_user_id', ['company_user_id'], False),
            ('uq_interact_semantic_daily_quota_company_day', ['company_user_id', 'day_start'], True),
        ],
    )
    _create(
        'interact_sso_ticket',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('company_user_id', sa.Text(), nullable=False),
        sa.Column('target_path', sa.Text(), nullable=False),
        sa.Column('return_url', sa.Text()),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.BigInteger()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        indexes=[
            ('ix_interact_sso_ticket_token_hash', ['token_hash'], True),
            ('ix_interact_sso_ticket_user_id', ['user_id'], False),
            ('ix_interact_sso_ticket_company_user_id', ['company_user_id'], False),
            ('ix_interact_sso_ticket_expires_at', ['expires_at'], False),
        ],
    )
    _create(
        'interact_semantic_plan_usage',
        sa.Column('company_user_id', sa.Text(), primary_key=True),
        sa.Column('dataset_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        'interact_semantic_plan_usage',
        'interact_sso_ticket',
        'interact_semantic_daily_quota',
        'interact_semantic_scan_schedule',
        'interact_semantic_query_event',
        'interact_row_policy',
        'interact_semantic_dataset_version',
        'interact_semantic_dataset',
        'interact_catalog_relationship',
        'interact_catalog_field',
        'interact_catalog_object',
        'interact_schema_snapshot',
    ):
        if table in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table)
