from open_webui.utils.interact_crm_auth import (
    decode_crm_access_token,
    issue_crm_access_token,
)


def test_crm_access_token_signs_employee_role_and_team_grants():
    token, claims = issue_crm_access_token(
        company_user_id='company-a',
        company_email='owner@example.com',
        webui_user_id='webui-owner',
        crm_integration_id='integration-a',
        crm_instance_id='instance-a',
        crm_instance_name='CRM A',
        crm_user_id='42',
        crm_user_email='employee@example.com',
        crm_user_role='sales',
        product_team_codes=['bd', 'bd', 'invalid'],
        scopes=['agent:select'],
        allowed_workflow_ids=[],
        ttl_seconds=300,
    )

    decoded = decode_crm_access_token(token)
    assert claims['crm_user_id'] == '42'
    assert decoded['crm_user_email'] == 'employee@example.com'
    assert decoded['crm_user_role'] == 'sales'
    assert decoded['product_team_codes'] == ['bd']
