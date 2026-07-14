from __future__ import annotations

import datetime as dt
import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from open_webui.semantic_query.contracts import CompiledQuery, FilterCondition, QueryPlan
from open_webui.semantic_query.errors import SemanticQueryError


@dataclass
class Dialect:
    name: str

    def quote(self, value: str) -> str:
        if self.name == 'mysql':
            return '`' + value.replace('`', '``') + '`'
        if self.name == 'mssql':
            return '[' + value.replace(']', ']]') + ']'
        return '"' + value.replace('"', '""') + '"'

    def table(self, physical: str) -> str:
        return '.'.join(self.quote(part) for part in physical.split('.'))

    @property
    def placeholder(self) -> str:
        return '%s' if self.name in {'postgres', 'mysql'} else '?'

    def limit(self, limit: int) -> tuple[str, str]:
        if self.name == 'mssql':
            return f'TOP {limit} ', ''
        return '', f' LIMIT {limit}'

    def time_bucket(self, expression: str, grain: str) -> str:
        if self.name == 'postgres':
            return f"DATE_TRUNC('{grain}', {expression})"
        if self.name == 'mysql':
            formats = {
                'day': '%%Y-%%m-%%d',
                'week': '%%x-%%v',
                'month': '%%Y-%%m-01',
                'year': '%%Y-01-01',
            }
            if grain == 'quarter':
                return f"CONCAT(YEAR({expression}), '-Q', QUARTER({expression}))"
            return f"DATE_FORMAT({expression}, '{formats[grain]}')"
        if self.name == 'mssql':
            if grain == 'day':
                return f'CAST({expression} AS date)'
            if grain == 'week':
                return f'DATEADD(week, DATEDIFF(week, 0, {expression}), 0)'
            if grain == 'month':
                return f'DATEFROMPARTS(YEAR({expression}), MONTH({expression}), 1)'
            if grain == 'quarter':
                return f'DATEFROMPARTS(YEAR({expression}), ((DATEPART(quarter, {expression})-1)*3)+1, 1)'
            return f'DATEFROMPARTS(YEAR({expression}), 1, 1)'
        formats = {'day': '%Y-%m-%d', 'week': '%Y-%W', 'month': '%Y-%m-01', 'quarter': '%Y', 'year': '%Y-01-01'}
        if grain == 'quarter':
            return (
                f"printf('%04d-Q%d', CAST(strftime('%Y', {expression}) AS int), "
                f"((CAST(strftime('%m', {expression}) AS int)-1)/3)+1)"
            )
        return f"strftime('{formats[grain]}', {expression})"


