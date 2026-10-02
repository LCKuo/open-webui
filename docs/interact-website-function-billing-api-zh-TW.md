# Website 通用功能扣款 API

更新日期：2026-09-21。v1 已發布至正式 Website，建置版本 `d4AnHbiehU8Uy5KpQGYqA`；[驗收與發布紀錄](./operations/app-billing-release-20260921.md)。

## 1. 適用範圍

Website + Interact AI 是同一平台，共用企業錢包。這份 API 用於不呼叫 AI 的 App 功能，例如文件匯出、資料處理。AI 模型用量仍由 Interact AI 結算，App 不可將同一筆 AI 用量再次送到這份 API 扣款。

這不是登入 API、AI API Key 或產品 entitlement API，也不新增 CRM 席位限制。現有 CRM Agent Token 與 AI 扣款路徑保留不變。OIDC 登入、產品授權查詢與同時使用人數租約仍不在本次交付範圍。

## 2. 管理員設定

1. 登入 Website Admin，側邊欄選「功能扣款 API」：`/admin/licenses/billing`。
2. 點「建立扣款憑證」，選擇實際扣款企業，輸入產品代碼與名稱、憑證效期、單次及每日 Token 上限。
3. 設定第一項付費功能：功能代碼、名稱、計量單位、每單位 Token、單次數量上限。
4. 建立後憑證只顯示一次，以安全方式存入產品後端的 secret manager／環境變數。Website 只保存雜湊；遺失時建立新憑證，再撤銷舊憑證。
5. 「管理費率」可新增功能或建立同一功能的新費率版本，已預扣的操作仍按原費率結算。停用憑證不會動到公司的 AI Key、CRM 綁定或其他產品憑證。

費率與上限須由管理員確認，本系統不會替客戶預設收費或自動發放扣款權。建立、撤銷及費率修改會保存操作者、時間與內容稽核紀錄。

每個憑證只綁定一個公司及產品；請求不接受自行指定公司、產品、單價或扣款金額。憑證最長一年，每日限額採台灣時間。這是有界限的後端服務授權，不代表已驗證 App 內使用者的主站身分。

## 3. 連線規則

Base URL：`https://interact-vision.com.tw/api/app-integrations/v1`

```http
Authorization: Bearer iwb_<管理員核發的憑證>
Content-Type: application/json
Idempotency-Key: export-job-20260921-001
```

`Idempotency-Key` 可省略；有傳時必須等於 body 的 `operation_id`。後端請求只使用 HTTPS。不得將扣款憑證放進桌面程式、行動 App、網頁 JavaScript、localStorage 或公開儲存庫。沒有可信任後端的 App，先建置後端代理，不要把企業扣款憑證發給終端使用者。

所有成功回應為 `{ "ok": true, "data": ..., "request_id": "..." }`；錯誤為 `{ "ok": false, "code": "...", "error": "...", "request_id": "..." }`。回應不快取。JSON 最大 32 KB，數量為整數，不接受未定義欄位。

## 4. 端點

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | `/wallet` | 綁定公司的帳面餘額、有效預留、可用餘額 |
| GET | `/rates` | 憑證可用功能及目前核定費率 |
| POST | `/charges` | 一次直接扣款，原子寫入餘額、用量及帳本 |
| POST | `/usage/reservations` | 執行付費工作前預留額度，不立即減少帳面餘額 |
| POST | `/usage/reservations/{id}/commit` | 依實際數量結算並釋放剩餘預留 |
| POST | `/usage/reservations/{id}/cancel` | 取消尚未結算的預留，不是已扣款退款 |
| GET | `/usage/operations/{operation_id}` | 查詢本憑證的一筆操作 |
| GET | `/usage/operations?page=1` | 本憑證的操作紀錄，每頁 25 筆 |

### 餘額與費率

`GET /wallet` 的 data：

```json
{
  "company_id": "綁定公司ID",
  "balance": 1000,
  "reserved": 80,
  "available": 920,
  "unit_code": "platform_token",
  "checked_at": "2026-09-21T10:00:00.000Z"
}
```

`reserved` 同時包括 AI 與 App 的有效預留；顯示的餘額不是扣款許可，最終以原子扣款／預留結果為準。`GET /rates` 的 data 是陣列，各項包含 `toolKey`、`displayName`、`unitLabel`、`tokensPerUnit`、`maxQuantity`、`version`。

### 一次扣款

`POST /charges`：

```json
{
  "operation_id": "export-job-20260921-001",
  "tool_key": "document-export",
  "quantity": 3,
  "actor_ref": "employee-12",
  "summary": "匯出三份文件"
}
```

`operation_id` 在同一憑證內唯一，允許英數字及 `_.:-`，最長 128 字元。`tool_key` 必須是管理員已核定的功能代碼。數量 1 至 1,000,000，亦受核定數量、單次金額、每日金額及餘額限制。`actor_ref`（最多 120 字）、`summary`（最多 300 字）可省略。

