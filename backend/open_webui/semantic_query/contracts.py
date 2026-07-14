from __future__ import annotations

import datetime as dt
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
