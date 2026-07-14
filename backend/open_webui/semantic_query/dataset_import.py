from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from open_webui.semantic_query.service import validate_dataset_definition


class PortableModel(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class PortableFieldRef(PortableModel):
    object_name: str = Field(..., alias='object', min_length=1, max_length=300)
    field_name: str = Field(..., alias='field', min_length=1, max_length=200)


class PortableFilter(PortableModel):
    field_ref: PortableFieldRef = Field(..., alias='field')
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
        if self.value is None:
            raise ValueError(f'{self.operator} requires a value')
        return self


class PortableDimension(PortableModel):
    id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    field_ref: PortableFieldRef = Field(..., alias='field')


class PortableMeasure(PortableModel):
    id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    field_ref: PortableFieldRef = Field(..., alias='field')
    aggregation: Literal['sum', 'count', 'count_distinct', 'avg', 'min', 'max']
    filters: list[PortableFilter] = Field(default_factory=list, max_length=20)


class PortableMetricExpression(PortableModel):
    operator: Literal['measure', 'divide', 'multiply', 'add', 'subtract']
    measure_id: str | None = Field(default=None, alias='measureId', max_length=200)
    left_measure_id: str | None = Field(default=None, alias='leftMeasureId', max_length=200)
    right_measure_id: str | None = Field(default=None, alias='rightMeasureId', max_length=200)

    @model_validator(mode='after')
    def validate_operands(self):
        if self.operator == 'measure' and not self.measure_id:
            raise ValueError('measure operator requires measureId')
        if self.operator != 'measure' and not (self.left_measure_id and self.right_measure_id):
            raise ValueError(f'{self.operator} requires leftMeasureId and rightMeasureId')
        return self


class PortableMetric(PortableModel):
    id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    semantic_type: str = Field(default='number', alias='semanticType', max_length=80)
    expression: PortableMetricExpression


class PortableJoinPair(PortableModel):
    left_field: str = Field(..., alias='leftField', min_length=1, max_length=200)
    right_field: str = Field(..., alias='rightField', min_length=1, max_length=200)


class PortableRelationship(PortableModel):
    left_object: str = Field(..., alias='leftObject', min_length=1, max_length=300)
    right_object: str = Field(..., alias='rightObject', min_length=1, max_length=300)
    join_pairs: list[PortableJoinPair] = Field(..., alias='joinPairs', min_length=1, max_length=8)


class PortableSemanticModel(PortableModel):
    root_object: str = Field(..., alias='rootObject', min_length=1, max_length=300)
    when_to_use: str = Field(default='', alias='whenToUse', max_length=2000)
    not_for: list[str] = Field(default_factory=list, alias='notFor', max_length=100)
    examples: list[str] = Field(default_factory=list, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=100)
    default_time_dimension: str | None = Field(default=None, alias='defaultTimeDimension', max_length=200)
    dimensions: list[PortableDimension] = Field(default_factory=list, max_length=100)
    measures: list[PortableMeasure] = Field(default_factory=list, max_length=100)
    metrics: list[PortableMetric] = Field(default_factory=list, max_length=100)
    relationships: list[PortableRelationship] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def require_semantic_fields(self):
        if not self.dimensions and not self.measures:
            raise ValueError('model requires at least one dimension or measure')
        return self


class PortableAccess(PortableModel):
    mode: Literal[
        'company_admins',
        'selected_members',
        'all_company_members',
        'selected_channels',
    ] = 'company_admins'
    member_ids: list[str] = Field(default_factory=list, alias='memberIds', max_length=500)
    group_ids: list[str] = Field(default_factory=list, alias='groupIds', max_length=500)
    model_ids: list[str] = Field(default_factory=list, alias='modelIds', max_length=200)
    channel_ids: list[str] = Field(default_factory=list, alias='channelIds', max_length=200)
    workflow_ids: list[str] = Field(default_factory=list, alias='workflowIds', max_length=200)


class PortablePermissionTarget(PortableModel):
    object_name: str = Field(..., alias='object', min_length=1, max_length=300)
    field_name: str | None = Field(default=None, alias='field', min_length=1, max_length=200)


class PortablePermissionRecommendation(PortableModel):
    target: PortablePermissionTarget
    permission: Literal['enabled', 'readable', 'filterable', 'groupable', 'aggregatable']
    action: Literal['grant', 'revoke']
    reason: str = Field(..., min_length=8, max_length=1000)
    required_for: list[str] = Field(default_factory=list, alias='requiredFor', max_length=50)

    @model_validator(mode='after')
    def validate_target(self):
        if self.permission == 'enabled' and self.target.field_name:
            raise ValueError('enabled is an object permission and must not include target.field')
        if self.permission != 'enabled' and not self.target.field_name:
            raise ValueError(f'{self.permission} is a field permission and requires target.field')
        return self


class PortableDataset(PortableModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=120, pattern=r'^[a-z0-9][a-z0-9_-]*$')
    description: str = Field(default='', max_length=2000)
    business_domain: str | None = Field(default=None, alias='businessDomain', max_length=200)
    access: PortableAccess = Field(default_factory=PortableAccess)
    model: PortableSemanticModel


class PortableDatasetDocument(PortableModel):
    format: Literal['interact-semantic-dataset']
    version: Literal[1]
    dataset: PortableDataset
    permission_recommendations: list[PortablePermissionRecommendation] = Field(
        default_factory=list,
        alias='permissionRecommendations',
        max_length=500,
    )


def _issue(code: str, path: str, message: str, **details: Any) -> dict[str, Any]:
    return {'code': code, 'path': path, 'message': message, **details}


class CatalogResolver:
    def __init__(self, catalog: dict[str, Any]):
        self.catalog = catalog
        self.objects = list(catalog.get('objects') or [])
        self.errors: list[dict[str, Any]] = []

    def object(self, reference: str, path: str) -> dict[str, Any] | None:
        exact = [item for item in self.objects if str(item.get('physical_name')) == reference]
        if len(exact) == 1:
            return exact[0]
        lowered = reference.lower()
        candidates = [item for item in self.objects if str(item.get('physical_name')).lower() == lowered]
        if not candidates and '.' not in reference:
            candidates = [
                item for item in self.objects if str(item.get('physical_name')).split('.')[-1].lower() == lowered
            ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            self.errors.append(
                _issue(
                    'IMPORT-OBJECT-AMBIGUOUS',
                    path,
                    f'Object reference is ambiguous: {reference}',
                    reference=reference,
                    candidates=[item.get('physical_name') for item in candidates[:20]],
                )
            )
        else:
            self.errors.append(
                _issue(
                    'IMPORT-OBJECT-NOT-FOUND',
                    path,
                    f'Object was not found in the current schema snapshot: {reference}',
                    reference=reference,
                )
            )
        return None

    def field(
        self,
        reference: PortableFieldRef,
        path: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        obj = self.object(reference.object_name, f'{path}.object')
        if not obj:
            return None
        fields = list(obj.get('fields') or [])
        exact = [item for item in fields if str(item.get('physical_name')) == reference.field_name]
        candidates = exact or [
            item for item in fields if str(item.get('physical_name')).lower() == reference.field_name.lower()
        ]
        if len(candidates) == 1:
            return obj, candidates[0]
        self.errors.append(
            _issue(
                'IMPORT-FIELD-NOT-FOUND' if not candidates else 'IMPORT-FIELD-AMBIGUOUS',
                f'{path}.field',
                f'Field could not be resolved: {reference.object_name}.{reference.field_name}',
                reference={'object': reference.object_name, 'field': reference.field_name},
                candidates=[item.get('physical_name') for item in candidates[:20]],
            )
        )
        return None

    def field_on_object(
        self,
        obj: dict[str, Any],
        field_name: str,
        path: str,
    ) -> dict[str, Any] | None:
        candidates = [item for item in obj.get('fields') or [] if str(item.get('physical_name')) == field_name]
        if not candidates:
            candidates = [
                item for item in obj.get('fields') or [] if str(item.get('physical_name')).lower() == field_name.lower()
            ]
        if len(candidates) == 1:
            return candidates[0]
        self.errors.append(
            _issue(
                'IMPORT-FIELD-NOT-FOUND' if not candidates else 'IMPORT-FIELD-AMBIGUOUS',
                path,
                f'Field could not be resolved: {obj.get("physical_name")}.{field_name}',
                reference={'object': obj.get('physical_name'), 'field': field_name},
            )
        )
        return None


def _validation_issue(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get('code') or 'IMPORT-DATASET-INVALID')
    messages = {
        'SCHEMA-SNAPSHOT-NOT-READY': 'The imported dataset does not target the current schema snapshot.',
        'SEMANTIC-FIELD-NOT-ALLOWED': 'A referenced object or field is disabled or lacks the required permission.',
        'SEMANTIC-RELATIONSHIP-MISSING': 'A required confirmed relationship is missing.',
        'SEMANTIC-RELATIONSHIP-AMBIGUOUS': 'More than one relationship path can satisfy this field reference.',
        'QUERY-PLAN-INVALID': 'The semantic definition contains an invalid ID, aggregation, metric, or expression.',
        'QUERY-TYPE-MISMATCH': 'The selected aggregation is not compatible with the referenced field type.',
        'QUERY-FANOUT-RISK': 'The selected relationship can duplicate measures during aggregation.',
    }
    path = str(item.get('path') or 'dataset.model')
    path = (
        path.replace('definition.rootObjectId', 'dataset.model.rootObject')
        .replace('definition.relationshipIds', 'dataset.model.relationships')
        .replace('definition.dimensions', 'dataset.model.dimensions')
        .replace('definition.measures', 'dataset.model.measures')
        .replace('definition.metrics', 'dataset.model.metrics')
        .replace('definition.fields', 'dataset.model.fields')
    )
    return _issue(code, path, messages.get(code, code))


def resolve_portable_dataset(  # noqa: C901
    document: dict[str, Any],
    catalog: dict[str, Any],
    connector_id: str,
) -> dict[str, Any]:
    try:
        imported = PortableDatasetDocument.model_validate(document)
    except ValidationError as error:
        return {
            'ok': False,
            'errors': [
                _issue(
                    'IMPORT-DOCUMENT-INVALID',
                    '.'.join(str(part) for part in item['loc']),
                    item['msg'],
                )
                for item in error.errors(include_url=False)
            ],
            'warnings': [],
            'dataset': None,
        }

    resolver = CatalogResolver(catalog)
    model = imported.dataset.model
    root = resolver.object(model.root_object, 'dataset.model.rootObject')
    semantic_ids: set[str] = set()
    required_permissions: dict[tuple[str, str | None, str], dict[str, Any]] = {}

    def require_permission(
        obj: dict[str, Any],
        field: dict[str, Any] | None,
        permission: str,
        required_by: str,
    ) -> None:
        key = (str(obj.get('id')), str(field.get('id')) if field else None, permission)
        requirement = required_permissions.setdefault(
            key,
            {
                'object': obj,
                'field': field,
                'permission': permission,
                'requiredBy': [],
            },
        )
        if required_by not in requirement['requiredBy']:
            requirement['requiredBy'].append(required_by)

    if root:
        require_permission(root, None, 'enabled', 'dataset.model.rootObject')

    def register_semantic_id(value: str, path: str) -> None:
        if value in semantic_ids:
            resolver.errors.append(
                _issue('IMPORT-SEMANTIC-ID-DUPLICATE', path, f'Duplicate semantic ID: {value}', reference=value)
            )
        semantic_ids.add(value)

    dimensions = []
    for index, item in enumerate(model.dimensions):
        register_semantic_id(item.id, f'dataset.model.dimensions.{index}.id')
        resolved = resolver.field(item.field_ref, f'dataset.model.dimensions.{index}.field')
        if resolved:
            require_permission(resolved[0], None, 'enabled', f'dimension {item.id}')
            require_permission(resolved[0], resolved[1], 'readable', f'dimension {item.id}')
            require_permission(resolved[0], resolved[1], 'groupable', f'dimension {item.id}')
            if item.id == model.default_time_dimension:
                require_permission(
                    resolved[0],
                    resolved[1],
                    'filterable',
                    f'default time dimension {item.id}',
                )
            dimensions.append(
                {
                    'id': item.id,
                    'name': item.name,
                    **({'description': item.description} if item.description else {}),
                    'fieldId': resolved[1]['id'],
                }
            )

    measures = []
    for index, item in enumerate(model.measures):
        register_semantic_id(item.id, f'dataset.model.measures.{index}.id')
        resolved = resolver.field(item.field_ref, f'dataset.model.measures.{index}.field')
        filters = []
        for filter_index, condition in enumerate(item.filters):
            filter_field = resolver.field(
                condition.field_ref,
                f'dataset.model.measures.{index}.filters.{filter_index}.field',
            )
            if filter_field:
                field = filter_field[1]
                require_permission(
                    filter_field[0],
                    None,
                    'enabled',
                    f'measure filter {item.id}[{filter_index}]',
                )
                require_permission(
                    filter_field[0],
                    field,
                    'readable',
                    f'measure filter {item.id}[{filter_index}]',
                )
                require_permission(
                    filter_field[0],
                    field,
                    'filterable',
                    f'measure filter {item.id}[{filter_index}]',
                )
                filters.append(
                    {
                        'fieldId': field['id'],
                        'operator': condition.operator,
                        'value': condition.value,
                    }
                )
        if resolved:
            require_permission(resolved[0], None, 'enabled', f'measure {item.id}')
            require_permission(resolved[0], resolved[1], 'readable', f'measure {item.id}')
            require_permission(resolved[0], resolved[1], 'aggregatable', f'measure {item.id}')
            measures.append(
                {
                    'id': item.id,
                    'name': item.name,
                    **({'description': item.description} if item.description else {}),
                    'fieldId': resolved[1]['id'],
                    'aggregation': item.aggregation,
                    **({'filters': filters} if filters else {}),
                }
            )

    metrics = []
    for index, item in enumerate(model.metrics):
        register_semantic_id(item.id, f'dataset.model.metrics.{index}.id')
        expression = {'operator': item.expression.operator}
        for source, target in (
            (item.expression.measure_id, 'measureId'),
            (item.expression.left_measure_id, 'leftMeasureId'),
            (item.expression.right_measure_id, 'rightMeasureId'),
        ):
            if source:
                expression[target] = source
        metrics.append(
            {
                'id': item.id,
                'name': item.name,
                **({'description': item.description} if item.description else {}),
                'semanticType': item.semantic_type,
                'expression': expression,
            }
        )

    relationship_ids: list[str] = []
    confirmed_relationships = [item for item in catalog.get('relationships') or [] if item.get('status') == 'confirmed']
    for index, item in enumerate(model.relationships):
        left = resolver.object(item.left_object, f'dataset.model.relationships.{index}.leftObject')
        right = resolver.object(item.right_object, f'dataset.model.relationships.{index}.rightObject')
        if not left or not right:
            continue
        require_permission(left, None, 'enabled', f'relationship {index} left object')
        require_permission(right, None, 'enabled', f'relationship {index} right object')
        pairs = []
        for pair_index, pair in enumerate(item.join_pairs):
            left_field = resolver.field_on_object(
                left,
                pair.left_field,
                f'dataset.model.relationships.{index}.joinPairs.{pair_index}.leftField',
            )
            right_field = resolver.field_on_object(
                right,
                pair.right_field,
                f'dataset.model.relationships.{index}.joinPairs.{pair_index}.rightField',
            )
            if left_field and right_field:
                pairs.append((left_field['id'], right_field['id']))
        if len(pairs) != len(item.join_pairs):
            continue
        pair_set = set(pairs)
        reverse_pair_set = {(right_id, left_id) for left_id, right_id in pairs}
        matches = []
        for relationship in confirmed_relationships:
            stored_pairs = {
                (pair.get('leftFieldId'), pair.get('rightFieldId')) for pair in relationship.get('join_pairs') or []
            }
            forward = (
                relationship.get('left_object_id') == left['id']
                and relationship.get('right_object_id') == right['id']
                and stored_pairs == pair_set
            )
            reverse = (
                relationship.get('left_object_id') == right['id']
                and relationship.get('right_object_id') == left['id']
                and stored_pairs == reverse_pair_set
            )
            if forward or reverse:
                matches.append(relationship)
        if len(matches) == 1:
            if matches[0]['id'] not in relationship_ids:
                relationship_ids.append(matches[0]['id'])
        else:
            resolver.errors.append(
                _issue(
                    'IMPORT-RELATIONSHIP-NOT-FOUND' if not matches else 'IMPORT-RELATIONSHIP-AMBIGUOUS',
                    f'dataset.model.relationships.{index}',
                    (
                        'A matching confirmed relationship was not found.'
                        if not matches
                        else 'Multiple relationships match.'
                    ),
                    reference={
                        'leftObject': item.left_object,
                        'rightObject': item.right_object,
                        'joinPairs': [pair.model_dump(by_alias=True) for pair in item.join_pairs],
                    },
                )
            )

    if model.default_time_dimension and model.default_time_dimension not in {item['id'] for item in dimensions}:
        resolver.errors.append(
            _issue(
                'IMPORT-DEFAULT-TIME-DIMENSION-NOT-FOUND',
                'dataset.model.defaultTimeDimension',
                'defaultTimeDimension must reference an imported dimension ID.',
                reference=model.default_time_dimension,
            )
        )

    definition = {
        'snapshotId': catalog.get('snapshotId'),
        'rootObjectId': root.get('id') if root else '',
        'whenToUse': model.when_to_use,
        'notFor': model.not_for,
        'examples': model.examples,
        'synonyms': model.synonyms,
        'relationshipIds': relationship_ids,
        'dimensions': dimensions,
        'measures': measures,
        'metrics': metrics,
        **({'defaultTimeDimensionId': model.default_time_dimension} if model.default_time_dimension else {}),
    }
    access = imported.dataset.access
    dataset = {
        'connector_id': connector_id,
        'name': imported.dataset.name,
        'slug': imported.dataset.slug,
        'description': imported.dataset.description,
        'business_domain': imported.dataset.business_domain,
        'access_mode': access.mode,
        'allowed_member_ids': access.member_ids,
        'allowed_group_ids': access.group_ids,
        'allowed_model_ids': access.model_ids,
        'allowed_channel_ids': access.channel_ids,
        'allowed_workflow_ids': access.workflow_ids,
        'definition': definition,
    }

    permission_changes: list[dict[str, Any]] = []
    change_index: dict[tuple[str, str | None, str, bool], dict[str, Any]] = {}

    def permission_value(
        obj: dict[str, Any],
        field: dict[str, Any] | None,
        permission: str,
    ) -> bool:
        return bool((field if field is not None else obj).get(permission))

    def append_change(
        obj: dict[str, Any],
        field: dict[str, Any] | None,
        permission: str,
        desired: bool,
        reason: str,
        required_by: list[str],
        required: bool,
        source: str,
        conflict: bool = False,
    ) -> dict[str, Any]:
        object_name = str(obj.get('physical_name') or '')
        field_name = str(field.get('physical_name') or '') if field else None
        current = permission_value(obj, field, permission)
        action = 'grant' if desired else 'revoke'
        status = 'conflict' if conflict else 'already_satisfied' if current == desired else 'pending'
        key = (str(obj.get('id')), str(field.get('id')) if field else None, permission, desired)
        existing = change_index.get(key)
        if existing:
            existing['required'] = existing['required'] or required
            existing['source'] = 'system+ai' if existing['source'] != source else source
            existing['requiredBy'] = list(dict.fromkeys([*existing['requiredBy'], *required_by]))
            if reason not in existing['reason']:
                existing['reason'] = f'{existing["reason"]} {reason}'
            if conflict:
                existing['status'] = 'conflict'
            return existing
        change = {
            'id': f'{object_name}|{field_name or "-"}|{permission}|{action}',
            'targetType': 'field' if field else 'object',
            'object': object_name,
            'field': field_name,
            'objectId': obj.get('id'),
            'fieldId': field.get('id') if field else None,
            'permission': permission,
            'action': action,
            'current': current,
            'desired': desired,
            'reason': reason,
            'required': required,
            'requiredBy': required_by,
            'source': source,
            'status': status,
            'impact': (
                '若略過，這份語意資料集將無法通過驗證或執行相關查詢。'
                if required
                else '這是 AI 的治理建議；略過不會阻止本次資料集匯入。'
                if desired
                else '關閉後可能影響其他既有資料集；請確認沒有其他用途再同意。'
            ),
        }
        change_index[key] = change
        permission_changes.append(change)
        return change

    for requirement in required_permissions.values():
        obj = requirement['object']
        field = requirement['field']
        permission = requirement['permission']
        if not permission_value(obj, field, permission):
            append_change(
                obj,
                field,
                permission,
                True,
                '系統依語意模型推導出的最低必要權限。',
                requirement['requiredBy'],
                True,
                'system',
            )

    required_keys = set(required_permissions)
    for index, recommendation in enumerate(imported.permission_recommendations):
        path = f'permissionRecommendations.{index}'
        obj = resolver.object(recommendation.target.object_name, f'{path}.target.object')
        if not obj:
            continue
        field = None
        if recommendation.target.field_name:
            field = resolver.field_on_object(
                obj,
                recommendation.target.field_name,
                f'{path}.target.field',
            )
            if not field:
                continue
        desired = recommendation.action == 'grant'
        requirement_key = (
            str(obj.get('id')),
            str(field.get('id')) if field else None,
            recommendation.permission,
        )
        conflicts_with_dataset = not desired and requirement_key in required_keys
        append_change(
            obj,
            field,
            recommendation.permission,
            desired,
            recommendation.reason,
            recommendation.required_for,
            requirement_key in required_keys and desired,
            'ai',
            conflicts_with_dataset,
        )

    # Validate semantics with only the system-derived grants applied in memory. This
    # separates structural errors from permissions that still require human approval.
    effective_catalog = deepcopy(catalog)
    effective_objects = {str(item.get('id')): item for item in effective_catalog.get('objects') or []}
    for (object_id, field_id, permission), _requirement in required_permissions.items():
        effective_object = effective_objects.get(object_id)
        if not effective_object:
            continue
        if field_id is None:
            effective_object[permission] = True
            continue
        effective_field = next(
            (item for item in effective_object.get('fields') or [] if str(item.get('id')) == field_id),
            None,
        )
        if effective_field:
            effective_field[permission] = True

    validation = validate_dataset_definition(dataset, effective_catalog)
    errors = [*resolver.errors, *[_validation_issue(item) for item in validation['errors']]]
    warnings = [_validation_issue(item) for item in validation['warnings']]
    for change in permission_changes:
        if change['status'] == 'conflict':
            warnings.append(
                _issue(
                    'IMPORT-PERMISSION-CONFLICT',
                    'permissionRecommendations',
                    (
                        f'AI 建議關閉 {change["object"]}.{change["field"] or change["permission"]}，'
                        '但本資料集需要此權限；此建議只能略過。'
                    ),
                    reference=change['id'],
                )
            )
    if access.mode == 'selected_channels' and not access.channel_ids:
        errors.append(
            _issue(
                'IMPORT-CHANNEL-ACL-EMPTY',
                'dataset.access.channelIds',
                'selected_channels access requires at least one channel ID.',
            )
        )
    required_pending = any(change['required'] and change['status'] == 'pending' for change in permission_changes)
    return {
        'ok': not errors and not required_pending,
        'errors': errors,
        'warnings': warnings,
        'dataset': dataset,
        'permissionReviewRequired': any(change['status'] in {'pending', 'conflict'} for change in permission_changes),
        'permissionChanges': permission_changes,
        'authorizationSummary': {
            'missingRequired': sum(
                1 for change in permission_changes if change['required'] and change['status'] == 'pending'
            ),
            'aiSuggestions': len(imported.permission_recommendations),
            'alreadySatisfied': sum(1 for change in permission_changes if change['status'] == 'already_satisfied'),
            'conflicts': sum(1 for change in permission_changes if change['status'] == 'conflict'),
        },
        'summary': {
            'dimensions': len(dimensions),
            'measures': len(measures),
            'metrics': len(metrics),
            'relationships': len(relationship_ids),
        },
    }
