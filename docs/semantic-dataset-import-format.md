# Portable Semantic Dataset JSON v1

`interact-semantic-dataset` 是跨 Connector 的語意資料集匯入格式。它使用資料庫中的實體物件與欄位名稱，不保存 WebUI 內部 UUID，因此可以由程式、顧問、資料團隊或另一套部署產生。

## 安全與生命週期

- 匯入只解析並套用為新的未儲存草稿，不會直接建立或發布資料集。
- 匯入 API 只解析權限建議，不會直接啟用資料表、改變欄位權限或建立關聯。
- WebUI 只有在企業管理員逐項同意後才會呼叫 Catalog ACL API；略過必要權限時不能套用草稿。
- 所有物件、欄位與關聯都以 Connector 最新的 ready schema snapshot 解析。
- 關聯必須已存在於 catalog 且狀態為 `confirmed`。
- 匯入後仍須通過 Dataset validation、Connector ACL、欄位 ACL 與 fanout 檢查。
- JSON 不接受 SQL、連線字串、密碼、Service Token 或任意執行內容。
- 單一匯入文件上限為 500 KB。

## 最外層格式

```json
{
	"format": "interact-semantic-dataset",
	"version": 1,
	"dataset": {},
	"permissionRecommendations": []
}
```

未知屬性會被拒絕，避免拼字錯誤被靜默忽略。未來不相容變更必須增加 `version`。

## 通用範例

```json
{
	"format": "interact-semantic-dataset",
	"version": 1,
	"permissionRecommendations": [
		{
			"target": {
				"object": "directory.carriers",
				"field": "display_name"
			},
			"permission": "groupable",
			"action": "grant",
			"reason": "承運商排名需要依名稱分組；未開啟就無法產生排名。",
			"requiredFor": ["carrier.name dimension"]
		}
	],
	"dataset": {
		"name": "Delivery performance",
		"slug": "delivery-performance",
		"description": "Delivery volume, fees, and carrier performance.",
		"businessDomain": "Logistics",
		"access": {
			"mode": "company_admins",
			"memberIds": [],
			"groupIds": [],
			"modelIds": [],
			"channelIds": [],
			"workflowIds": []
		},
		"model": {
			"rootObject": "warehouse.shipments",
			"whenToUse": "Delivery rankings, fees, and completion rates.",
			"notFor": ["inventory levels"],
			"examples": ["Which carrier delivered the most shipments this month?"],
			"synonyms": ["carrier", "parcel"],
			"defaultTimeDimension": "shipment.delivered_at",
			"dimensions": [
				{
					"id": "carrier.name",
					"name": "Carrier",
					"field": {
						"object": "directory.carriers",
						"field": "display_name"
					}
				},
				{
					"id": "shipment.delivered_at",
					"name": "Delivered at",
					"field": {
						"object": "warehouse.shipments",
						"field": "delivered_at"
					}
				}
			],
			"measures": [
				{
					"id": "shipment.fees",
					"name": "Delivery fees",
					"field": {
						"object": "warehouse.shipments",
						"field": "delivery_fee"
					},
					"aggregation": "sum",
					"filters": [
						{
							"field": {
								"object": "warehouse.shipments",
								"field": "status"
							},
							"operator": "eq",
							"value": "delivered"
						}
					]
				},
				{
					"id": "shipment.count",
					"name": "Shipments",
					"field": {
						"object": "warehouse.shipments",
						"field": "tracking_id"
					},
					"aggregation": "count_distinct"
				}
			],
			"metrics": [
				{
					"id": "shipment.average_fee",
					"name": "Average fee",
					"semanticType": "money",
					"expression": {
						"operator": "divide",
						"leftMeasureId": "shipment.fees",
						"rightMeasureId": "shipment.count"
					}
				}
			],
			"relationships": [
				{
					"leftObject": "warehouse.shipments",
					"rightObject": "directory.carriers",
					"joinPairs": [
						{
							"leftField": "carrier_id",
							"rightField": "id"
						}
					]
				}
			]
		}
	}
}
```

## 欄位規則

### Dataset

| JSON path                | 必填 | 說明                                                                               |
| ------------------------ | ---- | ---------------------------------------------------------------------------------- |
| `dataset.name`           | 是   | 使用者可讀名稱，最長 200 字元。                                                    |
| `dataset.slug`           | 是   | 小寫英數、`_`、`-`，最長 120 字元。                                                |
| `dataset.description`    | 否   | Agent selector 與管理介面使用的用途摘要。                                          |
| `dataset.businessDomain` | 否   | 商業領域，例如 Logistics、Finance、HR。                                            |
| `dataset.access.mode`    | 否   | `company_admins`、`all_company_members`、`selected_members`、`selected_channels`。 |

`selected_channels` 必須提供至少一個 `channelIds`。匯入的 ACL 只會再縮小 Connector ACL，不會擴大 Connector 本身的允許範圍。

### Object 與 field reference

```json
{
	"object": "schema_or_namespace.table_or_view",
	"field": "column_name"
}
```

完整 physical object name 會優先精確比對。省略 schema 時只有在目前 snapshot 中名稱唯一才可解析；有多個同名物件時會回 `IMPORT-OBJECT-AMBIGUOUS`。

### Dimensions

Dimension 使用穩定的 semantic `id`，必須對應已啟用物件中 `readable + groupable` 的欄位。作為時間篩選的 dimension 還必須 `filterable`。

### Measures

支援的 `aggregation`：

- `sum`
- `count`
- `count_distinct`
- `avg`
- `min`
- `max`

Measure 欄位必須 `readable + aggregatable`。Measure filter 的欄位必須 `readable + filterable`，支援 `eq`、`ne`、比較、集合、文字、null 與 between operators。

### Metrics

Metric expression 支援 `measure`、`divide`、`multiply`、`add`、`subtract`。Metric 只能引用同一份 JSON 中定義的 measure semantic ID。

### Relationships

Relationship 使用 physical object 與 join field 描述，不使用內部 relationship UUID。`joinPairs` 支援 1 到 8 組欄位，因此可解析複合鍵，也可用與 catalog 相反的左右方向描述。

匯入只會尋找完全相符且已確認的 catalog relationship；找不到時不會自行建立 join。

### Permission recommendations

`permissionRecommendations` 是 AI 的治理建議，不是授權指令。每一項使用 physical object／field 名稱，支援：

- Object：`enabled`
- Field：`readable`、`filterable`、`groupable`、`aggregatable`
- Action：`grant`、`revoke`

`reason` 至少 8 個字元，應說明對應商業問題、語意欄位及不變更的後果。`requiredFor` 用來列出 dimension、measure、filter 或治理目的。

後端不信任 AI 的清單，而會由 dataset model 重新推導最低必要權限。AI 漏列時系統會補出 `source: system` 的必要授權；AI 建議關閉本資料集需要的權限時，會回傳 `status: conflict`，只能略過。

## API

```text
POST /api/v1/interact/data-connectors/{connector_id}/semantic-datasets/import
```

Request：

```json
{
	"document": {
		"format": "interact-semantic-dataset",
		"version": 1,
		"dataset": {},
		"permissionRecommendations": []
	}
}
```

Response 會包含 `errors`、`warnings`、解析後的 draft `dataset`、`permissionChanges`、`authorizationSummary` 與 dimensions/measures/metrics/relationships 數量。此 API 不寫入資料庫。
