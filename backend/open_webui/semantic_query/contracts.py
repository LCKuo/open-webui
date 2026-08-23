from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FilterCondition(StrictModel):
    fieldId: str = Field(..., min_length=1, max_length=200)
    operator: Literal[
        'eq',
        'ne',
        'gt',
        'gte',
        'lt',
        'lte',
        'in',
        'not_in',
        'contains',
        'starts_with',
        'is_null',
        'is_not_null',
        'between',
    ]
    value: Any = None

    @model_validator(mode='after')
    def validate_value(self):
        if self.operator in {'is_null', 'is_not_null'}:
            return self
        if self.operator in {'in', 'not_in', 'between'} and not isinstance(self.value, list):
            raise ValueError(f'{self.operator} requires a list value')
        if self.operator == 'between' and len(self.value) != 2:
            raise ValueError('between requires exactly two values')
        if self.operator not in {'is_null', 'is_not_null'} and self.value is None:
            raise ValueError(f'{self.operator} requires a value')
        return self


class FilterGroup(StrictModel):
    operator: Literal['and', 'or'] = 'and'
    conditions: list[FilterCondition] = Field(default_factory=list, max_length=30)


class TimeRange(StrictModel):
    dimensionId: str = Field(..., min_length=1, max_length=200)
    preset: (
        Literal[
            'today',
            'yesterday',
            'this_week',
            'last_week',
            'this_month',
            'last_month',
            'this_quarter',
            'last_quarter',
            'this_year',
            'last_year',
        ]
        | None
    ) = None
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    timezone: str = Field(default='Asia/Taipei', max_length=80)

    @model_validator(mode='after')
    def validate_range(self):
        if not self.preset and not (self.start and self.end):
            raise ValueError('timeRange requires a preset or start/end')
        if self.start and self.end and self.start >= self.end:
            raise ValueError('timeRange start must be before end')
        return self


class OrderItem(StrictModel):
    fieldId: str = Field(..., min_length=1, max_length=200)
    direction: Literal['asc', 'desc'] = 'asc'


class QueryPlan(StrictModel):
    version: Literal['1'] = '1'
    datasetId: str = Field(..., min_length=1, max_length=200)
    datasetVersion: int | None = Field(default=None, ge=1)
    measures: list[str] = Field(default_factory=list, max_length=12)
    metrics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    filters: FilterGroup | None = None
    timeRange: TimeRange | None = None
    timeGrain: Literal['day', 'week', 'month', 'quarter', 'year'] | None = None
    orderBy: list[OrderItem] = Field(default_factory=list, max_length=8)
    limit: int = Field(default=20, ge=1, le=1000)
    includeTotals: bool = False
    intentSummary: str | None = Field(default=None, max_length=500)

    @model_validator(mode='before')
    @classmethod
    def normalize_common_tool_shapes(cls, value: Any):
        def parse_json_container(candidate: Any) -> Any:
            if not isinstance(candidate, str):
                return candidate
            raw = candidate.strip()
            if not raw or len(raw) > 20_000 or raw[0] not in {'[', '{'}:
                return candidate
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return candidate
            return parsed if isinstance(parsed, (list, dict)) else candidate

        def unwrap_item_container(candidate: Any) -> Any:
            """Normalize provider-generated array wrappers without relaxing the schema."""
            candidate = parse_json_container(candidate)
            for _ in range(3):
                if not isinstance(candidate, dict) or set(candidate) != {'item'}:
                    break
                candidate = parse_json_container(candidate['item'])
            return candidate

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        version = normalized.get('version')
        if version in {1, 1.0, '1.0'}:
            normalized['version'] = '1'
        for field in ('measures', 'metrics', 'dimensions', 'orderBy'):
            if field in normalized:
                raw_field = parse_json_container(normalized[field])
                normalized[field] = unwrap_item_container(raw_field)
                if (
                    field in {'measures', 'metrics', 'dimensions'}
                    and isinstance(normalized[field], str)
                    and isinstance(raw_field, dict)
                    and set(raw_field) == {'item'}
                ):
                    normalized[field] = [normalized[field]]

        order_by = normalized.get('orderBy')
        if isinstance(order_by, dict):
            normalized['orderBy'] = [order_by]
        elif isinstance(order_by, list):
            normalized['orderBy'] = [unwrap_item_container(item) for item in order_by]

        for item in normalized.get('orderBy') or []:
            if not isinstance(item, dict):
                continue
            direction = item.get('direction')
            if isinstance(direction, str):
                item['direction'] = direction.strip().lower()

        filters = unwrap_item_container(normalized.get('filters'))
        if isinstance(filters, list):
            filters = {'operator': 'and', 'conditions': filters}
        elif isinstance(filters, dict) and filters.get('fieldId'):
            filters = {'operator': 'and', 'conditions': [filters]}
        elif isinstance(filters, dict):
            filters = dict(filters)
            logic = filters.pop('logic', None)
            if 'operator' not in filters and logic is not None:
                filters['operator'] = logic

        if isinstance(filters, dict):
            aliases = {
                '=': 'eq',
                'equal': 'eq',
                'equals': 'eq',
                '!=': 'ne',
                '<>': 'ne',
                'not_equal': 'ne',
                'not_equals': 'ne',
                'greater_than': 'gt',
                'greater_than_or_equal': 'gte',
                'less_than': 'lt',
                'less_than_or_equal': 'lte',
                'one_of': 'in',
                'not_one_of': 'not_in',
                'includes': 'contains',
                'startswith': 'starts_with',
            }
            group_operator = filters.get('operator')
            if isinstance(group_operator, str) and group_operator.strip().lower() in {'and', 'or'}:
                filters['operator'] = group_operator.strip().lower()
            conditions = unwrap_item_container(filters.get('conditions'))
            if isinstance(conditions, dict) and conditions.get('fieldId'):
                conditions = [conditions]
            if isinstance(conditions, list):
                normalized_conditions = []
                for condition in conditions:
                    condition = unwrap_item_container(condition)
                    if not isinstance(condition, dict):
                        normalized_conditions.append(condition)
                        continue
                    condition = dict(condition)
                    operator = condition.get('operator')
                    if isinstance(operator, str):
                        token = operator.strip()
                        condition['operator'] = aliases.get(token, aliases.get(token.lower(), token.lower()))
                    if 'value' in condition:
                        condition['value'] = unwrap_item_container(condition['value'])
                    normalized_conditions.append(condition)
                filters['conditions'] = normalized_conditions
            normalized['filters'] = filters
        return normalized

    @model_validator(mode='after')
    def validate_selection(self):
        if not self.measures and not self.metrics and not self.dimensions:
            raise ValueError('at least one measure, metric, or dimension is required')
        if self.timeGrain and not self.timeRange:
            raise ValueError('timeGrain requires timeRange')
        return self


class QueryRuntimeContext(StrictModel):
    user_id: str | None = None
    user_role: str | None = None
    company_user_id: str
    company_member_id: str | None = None
    company_member_role: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    model_id: str | None = None
    channel_id: str | None = None
    channel_source: str | None = None
    workflow_id: str | None = None
    external_user_id: str | None = None
    request_id: str | None = None


class CompiledQuery(StrictModel):
    sql: str
    parameters: list[Any]
    selectedFields: list[dict[str, Any]]
    queryHash: str
    joins: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
