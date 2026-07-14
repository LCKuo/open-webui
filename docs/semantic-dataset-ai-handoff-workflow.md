# Semantic Dataset AI Handoff Workflow

## 使用者流程

1. 企業管理員進入 Connector 的「語意資料集」，開啟「匯入 JSON」。
2. 按「匯出 AI Schema 交接包」。WebUI 從最新 ready snapshot 產生 `interact-semantic-schema-handoff v1`。
3. 使用者自行把交接包與商業問題交給選定的 AI。WebUI 不呼叫或指定任何外部模型。
4. AI 只回傳一個 `interact-semantic-dataset v1` JSON，並在 `permissionRecommendations` 列出建議開啟或關閉的權限。
5. 使用者上傳或貼上 JSON。後端解析 physical names、確認 relationship，並獨立推導最低必要權限。
6. WebUI 一次顯示一項權限表單。管理員可同意、略過或暫停後繼續。
7. 每項同意的變更透過 company-scoped Catalog ACL API 套用；API 會再次核對 Connector 與 latest snapshot。完成後重新載入 Catalog 並以原 JSON 再驗證。
8. 必要授權均成立且結構驗證通過後，才可「套用為新草稿」。儲存與發布仍是後續獨立操作。

## 交接包內容

匯出 API：

```text
GET /api/v1/interact/data-connectors/{connector_id}/semantic-datasets/ai-schema-handoff
```

逐項權限 API：

```text
POST /api/v1/interact/data-connectors/{connector_id}/catalog/permission-changes
```

Request 必須帶入匯入時的 `snapshot_id`、target type／ID、單一 permission 與 desired boolean。若 latest snapshot 已更新就回 HTTP 409，管理員必須重新檢查 JSON，不能把舊審核決定套到新 Schema。

交接包包含：

- Connector 顯示名稱、類型、snapshot version 與 fingerprint。
- 所有已授權與未授權 object。
- 欄位 physical／semantic type、PK、敏感度、遮罩與四種授權狀態。
- 關聯方向、join pairs、確認狀態與 fanout 風險。
- AI 任務、最低權限規則、輸出契約及禁止欄位。

交接包不包含 Host、Port、Database username、Password、Connection string、Token、資料列或內部 Catalog UUID。Schema 名稱本身仍可能敏感，因此 UI 會提醒使用者在交給外部 AI 前自行確認。

## 權限推導

| 語意用途               | 系統最低需求                                       |
| ---------------------- | -------------------------------------------------- |
| Root object            | `object.enabled`                                   |
| Dimension              | `enabled + readable + groupable`                   |
| Default time dimension | Dimension 權限再加 `filterable`                    |
| Measure                | `enabled + readable + aggregatable`                |
| Measure filter         | `enabled + readable + filterable`                  |
| Relationship           | 兩端 object enabled，且既有 relationship confirmed |

系統推導優先於 AI 建議。AI 建議分為：

- `system`：AI 漏列，但資料集執行所需。
- `ai`：只由 AI 提出的額外最小權限或撤銷建議。
- `system+ai`：AI 建議與系統推導一致。
- `conflict`：AI 想撤銷本資料集所需權限，UI 不提供套用按鈕。

## 失敗與恢復

- JSON、物件、欄位或關聯錯誤：列出 JSON path，不進入授權流程。
- 權限 API 失敗：停留在目前項目，顯示錯誤，不自動跳到下一項。
- 使用者略過必要授權：保留解析結果，但禁止套用草稿。
- 使用者略過 AI 額外建議：不阻止匯入。
- 使用者中途關閉：已同意變更不回滾，重新載入 Catalog，之後可從未處理項目繼續。
- Schema 在流程中更新：重新驗證會依最新 ready snapshot 回報 stale／missing reference，不會套用舊 UUID。