`actor_ref` 是產品後端提供的操作者參考，不宣稱是 Website 驗證的使用者。請勿放入密碼、金鑰或非必要個資。直接扣款適用產品後端能確認應計費的事件；可能執行失敗或數量不確定的工作，優先使用下列預留流程。

### 預留、執行、結算

1. `POST /usage/reservations`：

```json
{
  "operation_id": "export-job-20260921-002",
  "tool_key": "document-export",
  "estimated_quantity": 8,
  "expires_in_seconds": 900,
  "actor_ref": "employee-12",
  "summary": "批次文件匯出"
}
```

2. 保存回傳 `reservation_id`。取得 `reserved` 狀態後才開始工作。效期預設 900 秒，60 至 3,600 秒；尚無延長 API，工作必須在效期內完成結算。
3. 若完成 3 份，呼叫 `POST /usage/reservations/{reservation_id}/commit`：

```json
{ "operation_id": "export-job-20260921-002", "actual_quantity": 3 }
```

4. 若未產生成本，呼叫 `POST /usage/reservations/{reservation_id}/cancel`：

```json
{ "operation_id": "export-job-20260921-002", "reason": "工作取消，未產生費用" }
```

`actual_quantity` 可為 0，但不能超過預留數量；不會自動追加扣款。若全部未執行，建議取消。到期額度立即不再占用可用餘額，操作查詢顯示 `expired`，不得再結算。撤銷／過期的憑證無法繼續結算，因此輪替憑證前先處理進行中的操作；未結算預留最遲一小時自動失效。

### 操作回應

```json
{
  "operation_id": "export-job-20260921-002",
  "reservation_id": "reservation-id",
  "status": "committed",
  "product_key": "document-app",
  "tool_key": "document-export",
  "estimated_quantity": 8,
  "actual_quantity": 3,
  "unit_label": "份",
  "tokens_per_unit": 10,
  "price_version": 1,
  "reserved_units": 80,
  "charged_units": 30,
  "released_units": 50,
  "usage_id": "usage-id",
  "ledger_entry_id": "ledger-id",
  "expires_at": "2026-09-21T10:15:00.000Z",
  "created_at": "2026-09-21T10:00:00.000Z"
}
```

`status` 為 `reserved`、`committed`、`cancelled`、`expired`。未結算時 `usage_id`、`ledger_entry_id`、`actual_quantity` 為 null，實扣為 0。`reserved_units` 保留原始預留數量供對帳，不代表終結後仍占用。

## 5. 安全重試與錯誤

- 同憑證、同 `operation_id`、同內容重送回傳同一操作，不再扣款；同鍵不同內容回 409。重試不要更動摘要或操作者。
- 請求逾時／503 時，先 GET 操作狀態，再用原編號與原內容重試。不要改新編號來重試同一筆工作。
- 未知狀態禁止宣告扣款成功。只有 `committed` 且取得用量及帳本編號才完成。
- `401 INVALID_CREDENTIAL`：缺少、無效、到期或已撤銷憑證。
- `402 INSUFFICIENT_BALANCE`：共用可用餘額不足。
- `403`：公司停用、功能未核准、單次或每日額度不足。
- `404 OPERATION_NOT_FOUND`：不存在或不屬於這個憑證的操作。
- `409`：冪等衝突、操作已取消／結算、預留到期或數量超額。
- `400 INVALID_REQUEST`／`INVALID_JSON`、`413 BODY_TOO_LARGE`：修正請求後重送，未扣款。
- `503 BILLING_UNAVAILABLE`：保留 `request_id`，查詢原操作並稍後重試。

已結算操作不能透過 cancel 退款；本版未開放退款 API。需退款時由平台管理員按既有帳務程序處理，不能直接改寫原紀錄。

## 6. 使用者看到什麼

Website 企業 Profile →「查看完整使用紀錄」，可看到兩種真實已入帳紀錄：

- 藍色「AI 用量」：來源（Interact AI／API／有可信來源欄位的 CRM 或 LINE）、工作流用途、模型、使用者等。
- 綠色「功能扣款」：產品名稱、功能用途、產品操作者、扣款 Token；明細顯示數量、核定費率版本、操作編號、帳本編號。

非 AI 記錄不偽造 input/output token，也不套用模型費率推估新台幣費用。CSV 的未知新台幣金額留空。預留、取消與到期本身沒有實扣，不新增成扣款紀錄；產品後端可透過操作查詢取得狀態。既有歷史紀錄缺少產品／操作者證據時不推測補寫。

## 7. 驗收與上線

整合先以測試公司、核定測試額度驗收：成功扣款、同鍵重送、同鍵不同內容、餘額不足、部分結算、取消、到期、跨憑證拒絕、費率改版、撤銷金鑰、Profile badge 與帳本對帳。不要使用客戶正式餘額做假扣款測試。

管理員必須先建立各 App 的實際功能費率與專屬後端憑證。完成 API 不等於既有 App 自動接上；App 後端仍須依本文件串接。
