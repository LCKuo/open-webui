from __future__ import annotations

from functools import lru_cache
from typing import Any


def _field_reference(objects: dict[str, dict[str, Any]], field_id: str | None) -> str | None:
    if not field_id:
        return None
    for obj in objects.values():
        for field in obj.get('fields') or []:
            if field.get('id') == field_id:
                return str(field.get('physical_name') or '') or None
    return None


@lru_cache(maxsize=1)
def _portable_dataset_json_schema() -> dict[str, Any]:
    from open_webui.semantic_query.dataset_import import PortableDatasetDocument

    return PortableDatasetDocument.model_json_schema(by_alias=True)


def build_schema_handoff(
    catalog: dict[str, Any],
    connector: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a credential-free, portable schema brief for an external AI."""
    snapshot_schema = (snapshot or {}).get('schema_json') or {}
    scanned_objects = {
        str(item.get('name') or item.get('table') or ''): item
        for item in snapshot_schema.get('tables') or snapshot_schema.get('objects') or []
        if str(item.get('name') or item.get('table') or '')
    }
    object_by_id = {str(item.get('id')): item for item in catalog.get('objects') or []}
    objects = []
    undocumented_objects: list[str] = []
    undocumented_fields: list[str] = []
    undocumented_views: list[str] = []
    for obj in sorted(object_by_id.values(), key=lambda item: str(item.get('physical_name') or '')):
        object_name = str(obj.get('physical_name') or '')
        scanned_object = scanned_objects.get(object_name) or {}
        scanned_fields = {
            str(item.get('name') or ''): item
            for item in scanned_object.get('columns') or []
            if str(item.get('name') or '')
        }
        object_description = str(obj.get('description') or scanned_object.get('description') or '').strip()
        object_type = str(obj.get('object_type') or scanned_object.get('type') or 'table').lower()
        if not object_description:
            undocumented_objects.append(object_name)
            if 'view' in object_type:
                undocumented_views.append(object_name)
        fields = []
        for field in sorted(obj.get('fields') or [], key=lambda item: str(item.get('physical_name') or '')):
            field_name = str(field.get('physical_name') or '')
            scanned_field = scanned_fields.get(field_name) or {}
            field_description = str(field.get('description') or scanned_field.get('description') or '').strip()
            if not field_description:
                undocumented_fields.append(f'{object_name}.{field_name}')
            fields.append(
                {
                    'name': field_name,
                    'displayName': field.get('display_name') or field_name,
                    'description': field_description,
                    'physicalType': field.get('physical_type') or '',
                    'semanticType': field.get('semantic_type') or '',
                    'nullable': bool(field.get('nullable', scanned_field.get('nullable', True))),
                    'primaryKey': bool(field.get('primary_key')),
                    'unique': bool(scanned_field.get('unique')),
                    **(
                        {'defaultExpression': scanned_field.get('default')}
                        if scanned_field.get('default') is not None
                        else {}
                    ),
                    **(
                        {'allowedValues': list(scanned_field.get('allowed_values') or [])}
                        if scanned_field.get('allowed_values')
                        else {}
                    ),
                    'sensitivity': field.get('sensitivity') or 'internal',
                    'maskingRule': field.get('masking_rule') or 'none',
                    'authorization': {
                        'readable': bool(field.get('readable')),
                        'filterable': bool(field.get('filterable')),
                        'groupable': bool(field.get('groupable')),
                        'aggregatable': bool(field.get('aggregatable')),
                    },
                }
            )
        primary_key = [item['name'] for item in fields if item['primaryKey']]
        unique_keys = [
            {
                'name': item.get('name') or '',
                'fields': list(item.get('columns') or []),
            }
            for item in scanned_object.get('unique_keys') or []
            if item.get('columns')
        ]
        objects.append(
            {
                'name': object_name,
                'displayName': obj.get('display_name') or object_name,
                'description': object_description,
                'objectType': obj.get('object_type') or 'table',
                'documentationStatus': 'documented' if object_description else 'missing_description',
                'constraints': {
                    'primaryKey': primary_key,
                    'uniqueKeys': unique_keys,
                    'candidateKeys': [key for key in [primary_key, *[item['fields'] for item in unique_keys]] if key],
                    'foreignKeys': [
                        {
                            'field': item.get('column'),
                            'referencesObject': item.get('references_table'),
                            'referencesField': item.get('references_column'),
                            'constraintName': item.get('constraint_name') or '',
                        }
                        for item in scanned_object.get('foreign_keys') or []
                    ],
                },
                'authorization': {'enabled': bool(obj.get('enabled'))},
                'fields': fields,
            }
        )

    relationships = []
    for relationship in catalog.get('relationships') or []:
        left = object_by_id.get(str(relationship.get('left_object_id')))
        right = object_by_id.get(str(relationship.get('right_object_id')))
        if not left or not right:
            continue
        join_pairs = []
        for pair in relationship.get('join_pairs') or []:
            left_field = _field_reference(object_by_id, pair.get('leftFieldId'))
            right_field = _field_reference(object_by_id, pair.get('rightFieldId'))
            if left_field and right_field:
                join_pairs.append({'leftField': left_field, 'rightField': right_field})
        relationships.append(
            {
                'leftObject': left.get('physical_name'),
                'rightObject': right.get('physical_name'),
                'relationshipType': relationship.get('relationship_type'),
                'joinType': relationship.get('join_type'),
                'joinPairs': join_pairs,
                'status': relationship.get('status'),
                'fanoutRisk': relationship.get('fanout_risk'),
                'description': relationship.get('description') or '',
            }
        )

    return {
        'format': 'interact-semantic-schema-handoff',
        'version': 3,
        'source': {
            'connectorName': connector.get('name') or '',
            'connectorType': connector.get('connectorType') or '',
            'snapshotVersion': (snapshot or {}).get('version'),
            'snapshotFingerprint': (snapshot or {}).get('fingerprint') or '',
            'databaseDescription': ((snapshot_schema.get('database') or {}).get('description') or ''),
            'schemaDescriptions': snapshot_schema.get('schema_descriptions') or {},
        },
        'securityNotice': {
            'containsCredentials': False,
            'containsDataRows': False,
            'schemaNamesMayBeSensitive': True,
            'message': '此檔案只有結構與目前授權狀態；分享前仍應確認資料表與欄位名稱可對外揭露。',
        },
        'instructionsForAi': {
            'role': '你是資料建模與資料治理助理。請根據 schema 建立可匯回 Interact WebUI 的語意資料集 JSON。',
            'task': [
                '理解使用者想回答的商業問題，選擇最小必要的物件、欄位、聚合與關聯。',
                '輸出一個 interact-semantic-dataset v1 JSON，不要輸出 Markdown、SQL、解說文字或多個 JSON。',
                '所有 object 與 field 必須使用本檔案 schema 中的實體名稱，不得捏造內部 UUID。',
                '逐項檢查 authorization；需要但尚未開啟的權限，必須列入 permissionRecommendations。',
                '可建議撤銷不必要權限，但不得撤銷同一份語意資料集所需權限。',
            ],
            'minimumPermissionRules': {
                'rootObject': ['object.enabled'],
                'dimension': ['object.enabled', 'field.readable', 'field.groupable'],
                'defaultTimeDimension': ['field.filterable'],
                'measure': ['object.enabled', 'field.readable', 'field.aggregatable'],
                'measureFilter': ['object.enabled', 'field.readable', 'field.filterable'],
                'relationship': [
                    '兩端 object.enabled',
                    'relationship.status 必須是 confirmed',
                    '只作 JOIN 的鍵不需要 field.readable、filterable、groupable 或 aggregatable',
                ],
            },
            'relationshipJoinKeyPolicy': {
                'useConfirmedRelationshipsOnly': True,
                'copyExactObjectAndJoinFieldNamesFromSchemaRelationships': True,
                'joinOnlyKeysRequireReadable': False,
                'joinOnlyKeysRequireFilterable': False,
                'joinOnlyKeysRequireGroupable': False,
                'joinOnlyKeysRequireAggregatable': False,
                'recommendPermissionsForJoinOnlyKeys': False,
                'rule': (
                    'JOIN 鍵是內部結構欄位。即使 authorization.readable=false，只要 schema.relationships '
                    '內有相同且 confirmed 的關聯，仍應把該 relationship 複製到輸出；不得只為 JOIN 建議開放欄位權限。'
                ),
                'exception': (
                    '若同一欄位同時被當作 dimension、measure 或 measure filter，才依該用途套用對應欄位權限。'
                ),
            },
            'measureFilterPolicy': {
                'fieldReferenceUsesPhysicalNames': True,
                'requiredPermissions': ['object.enabled', 'field.readable', 'field.filterable'],
                'operators': [
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
                ],
                'rule': (
                    'filters.field.object 與 filters.field.field 必須使用 schema 內的實體名稱；'
                    '不得填 semantic ID。in、not_in、between 的 value 必須是陣列，between 必須剛好兩個值。'
                ),
            },
            'permissionRecommendationRules': [
                '每項建議只能變更 enabled、readable、filterable、groupable、aggregatable 其中一個權限。',
                'action 使用 grant 或 revoke；reason 必須說明商業問題、對應維度／指標及不變更的後果。',
                '未授權但資料集需要的 table 與 field 不可省略，必須逐項列出。',
                'WebUI 會重新驗證所有建議並逐項詢問管理員；AI 建議不會自動取得授權。',
            ],
            'schemaInterpretationRules': [
                (
                    'description、allowedValues、constraints.candidateKeys 與 confirmed relationship '
                    '是優先證據，不可只靠英文名稱猜測商業語意。'
                ),
                (
                    '沒有 description 的 view 不得假設資料粒度、統計期間或聚合口徑；'
                    '優先選擇有明確時間欄位與關聯的基礎事實表。'
                ),
                '不得自行捏造列舉值或狀態 filter；只能使用 allowedValues 已列出的值，或完全不加該 filter。',
                '同一資料集若跨越兩條 one-to-many 事實路徑，必須避免聚合 fanout；無法證明安全時拆成不同資料集。',
                '以 candidateKeys 判斷資料粒度與人員／客戶唯一性；顯示名稱不可取代唯一識別欄位。',
                'defaultTimeDimension 必須是實際事件時間；last_* 時間通常只表示最近事件，不等同歷史事件期間。',
            ],
            'preflightChecklist': [
                '只輸出一個 JSON object，且不得包在 Markdown code fence。',
                'format 必須是 interact-semantic-dataset，version 必須是數字 1。',
                'rootObject、所有 field.object 與 field.field 都能在 schema 中以實體名稱精確找到。',
                '每個跨表 dimension、measure 或 measure filter 都有一條輸出 relationships 可連回 rootObject。',
                '每條輸出 relationship 都逐字複製自 schema.relationships 中 status=confirmed 的物件與 joinPairs。',
                '沒有因為 JOIN-only 欄位 readable=false 而省略 relationship，也沒有為 JOIN-only 欄位建議授權。',
                '每個未滿足的用途權限都有一筆獨立 permissionRecommendations；不得合併多個 permission。',
                'defaultTimeDimension 若存在，必須等於 dimensions 中某一個 id。',
                'sum 與 avg 只使用可做算術的數值欄位；人數或筆數優先使用穩定鍵 count_distinct。',
                'metric expression 只引用同一份 JSON 內已定義的 measure ID。',
                '未使用 sql、query、credentials、connectionString，也未加入 JSON Schema 不允許的額外欄位。',
            ],
        },
        'outputContract': {
            'format': 'interact-semantic-dataset',
            'version': 1,
            'topLevelKeys': ['format', 'version', 'dataset', 'permissionRecommendations'],
            'permissionRecommendationShape': {
                'target': {'object': 'schema.table', 'field': 'column（物件 enabled 建議時省略）'},
                'permission': 'enabled | readable | filterable | groupable | aggregatable',
                'action': 'grant | revoke',
                'reason': '具體說明為何需要開啟或關閉，以及對查詢的影響',
                'requiredFor': ['對應的 dimension、measure、filter 或治理目的'],
            },
            'datasetModelNotes': {
                'fieldReference': {'object': 'schema.table', 'field': 'column'},
                'aggregation': 'sum | count | count_distinct | avg | min | max',
                'accessMode': 'company_admins | selected_members | all_company_members | selected_channels',
                'relationship': {
                    'leftObject': 'schema.table',
                    'rightObject': 'schema.table',
                    'joinPairs': [{'leftField': 'column', 'rightField': 'column'}],
                    'mustMatch': 'schema.relationships 中 status=confirmed 的關聯；不得自行創造或猜測',
                },
                'forbiddenKeys': ['sql', 'query', 'credentials', 'connectionString'],
            },
            'strictJsonSchema': _portable_dataset_json_schema(),
            'jsonTemplate': {
                'format': 'interact-semantic-dataset',
                'version': 1,
                'dataset': {
                    'name': '使用者可讀的資料集名稱',
                    'slug': 'lowercase-stable-id',
                    'description': '這份資料集能回答的商業問題',
                    'businessDomain': '商業領域',
                    'access': {
                        'mode': 'company_admins',
                        'memberIds': [],
                        'groupIds': [],
                        'modelIds': [],
                        'channelIds': [],
                        'workflowIds': [],
                    },
                    'model': {
                        'rootObject': 'schema.fact_table',
                        'whenToUse': '具體說明何時應選用此資料集',
                        'notFor': ['不適用問題'],
                        'examples': ['使用者可能提出的問題'],
                        'synonyms': ['商業同義詞'],
                        'defaultTimeDimension': 'dimension.semantic_id',
                        'dimensions': [
                            {
                                'id': 'dimension.semantic_id',
                                'name': '維度名稱',
                                'description': '維度定義',
                                'field': {'object': 'schema.table', 'field': 'column'},
                            }
                        ],
                        'measures': [
                            {
                                'id': 'measure.left_id',
                                'name': '指標名稱',
                                'description': '計算口徑',
                                'field': {'object': 'schema.table', 'field': 'column'},
                                'aggregation': 'sum',
                                'filters': [
                                    {
                                        'field': {'object': 'schema.table', 'field': 'status'},
                                        'operator': 'eq',
                                        'value': 'active',
                                    }
                                ],
                            },
                            {
                                'id': 'measure.right_id',
                                'name': '分母指標名稱',
                                'description': '衍生指標使用的分母口徑',
                                'field': {'object': 'schema.table', 'field': 'id'},
                                'aggregation': 'count_distinct',
                                'filters': [],
                            },
                        ],
                        'metrics': [
                            {
                                'id': 'metric.semantic_id',
                                'name': '衍生指標名稱',
                                'semanticType': 'number',
                                'expression': {
                                    'operator': 'divide',
                                    'leftMeasureId': 'measure.left_id',
                                    'rightMeasureId': 'measure.right_id',
                                },
                            }
                        ],
                        'relationships': [
                            {
                                'leftObject': 'schema.fact_table',
                                'rightObject': 'schema.dimension_table',
                                'joinPairs': [{'leftField': 'dimension_id', 'rightField': 'id'}],
                            }
                        ],
                    },
                },
                'permissionRecommendations': [
                    {
                        'target': {'object': 'schema.table', 'field': 'column'},
                        'permission': 'groupable',
                        'action': 'grant',
                        'reason': '說明對應商業問題、語意欄位，以及不開啟的後果。',
                        'requiredFor': ['dimension.semantic_id'],
                    }
                ],
            },
        },
        'schemaQuality': {
            'status': 'complete' if not undocumented_objects and not undocumented_fields else 'incomplete',
            'objectCount': len(objects),
            'documentedObjectCount': len(objects) - len(undocumented_objects),
            'fieldCount': sum(len(item['fields']) for item in objects),
            'documentedFieldCount': sum(len(item['fields']) for item in objects) - len(undocumented_fields),
            'undocumentedObjects': undocumented_objects,
            'undocumentedFields': undocumented_fields,
            'warnings': [
                *(
                    ['部分物件缺少資料庫或 Catalog description；AI 不得自行補造商業口徑。']
                    if undocumented_objects
                    else []
                ),
                *(['部分欄位缺少 description；名稱只能作為線索，不是權威定義。'] if undocumented_fields else []),
                *(
                    [f'這些 View 缺少粒度說明，不宜直接作為事實表：{", ".join(undocumented_views)}']
                    if undocumented_views
                    else []
                ),
            ],
        },
        'schema': {'objects': objects, 'relationships': relationships},
    }
