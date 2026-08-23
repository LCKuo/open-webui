from open_webui.models.models import _model_product_affinity


def test_explicit_product_affinity_is_normalized_and_deduplicated():
    assert _model_product_affinity(
        {
            'productKeys': [' CRM ', 'erp', 'crm'],
            'productRoles': ['AM', 'am'],
        }
    ) == (['crm', 'erp'], ['am'])


def test_managed_bd_agent_is_classified_as_crm_bd():
    assert _model_product_affinity(
        {'managedUseCases': ['prospecting_discovery']}
    ) == (['crm'], ['bd'])


def test_managed_am_agent_is_classified_as_crm_am():
    assert _model_product_affinity(
        {'managedUseCases': ['customer_growth', 'account_management']}
    ) == (['crm'], ['am'])


def test_unassigned_agent_remains_general():
    assert _model_product_affinity({'managedUseCases': []}) == ([], [])
