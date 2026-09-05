import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import open_webui.models.interact_email as email_models
import open_webui.routers.interact_email as email_router
import pytest
import pytest_asyncio
from fastapi import HTTPException
from open_webui.routers.interact_email import (
    EmailSendRequest,
    _apply_recipient_policy,
    _resend_error_response,
    ensure_email_connector_allowed,
    send_resend_email,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def email_table(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return
        async with sessions() as session:
            yield session

    monkeypatch.setattr(email_models, 'async_engine', engine)
    monkeypatch.setattr(email_models, 'get_async_db_context', session_context)
    monkeypatch.setattr(email_models, '_tables_ready', False)
    monkeypatch.setattr(email_models, '_encrypt_json', lambda value: json.dumps(value))
    monkeypatch.setattr(email_models, '_decrypt_json', lambda value: json.loads(value))
    table = email_models.InteractEmailTable()
    await table.ensure_tables()
    yield table
    await engine.dispose()


def delivery_values():
    return {
        'company_user_id': 'company-1',
        'connector_id': 'connector-1',
        'workflow_id': 'workflow-1',
        'workflow_run_id': 'run-1',
        'requested_by': 'member-1',
        'channel_id': 'line-1',
        'idempotency_key': 'workflow-run-1-send',
        'status': 'sending',
        'recipient_count': 1,
        'recipient_domains': ['example.com'],
        'to_encrypted': 'encrypted-to',
        'cc_encrypted': None,
        'subject_encrypted': 'encrypted-subject',
        'content_encrypted': 'encrypted-content',
        'payload_hash': 'a' * 64,
    }


@pytest.mark.asyncio
async def test_delivery_idempotency_returns_existing_without_provider_resend(email_table):
    first, first_created = await email_table.create_delivery(**delivery_values())
    duplicate, duplicate_created = await email_table.create_delivery(**delivery_values())

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.id == first.id


@pytest.mark.asyncio
async def test_delivery_daily_limit_is_reserved_atomically(email_table):
    first_values = delivery_values()
    await email_table.create_delivery(daily_limit=1, **first_values)
    second_values = {**first_values, 'idempotency_key': 'workflow-run-2-send'}

    with pytest.raises(email_models.EmailDailyLimitExceeded):
        await email_table.create_delivery(daily_limit=1, **second_values)


@pytest.mark.asyncio
async def test_delivery_list_can_be_restricted_to_campaign_workflows(email_table):
    await email_table.create_delivery(**delivery_values())
    await email_table.create_delivery(
        **{
            **delivery_values(),
            'workflow_id': 'workflow-2',
            'idempotency_key': 'workflow-run-2-send',
        }
    )

    deliveries = await email_table.list_deliveries(
        'company-1',
        workflow_ids={'workflow-1'},
    )

    assert [delivery.workflow_id for delivery in deliveries] == ['workflow-1']


@pytest.mark.asyncio
async def test_checkpoint_revision_advances_across_multiple_pauses(email_table):
    first = await email_table.save_checkpoint(
        workflow_run_id='run-1',
        company_user_id='company-1',
        workflow_id='workflow-1',
        node_id='choice',
        wait_type='input',
        state={'step': 1},
        prompt={'choices': []},
        payload_hash=None,
    )
    consumed = await email_table.consume_checkpoint(
        workflow_run_id='run-1',
        company_user_id='company-1',
        actor_id='member-1',
        decision='selected',
        expected_revision=first['revision'],
    )
    second = await email_table.save_checkpoint(
        workflow_run_id='run-1',
        company_user_id='company-1',
        workflow_id='workflow-1',
        node_id='approval',
        wait_type='approval',
        state={'step': 2},
        prompt={'preview': {}},
        payload_hash='b' * 64,
    )

    assert consumed is not None
    assert first['revision'] == 1
    assert second['revision'] == 2


def test_connector_rejects_email_header_injection():
    with pytest.raises(ValidationError):
        email_models.EmailConnectorUpsertForm(
            name='Customer mail',
            from_name='Support\r\nBcc: attacker@example.com',
            from_address='support@example.com',
        )


def test_connector_secrets_are_trimmed_and_selected_access_requires_targets():
    form = email_models.EmailConnectorUpsertForm(
        name='Customer mail',
        from_address='support@example.com',
        api_key='  re_test_key  ',
    )
    assert form.api_key == 're_test_key'

    with pytest.raises(ValidationError):
        email_models.EmailConnectorUpsertForm(
            name='Customer mail',
            from_address='support@example.com',
            access_mode='selected_groups',
        )


def test_connector_acl_checks_member_group_workflow_and_channel_together():
    connector = SimpleNamespace(
        company_user_id='company-1',
        enabled=True,
        status='ready',
        allowed_workflow_ids=['workflow-1'],
        allowed_channel_ids=['line-1'],
        access_mode='selected_groups',
        allowed_member_ids=[],
        allowed_group_ids=['sales'],
    )
    context = {
        'company_user_id': 'company-1',
        'company_member_id': 'member-1',
        'company_member_role': 'member',
        'group_ids': ['sales'],
    }

    ensure_email_connector_allowed(connector, context, 'workflow-1', 'line-1')
    with pytest.raises(HTTPException):
        ensure_email_connector_allowed(connector, context, 'workflow-1', 'line-2')


def test_crm_service_principal_bypasses_seat_acl_but_not_workflow_acl():
    connector = SimpleNamespace(
        company_user_id='company-1',
        enabled=True,
        status='ready',
        allowed_workflow_ids=['workflow-1'],
        allowed_channel_ids=['line-1'],
        access_mode='company_admins',
        allowed_member_ids=[],
        allowed_group_ids=[],
    )
    context = {
        'company_user_id': 'company-1',
        'company_member_id': None,
        'company_member_role': None,
        'group_ids': [],
        'service_principal': True,
    }

    ensure_email_connector_allowed(connector, context, 'workflow-1', None)
    with pytest.raises(HTTPException):
        ensure_email_connector_allowed(connector, context, 'workflow-2', None)


def test_trusted_crm_direct_delivery_bypasses_route_acl_only():
    connector = SimpleNamespace(
        company_user_id='company-1',
        enabled=True,
        status='ready',
        allowed_workflow_ids=['workflow-1'],
        allowed_channel_ids=['line-1'],
        access_mode='company_admins',
        allowed_member_ids=[],
        allowed_group_ids=[],
    )
    context = {
        'company_user_id': 'company-1',
        'company_member_id': 'crm-user-1',
        'company_member_role': 'member',
        'group_ids': [],
        'service_principal': True,
    }

    with pytest.raises(HTTPException):
        ensure_email_connector_allowed(connector, context, None, None)

    context['trusted_product_delivery'] = 'crm'
    ensure_email_connector_allowed(connector, context, None, None)

    context['company_user_id'] = 'company-2'
    with pytest.raises(HTTPException):
        ensure_email_connector_allowed(connector, context, None, None)


def test_quarantined_connector_is_rejected_before_other_acl_checks():
    connector = SimpleNamespace(
        company_user_id='company-1',
        control_plane_status='orphaned',
        enabled=True,
        status='ready',
    )
    context = {
        'company_user_id': 'company-1',
        'company_member_id': 'owner-1',
        'company_member_role': 'owner',
        'group_ids': [],
    }

    with pytest.raises(HTTPException) as exc:
        ensure_email_connector_allowed(
            connector,
            context,
            workflow_id=None,
            channel_id=None,
            allow_disabled=True,
        )

    assert exc.value.status_code == 409
    assert 'quarantined' in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_control_plane_revision_quarantine_and_adoption_preserve_secret(
    email_table,
):
    initial = email_models.EmailConnectorUpsertForm(
        id='connector-1',
        name='Customer mail',
        enabled=True,
        api_key='re_test_secret',
        from_address='support@example.com',
    )
    created = await email_table.upsert_connector(
        'company-1',
        'owner@example.com',
        'owner-1',
        initial,
        control_plane_revision=3,
        managed_by='company_portal',
    )
    assert created.has_api_key is True
    assert created.control_plane_revision == 3

    with pytest.raises(ValueError):
        await email_table.upsert_connector(
            'company-1',
            'owner@example.com',
            'owner-1',
            initial,
            control_plane_revision=2,
            managed_by='company_portal',
        )

    quarantined = await email_table.quarantine_connector(
        'connector-1',
        'company-1',
    )
    assert quarantined is not None
    assert quarantined.enabled is False
    assert quarantined.control_plane_status == 'orphaned'

    adopted = await email_table.upsert_connector(
        'company-1',
        'owner@example.com',
        'owner-1',
        email_models.EmailConnectorUpsertForm(
            id='connector-1',
            name='Customer mail',
            enabled=True,
            from_address='support@example.com',
        ),
        control_plane_revision=4,
        managed_by='company_portal',
    )
    assert adopted.has_api_key is True
    assert adopted.control_plane_status == 'active'
    assert adopted.control_plane_revision == 4


def test_recipient_policy_accepts_legacy_combined_domain_values():
    connector = SimpleNamespace(
        recipient_policy={
            'allowed_domains': ['customer.com、partner.com'],
            'blocked_domains': [],
        },
        cc_policy={
            'allow_runtime_cc': True,
            'default_cc': ['asdfg6311@gmail.com'],
            'allowed_domains': ['gmail.com、interact-vision.com.tw'],
            'max_cc': 10,
        },
        max_recipients_per_send=20,
    )

    to, cc = _apply_recipient_policy(connector, ['buyer@customer.com'], [])

    assert to == ['buyer@customer.com']
    assert cc == ['asdfg6311@gmail.com']


def test_resend_unverified_domain_error_is_actionable_and_not_a_gateway_error():
    status, detail = _resend_error_response(
        403,
        'validation_error',
        'The interact-vision.com.tw domain is not verified. Please add and verify it.',
    )

    assert status == 422
    assert 'interact-vision.com.tw' in detail
    assert 'DNS 驗證' in detail


def test_resend_server_error_is_reported_as_service_unavailable():
    status, detail = _resend_error_response(502, 'application_error', '<html>Bad gateway</html>')

    assert status == 503
    assert detail == 'Resend 服務目前暫時無法完成寄送，請稍後再試。'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload_reply_to', 'expected_reply_to', 'existing_status'),
    [
        ('sales-owner@example.com', 'sales-owner@example.com', None),
        (None, 'service@example.com', None),
        ('sales-owner@example.com', 'sales-owner@example.com', 'failed'),
    ],
)
async def test_campaign_delivery_reaches_provider_and_records_sent_status(
    monkeypatch,
    payload_reply_to,
    expected_reply_to,
    existing_status,
):
    connector = SimpleNamespace(
        id='connector-1',
        company_user_id='company-1',
        enabled=True,
        status='ready',
        control_plane_status='active',
        allowed_workflow_ids=['workflow-1'],
        allowed_channel_ids=[],
        access_mode='company_admins',
        allowed_member_ids=[],
        allowed_group_ids=[],
        recipient_policy={'allowed_domains': [], 'blocked_domains': []},
        cc_policy={'default_cc': [], 'allowed_domains': [], 'max_cc': 10},
        max_recipients_per_send=20,
        daily_send_limit=100,
        company_email='service@example.com',
        from_name='Chengsyin Team',
        from_address='noreply@example.com',
        reply_to='owner@example.com',
    )
    delivery = SimpleNamespace(
        id='delivery-1',
        company_user_id='company-1',
        payload_hash='a' * 64,
        status=existing_status or 'sending',
        provider_message_id=None,
    )
    recorded = {}

    async def get_existing_delivery(_key):
        return delivery if existing_status else None

    async def create_delivery(**values):
        recorded['created'] = values
        return delivery, True

    async def update_delivery(_delivery_id, **values):
        recorded['updated'] = values
        for key, value in values.items():
            setattr(delivery, key, value)
        return delivery

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def text(self):
            return json.dumps({'id': 'resend-message-1'})

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            recorded['provider_url'] = url
            recorded['provider_request'] = kwargs
            return FakeResponse()

    monkeypatch.setattr(email_router.InteractEmail, 'decrypt_connector_api_key', lambda _connector: 're_test_key')
    monkeypatch.setattr(email_router.InteractEmail, 'get_delivery_by_idempotency_key', get_existing_delivery)
    monkeypatch.setattr(email_router.InteractEmail, 'create_delivery', create_delivery)
    monkeypatch.setattr(email_router.InteractEmail, 'update_delivery', update_delivery)
    monkeypatch.setattr(email_router, 'is_billing_enabled', lambda: False)
    monkeypatch.setattr(email_router, '_encrypt', lambda value: f'encrypted:{value}')
    monkeypatch.setattr(email_router.aiohttp, 'ClientSession', FakeSession)

    result = await send_resend_email(
        connector,
        {'company_user_id': 'company-1', 'service_principal': True},
        EmailSendRequest(
            connector_id='connector-1',
            to=['buyer@example.com'],
            subject='Campaign validation',
            text='This is a controlled campaign delivery test.',
            reply_to=payload_reply_to,
            workflow_id='workflow-1',
            workflow_run_id='run-1',
            idempotency_key='campaign-validation-1',
            payload_hash='a' * 64,
        ),
    )

    assert recorded['provider_url'].endswith('/emails')
    assert recorded['provider_request']['json']['to'] == ['buyer@example.com']
    assert recorded['provider_request']['json']['reply_to'] == expected_reply_to
    if existing_status:
        assert 'created' not in recorded
    else:
        assert recorded['created']['workflow_id'] == 'workflow-1'
        assert recorded['created']['from_address'] == 'noreply@example.com'
        assert recorded['created']['reply_to'] == expected_reply_to
    assert result.status == 'sent'
    assert result.provider_message_id == 'resend-message-1'


@pytest.mark.asyncio
async def test_email_events_are_idempotent(email_table):
    values = {
        'company_user_id': 'company-1',
        'delivery_id': None,
        'provider_event_id': 'evt-1',
        'provider_message_id': 'email-1',
        'event_type': 'email.delivered',
        'occurred_at': 1,
        'payload': {'type': 'email.delivered'},
    }

    assert await email_table.insert_event(**values) is True
    assert await email_table.insert_event(**values) is False
