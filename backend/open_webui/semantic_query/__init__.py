from open_webui.semantic_query.contracts import QueryPlan
from open_webui.semantic_query.service import execute_query, validate_dataset_definition

execute_semantic_query = execute_query

__all__ = ['QueryPlan', 'execute_semantic_query', 'validate_dataset_definition']
