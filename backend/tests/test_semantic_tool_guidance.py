import json

import pytest

from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.tools.interact_semantic import _error_result


@pytest.mark.parametrize('code,has_schema', [
    ('QUERY-PLAN-INVALID', True),
    ('QUERY-TYPE-MISMATCH', True),
    ('DB-AUTHENTICATION-FAILED', False),
    ('SEMANTIC-DATASET-NOT-ALLOWED', False),
])
def test_tool_guidance_exposes_contract_not_private_error_details(code, has_schema):
    result = json.loads(_error_result(SemanticQueryError(code, 'private-connection-detail')))
    assert result['ok'] is False
    assert result['error']['code'] == code
    assert 'private-connection-detail' not in json.dumps(result)
    assert ('queryPlanSchema' in result) is has_schema
    if has_schema:
        schema = result['queryPlanSchema']
        assert schema['additionalProperties'] is False
        assert 'groupBy' not in schema['properties']
        assert schema['properties']['dimensions']['maxItems'] == 8
        assert 'JSON true/false' in result['repairHint']
