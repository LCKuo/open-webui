from __future__ import annotations

from typing import Any


def _field_reference(objects: dict[str, dict[str, Any]], field_id: str | None) -> str | None:
    if not field_id:
        return None
    for obj in objects.values():
        for field in obj.get('fields') or []:
            if field.get('id') == field_id:
                return str(field.get('physical_name') or '') or None
    return None


def build_schema_handoff(
    catalog: dict[str, Any],
    connector: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a credential-free, portable schema brief for an external AI."""
    object_by_id = {str(item.get('id')): item for item in catalog.get('objects') or []}
    objects = []
    for obj in sorted(object_by_id.values(), key=lambda item: str(item.get('physical_name') or '')):
        fields = []
        for field in sorted(obj.get('fields') or [], key=lambda item: str(item.get('physical_name') or '')):
            fields.append(
                {
                    'name': field.get('physical_name'),
                    'displayName': field.get('display_name') or field.get('physical_name'),
                    'description': field.get('description') or '',
                    'physicalType': field.get('physical_type') or '',
                    'semanticType': field.get('semantic_type') or '',
                    'primaryKey': bool(field.get('primary_key')),
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
        objects.append(
            {
                'name': obj.get('physical_name'),
                'displayName': obj.get('display_name') or obj.get('physical_name'),
                'description': obj.get('description') or '',
                'objectType': obj.get('object_type') or 'table',
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
        'version': 1,
        'source': {
            'connectorName': connector.get('name') or '',
            'connectorType': connector.get('connectorType') or '',
            'snapshotVersion': (snapshot or {}).get('version'),
            'snapshotFingerprint': (snapshot or {}).get('fingerprint') or '',
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
                'relationship': ['兩端 object.enabled', 'relationship.status 必須是 confirmed'],
            },
            'permissionRecommendationRules': [
                '每項建議只能變更 enabled、readable、filterable、groupable、aggregatable 其中一個權限。',
                'action 使用 grant 或 revoke；reason 必須說明商業問題、對應維度／指標及不變更的後果。',
                '未授權但資料集需要的 table 與 field 不可省略，必須逐項列出。',
                'WebUI 會重新驗證所有建議並逐項詢問管理員；AI 建議不會自動取得授權。',
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
                'relationship': {
                    'leftObject': 'schema.table',
                    'rightObject': 'schema.table',
                    'joinPairs': [{'leftField': 'column', 'rightField': 'column'}],
                },
                'forbiddenKeys': ['sql', 'query', 'credentials', 'connectionString'],
            },
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
        'schema': {'objects': objects, 'relationships': relationships},
    }