class SemanticCompiler:
    def __init__(self, connector_type: str, catalog: dict[str, Any], definition: dict[str, Any]):
        normalized = (
            'postgres'
            if connector_type in {'postgres', 'postgresql'}
            else 'mysql'
            if connector_type in {'mysql', 'mariadb'}
            else connector_type
        )
        self.dialect = Dialect(normalized)
        self.definition = definition
        self.objects = {item['id']: item for item in catalog.get('objects') or []}
        self.fields = {
            field['id']: {**field, 'object_id': obj['id'], 'object': obj}
            for obj in catalog.get('objects') or []
            for field in obj.get('fields') or []
        }
        self.relationships = {
            item['id']: item for item in catalog.get('relationships') or [] if item.get('status') == 'confirmed'
        }
        self.dimensions = {item['id']: item for item in definition.get('dimensions') or []}
        self.measures = {item['id']: item for item in definition.get('measures') or []}
        self.metrics = {item['id']: item for item in definition.get('metrics') or []}
        self.relationship_ids = set(definition.get('relationshipIds') or [])
        self.parameters: list[Any] = []

    def compile(  # noqa: C901
        self, plan: QueryPlan, row_filters: list[dict[str, Any]] | None = None
    ) -> CompiledQuery:
        self.parameters = []
        selected_measures = [self._measure(item) for item in plan.measures]
        metric_measures = [
            self._measure(measure_id)
            for metric_id in plan.metrics
            for measure_id in self._metric_measure_ids(self._metric(metric_id))
        ]
        measure_fields = [
            self._field(item['fieldId'], aggregatable=True) for item in [*selected_measures, *metric_measures]
        ]
        anchors = {field['object_id'] for field in measure_fields}
        if len(anchors) > 1:
            raise SemanticQueryError(
                'QUERY-FANOUT-RISK',
                'Measures from different grains require separate published datasets or a pre-aggregated source.',
                admin_action='將不同 grain 的 measure 拆成資料集，或建立已驗證的彙總 view。',
            )
        anchor_id = next(iter(anchors), self._root_object_id())
        if anchor_id not in self.objects:
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', 'Dataset root object is missing.')

        requested_dimensions = [self._dimension(item) for item in plan.dimensions]
        dimension_fields = [self._field(item['fieldId'], groupable=True) for item in requested_dimensions]
        time_field = None
        if plan.timeRange:
            time_dimension = self._dimension(plan.timeRange.dimensionId)
            time_field = self._field(time_dimension['fieldId'], filterable=True)

        filter_conditions = list(plan.filters.conditions if plan.filters else [])
        policy_conditions = [FilterCondition.model_validate(item) for item in row_filters or []]
        filter_fields = [self._semantic_field(item.fieldId, filterable=True) for item in filter_conditions]
        filter_fields.extend(self._policy_field(item.fieldId) for item in policy_conditions)
        required_objects = {
            field['object_id'] for field in [*dimension_fields, *filter_fields, *([time_field] if time_field else [])]
        }
        joins, aliases = self._joins(anchor_id, required_objects)

        selected_sql: list[str] = []
        selected_fields: list[dict[str, Any]] = []
        group_sql: list[str] = []
        expression_by_id: dict[str, str] = {}

        for dimension, field in zip(requested_dimensions, dimension_fields):
            expression = self._column(field, aliases)
            if plan.timeGrain and plan.timeRange and dimension['id'] == plan.timeRange.dimensionId:
                expression = self.dialect.time_bucket(expression, plan.timeGrain)
            alias = dimension['id']
            selected_sql.append(f'{expression} AS {self.dialect.quote(alias)}')
            group_sql.append(expression)
            expression_by_id[alias] = expression
            selected_fields.append(
                {'id': alias, 'label': dimension.get('name') or alias, 'type': field.get('semantic_type')}
            )

        measure_expression_by_id: dict[str, str] = {}
        for measure in {item['id']: item for item in [*selected_measures, *metric_measures]}.values():
            field = self._field(measure['fieldId'], aggregatable=True)
            column = self._column(field, aliases)
            aggregation = str(measure.get('aggregation') or field.get('default_aggregation') or '').lower()
            if aggregation not in {'sum', 'count', 'count_distinct', 'avg', 'min', 'max'}:
                raise SemanticQueryError('QUERY-PLAN-INVALID', f'Unsupported aggregation: {aggregation}')
            expression = (
                f'COUNT(DISTINCT {column})' if aggregation == 'count_distinct' else f'{aggregation.upper()}({column})'
            )
            internal_filters = measure.get('filters') or []
            if internal_filters:
                predicate = self._conditions(
                    [FilterCondition.model_validate(item) for item in internal_filters], aliases, 'and'
                )
                if aggregation in {'sum', 'avg', 'min', 'max'}:
                    inner = column if aggregation != 'count' else '1'
                    expression = f'{aggregation.upper()}(CASE WHEN {predicate} THEN {inner} ELSE NULL END)'
                elif aggregation == 'count':
                    expression = f'SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END)'
                else:
                    expression = f'COUNT(DISTINCT CASE WHEN {predicate} THEN {column} ELSE NULL END)'
            measure_expression_by_id[measure['id']] = expression
            expression_by_id[measure['id']] = expression

        for measure in selected_measures:
            expression = measure_expression_by_id[measure['id']]
            selected_sql.append(f'{expression} AS {self.dialect.quote(measure["id"])}')
            field = self._field(measure['fieldId'])
            selected_fields.append(
                {'id': measure['id'], 'label': measure.get('name') or measure['id'], 'type': field.get('semantic_type')}
            )

        for metric_id in plan.metrics:
            metric = self._metric(metric_id)
            expression = self._metric_expression(metric.get('expression') or {}, measure_expression_by_id)
            selected_sql.append(f'{expression} AS {self.dialect.quote(metric_id)}')
            expression_by_id[metric_id] = expression
            selected_fields.append(
                {
                    'id': metric_id,
                    'label': metric.get('name') or metric_id,
                    'type': metric.get('semanticType', 'number'),
                }
            )

        if not selected_sql:
            raise SemanticQueryError('QUERY-PLAN-INVALID', 'Query has no selected output fields.')
        where_parts = []
        if filter_conditions:
            where_parts.append(
                self._conditions(filter_conditions, aliases, plan.filters.operator if plan.filters else 'and')
            )
        if policy_conditions:
            # Row policies are mandatory and cannot inherit a caller-controlled OR.
            where_parts.append(self._conditions(policy_conditions, aliases, 'and', policy=True))
        if time_field and plan.timeRange:
            start, end = resolve_time_range(plan.timeRange)
            column = self._column(time_field, aliases)
            where_parts.append(f'{column} >= {self._param(start)} AND {column} < {self._param(end)}')

        order_parts = []
        for item in plan.orderBy:
            if item.fieldId not in expression_by_id:
                raise SemanticQueryError('QUERY-PLAN-INVALID', f'orderBy field is not selected: {item.fieldId}')
            order_parts.append(f'{self.dialect.quote(item.fieldId)} {item.direction.upper()}')
        top, suffix = self.dialect.limit(plan.limit)
        anchor_alias = aliases[anchor_id]
        sql = (
            f'SELECT {top}{", ".join(selected_sql)} '
            f'FROM {self.dialect.table(self.objects[anchor_id]["physical_name"])} {anchor_alias}'
            + ''.join(joins)
            + (f' WHERE {" AND ".join(where_parts)}' if where_parts else '')
            + (f' GROUP BY {", ".join(group_sql)}' if group_sql else '')
            + (f' ORDER BY {", ".join(order_parts)}' if order_parts else '')
            + suffix
        )
        if ';' in sql:
            raise SemanticQueryError('QUERY-COMPILATION-FAILED', 'Multiple statements are not allowed.')
        query_hash = hashlib.sha256(sql.encode()).hexdigest()
        return CompiledQuery(
            sql=sql,
            parameters=self.parameters,
            selectedFields=selected_fields,
            queryHash=query_hash,
            joins=[relationship['id'] for relationship in self._join_relationships(anchor_id, required_objects)],
        )

    def _root_object_id(self) -> str:
        return str(self.definition.get('rootObjectId') or '')

    def _field(
        self,
        field_id: str,
        *,
        readable: bool = True,
        filterable: bool = False,
        groupable: bool = False,
        aggregatable: bool = False,
    ):
        field = self.fields.get(str(field_id))
        if not field or (readable and not field.get('readable')):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Field is not readable: {field_id}')
        if filterable and not field.get('filterable'):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Field is not filterable: {field_id}')
        if groupable and not field.get('groupable'):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Field is not groupable: {field_id}')
        if aggregatable and not field.get('aggregatable'):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Field is not aggregatable: {field_id}')
        if not field['object'].get('enabled'):
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', 'Field belongs to a disabled catalog object.')
        return field

    def _dimension(self, semantic_id: str):
        item = self.dimensions.get(semantic_id)
        if not item:
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Unknown dimension: {semantic_id}')
        return item

    def _measure(self, semantic_id: str):
        item = self.measures.get(semantic_id)
        if not item:
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Unknown measure: {semantic_id}')
        return item

    def _metric(self, semantic_id: str):
        item = self.metrics.get(semantic_id)
        if not item:
            raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Unknown metric: {semantic_id}')
        return item

    def _semantic_field(self, semantic_id: str, **requirements):
        if semantic_id in self.dimensions:
            return self._field(self.dimensions[semantic_id]['fieldId'], **requirements)
        if semantic_id in self.measures:
            return self._field(self.measures[semantic_id]['fieldId'], **requirements)
        raise SemanticQueryError('SEMANTIC-FIELD-NOT-ALLOWED', f'Unknown semantic field: {semantic_id}')

    def _policy_field(self, field_id: str):
        if field_id in self.dimensions or field_id in self.measures:
            return self._semantic_field(field_id, filterable=True)
        return self._field(field_id, filterable=True)

    def _graph(self):
        graph: dict[str, list[tuple[str, dict[str, Any], bool]]] = {}
        for relationship_id in self.relationship_ids:
            rel = self.relationships.get(relationship_id)
            if not rel:
                continue
            graph.setdefault(rel['left_object_id'], []).append((rel['right_object_id'], rel, True))
            graph.setdefault(rel['right_object_id'], []).append((rel['left_object_id'], rel, False))
        return graph

    def _find_path(self, start: str, target: str):
        if start == target:
            return []
        graph = self._graph()
        queue = deque([(start, [])])
        found = []
        visited_depth: dict[str, int] = {start: 0}
        while queue:
            node, path = queue.popleft()
            if len(path) > 5:
                continue
            for next_node, relationship, forward in graph.get(node, []):
                next_path = [*path, (next_node, relationship, forward)]
                if next_node == target:
                    found.append(next_path)
                    continue
                if visited_depth.get(next_node, 99) >= len(next_path):
                    visited_depth[next_node] = len(next_path)
                    queue.append((next_node, next_path))
        if not found:
            raise SemanticQueryError('SEMANTIC-RELATIONSHIP-MISSING', f'No relationship path from {start} to {target}.')
        shortest = min(len(path) for path in found)
        candidates = [path for path in found if len(path) == shortest]
        signatures = {tuple(step[1]['id'] for step in path) for path in candidates}
        if len(signatures) > 1:
            raise SemanticQueryError('SEMANTIC-RELATIONSHIP-AMBIGUOUS')
        return candidates[0]

    def _join_relationships(self, anchor: str, targets: set[str]):
        result = []
        seen = set()
        for target in targets:
            for _next, relationship, _forward in self._find_path(anchor, target):
                if relationship['id'] not in seen:
                    seen.add(relationship['id'])
                    result.append(relationship)
        return result

    def _joins(self, anchor: str, targets: set[str]):
        aliases = {anchor: 't0'}
        joins = []
        joined = {anchor}
        alias_index = 1
        for target in targets:
            for next_object, relationship, forward in self._find_path(anchor, target):
                if self._traversal_fans_out(relationship, forward):
                    raise SemanticQueryError(
                        'QUERY-FANOUT-RISK',
                        f'Relationship {relationship["id"]} expands the measure grain.',
                    )
                if next_object not in aliases:
                    aliases[next_object] = f't{alias_index}'
                    alias_index += 1
                if next_object not in joined:
                    conditions = []
                    for pair in relationship.get('join_pairs') or []:
                        left = self._field(pair['leftFieldId'])
                        right = self._field(pair['rightFieldId'])
                        conditions.append(f'{self._column(left, aliases)} = {self._column(right, aliases)}')
                    if not conditions:
                        raise SemanticQueryError('SEMANTIC-RELATIONSHIP-MISSING', 'Relationship has no join fields.')
                    join_kind = 'LEFT JOIN' if relationship.get('join_type') == 'left' else 'INNER JOIN'
                    joins.append(
                        f' {join_kind} {self.dialect.table(self.objects[next_object]["physical_name"])} '
                        f'{aliases[next_object]} ON {" AND ".join(conditions)}'
                    )
                    joined.add(next_object)
        return joins, aliases

    @staticmethod
    def _traversal_fans_out(relationship: dict[str, Any], forward: bool) -> bool:
        kind = relationship.get('relationship_type')
        if kind == 'many_to_many':
            return True
        if forward:
            return kind == 'one_to_many'
        return kind == 'many_to_one'

    def _column(self, field: dict[str, Any], aliases: dict[str, str]) -> str:
        alias = aliases.get(field['object_id'])
        if not alias:
            raise SemanticQueryError('QUERY-COMPILATION-FAILED', 'Field object was not joined.')
        return f'{alias}.{self.dialect.quote(field["physical_name"])}'

    def _param(self, value: Any) -> str:
        self.parameters.append(value)
        return self.dialect.placeholder

    def _conditions(
        self,
        conditions: list[FilterCondition],
        aliases: dict[str, str],
        operator: str,
        *,
        policy: bool = False,
    ):
        sql = [self._condition(item, aliases, policy=policy) for item in conditions]
        return '(' + f' {operator.upper()} '.join(sql) + ')'

    def _condition(self, condition: FilterCondition, aliases: dict[str, str], *, policy: bool = False) -> str:
        field = (
            self._policy_field(condition.fieldId)
            if policy
            else self._semantic_field(condition.fieldId, filterable=True)
        )
        column = self._column(field, aliases)
        op = condition.operator
        if op == 'is_null':
            return f'{column} IS NULL'
        if op == 'is_not_null':
            return f'{column} IS NOT NULL'
        if op in {'in', 'not_in'}:
            if not condition.value or len(condition.value) > 100:
                raise SemanticQueryError('QUERY-COST-LIMIT', 'IN filters require 1 to 100 values.')
            for value in condition.value:
                self._validate_value_type(field, value, op)
            placeholders = ', '.join(self._param(item) for item in condition.value)
            return f'{column} {"NOT IN" if op == "not_in" else "IN"} ({placeholders})'
        if op == 'between':
            for value in condition.value:
                self._validate_value_type(field, value, op)
            return f'{column} BETWEEN {self._param(condition.value[0])} AND {self._param(condition.value[1])}'
        if op in {'contains', 'starts_with'}:
            if field.get('semantic_type') not in {'string', 'pii', 'identifier'}:
                raise SemanticQueryError('QUERY-TYPE-MISMATCH', f'{op} requires a text field.')
            value = f'%{condition.value}%' if op == 'contains' else f'{condition.value}%'
            return f'{column} LIKE {self._param(value)}'
        self._validate_value_type(field, condition.value, op)
        operators = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
        return f'{column} {operators[op]} {self._param(condition.value)}'

    @staticmethod
    def _validate_value_type(field: dict[str, Any], value: Any, operator: str) -> None:
        semantic_type = field.get('semantic_type')
        if value is None:
            return
        if semantic_type in {'number', 'money'} and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise SemanticQueryError('QUERY-TYPE-MISMATCH', f'{operator} requires a numeric value.')
        if semantic_type == 'boolean' and not isinstance(value, bool):
            raise SemanticQueryError('QUERY-TYPE-MISMATCH', f'{operator} requires a boolean value.')

    @staticmethod
    def _metric_measure_ids(metric: dict[str, Any]) -> list[str]:
        expression = metric.get('expression') or {}
        result = []
        for key in ('measureId', 'leftMeasureId', 'rightMeasureId'):
            if expression.get(key):
                result.append(expression[key])
        return result

    def _metric_expression(self, expression: dict[str, Any], measures: dict[str, str]) -> str:
        operator = expression.get('operator')
        if operator == 'measure':
            return measures[self._measure(str(expression.get('measureId')))['id']]
        left_id = str(expression.get('leftMeasureId') or '')
        right_id = str(expression.get('rightMeasureId') or '')
        if left_id not in measures or right_id not in measures:
            raise SemanticQueryError('QUERY-PLAN-INVALID', 'Metric references an unavailable measure.')
        left, right = measures[left_id], measures[right_id]
        if operator == 'divide':
            return f'({left} / NULLIF({right}, 0))'
        if operator == 'multiply':
            return f'({left} * {right})'
        if operator == 'add':
            return f'({left} + {right})'
        if operator == 'subtract':
            return f'({left} - {right})'
        raise SemanticQueryError('QUERY-PLAN-INVALID', f'Unsupported metric operator: {operator}')


def resolve_time_range(time_range):  # noqa: C901
    if time_range.start and time_range.end:
        return time_range.start, time_range.end
    try:
        timezone = ZoneInfo(time_range.timezone)
    except Exception as exc:
        raise SemanticQueryError('QUERY-PLAN-INVALID', 'Unknown timezone.', field_path='timeRange.timezone') from exc
    now = dt.datetime.now(timezone)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    preset = time_range.preset
    if preset == 'today':
        return day, day + dt.timedelta(days=1)
    if preset == 'yesterday':
        return day - dt.timedelta(days=1), day
    week = day - dt.timedelta(days=day.weekday())
    if preset == 'this_week':
        return week, week + dt.timedelta(days=7)
    if preset == 'last_week':
        return week - dt.timedelta(days=7), week
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    previous_month = (month - dt.timedelta(days=1)).replace(day=1)
    if preset == 'this_month':
        return month, next_month
    if preset == 'last_month':
        return previous_month, month
    quarter_month = ((day.month - 1) // 3) * 3 + 1
    quarter = day.replace(month=quarter_month, day=1)
    next_quarter = (
        quarter.replace(year=quarter.year + 1, month=1)
        if quarter.month == 10
        else quarter.replace(month=quarter.month + 3)
    )
    previous_quarter = (
        quarter.replace(year=quarter.year - 1, month=10)
        if quarter.month == 1
        else quarter.replace(month=quarter.month - 3)
    )
    if preset == 'this_quarter':
        return quarter, next_quarter
    if preset == 'last_quarter':
        return previous_quarter, quarter
    year = day.replace(month=1, day=1)
    if preset == 'this_year':
        return year, year.replace(year=year.year + 1)
    if preset == 'last_year':
        return year.replace(year=year.year - 1), year
    raise SemanticQueryError('QUERY-PLAN-INVALID', 'Unsupported time preset.')
