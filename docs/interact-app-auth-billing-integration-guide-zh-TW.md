# Interact 平台 AI API 與計費串接手冊

> **平台定位：Website + Interact AI 是一套完整系統。** Website 是主要入口及帳號、授權、共用錢包與帳務管理端；Interact AI 是平台內的 AI 執行與 API Key 功能，不是獨立產品。CRM／其他 App 的 AI 功能以 Interact AI 產出的 API Key 存取並計量，再由 Website 結算；不經過 AI 的付費功能則由 Website 提供直接扣款 API。

> **本手冊記錄現行 AI API 合約。** 其中帳密登入與 API Key 端點屬於 `ai.interact-vision.com.tw`，不是 Website 密碼登入 API。Website SSO、公司資訊與產品授權已於 2026-09-22 另行發布，請用 [FormAI Website SSO 修改手冊](./formai-website-sso-integration-zh-TW.md)，不要把 Website 密碼送進本手冊的 AI 登入端點。不經 AI 的扣款見 [Website 功能扣款 API 手冊](./interact-website-function-billing-api-zh-TW.md)。CRM 既有 Agent Token 例外見第 8 節。

更新日期：2026-09-22  
適用服務：

- 主站台：`https://interact-vision.com.tw`
- AI API：`https://ai.interact-vision.com.tw/api/v1`

## 1. 先看結論

CRM／其他 App 的目標 AI 串接入口是 Interact AI API Key。現行 AI API 支援下列兩種憑證；它們不等同 App 本身的 Website 登入或非 AI 扣款授權：

1. **使用者 API Key，建議正式 App 採用**
   - 使用者先在 Interact AI 建立自己的 API Key。
   - App 後端使用 API Key 呼叫模型清單及聊天 API。
   - 每次推論都會自動歸屬到該使用者及其主站台企業帳號，照實際用量扣款。
2. **現有 Interact AI 帳密換 JWT，供既有受信任整合使用**
   - App 後端呼叫登入端點取得 JWT。
   - JWT 可呼叫使用者 API、建立 API Key、查模型及執行推論。
   - 正式前端不要直接保存使用者密碼；建議由 App 後端代理。

新 App 的一般登入依 Website 直連規格建置，AI 存取採第一種方式；本節保留第二種現有合約供相容性使用。兩種 AI 憑證都不能拿來直接呼叫通用扣款 API。Website 自身及其他 App 即使完全不呼叫 AI，仍可有付費功能，請依已發布的主站功能扣款合約串接。

2026-09-22 已提供獨立的 Website 產品 SSO 合約：

- `https://interact-vision.com.tw/api/auth/*` 是主站台網頁自己的 NextAuth 工作階段。
- OIDC issuer 為 `https://interact-vision.com.tw/sso`，discovery 為 `/sso/.well-known/openid-configuration`，採 Authorization Code + PKCE。
- 其他 App 不應直接呼叫 `company-credentials` callback，也不應讀取或共用主站台 Cookie。
- 已登入 Website 的使用者可在授權頁確認後返回已註冊 App；各 App 仍需實作標準 OIDC 回呼及 Token 驗證，不會因主站發布而自動完成 App 改版。

## 2. 系統角色與責任

| 元件 | 責任 |
| --- | --- |
| Website（平台管理與帳務） | 主要使用入口、企業帳號、平台方案、共用餘額及帳務；提供產品 SSO、`/sso/app/me`、entitlement、租約，以及分開授權的後端功能扣款 API |
| Interact AI（平台 AI 功能） | 現有 AI 工作階段、API Key、模型 ACL、實際模型執行、token 彙整，並呼叫 Website 內部帳務合約 |
| CRM／其他 App（延伸產品） | 保存自己的工作階段與受限憑證；AI 使用 Interact AI API Key，非 AI 付費功能使用 Website 扣款授權；處理錯誤與顯示用量 |

外部 App **不應自行計算台幣費用，也不應直接修改餘額**。主站台的結算紀錄才是帳務依據。

這是同一平台內的技術分工。公司不需另外購買一個 Interact AI 產品或儲值第二個錢包；平台既有 AI 功能資格、模型權限與配額仍須遵守。

## 3. 建議架構

```text
CRM／其他 App 的 AI 功能
        |
        | Interact AI 產出的 API Key
        | （既有 JWT／CRM Agent Token 整合依現行權限相容）
        v
https://ai.interact-vision.com.tw/api/v1
        |
        | 1. 解析使用者與 API Key
        | 2. 檢查模型 ACL
        | 3. 向主站台預扣
        | 4. 執行模型
        | 5. 依實際 token 結算
        v
https://interact-vision.com.tw

Website／CRM／其他 App 的非 AI 付費功能（待實作通用合約）
        |
        | Website 核發的產品範圍授權
        v
Website 直接扣款 API → 同一企業錢包、預扣／結算及帳本
```

使用者在 Website 統一管理儲值、餘額與用量。AI 用量由 Interact AI 回報，非 AI 用量由受信任產品後端回報；同一次 AI 推論不能再經直接扣款 API 重扣。若另收 App 服務費，須事先定價並在帳本明確區分。

不要讓瀏覽器或行動 App 直接持有主站台 `Service Token`。Service Token 只能存在受控伺服器的秘密管理系統。

## 4. 方案 A：使用者 API Key

### 4.1 建立 API Key

使用者可從 Interact AI 的「設定 → 帳號 → API 存取」建立金鑰。

目前規則：

- 每位使用者最多 10 把有效金鑰。
- 到期日可選 30、90、365 天或永不到期。
- 金鑰只在建立時顯示一次，資料庫只保存雜湊與遮罩資訊。
- 固定 scope 為 `models:read`、`chat:write`。
- 正式環境已啟用一般使用者建立 API Key 的權限。

也可先用 JWT 呼叫建立端點：

```http
POST https://ai.interact-vision.com.tw/api/v1/auths/api_keys
Authorization: Bearer <USER_JWT>
Content-Type: application/json

{
  "name": "my-crm-production",
  "expires_in_days": 90
}
```

成功回傳：

```json
{
  "id": "key-record-id",
  "name": "my-crm-production",
  "prefix": "sk-xxxxxxxx",
  "last_four": "1234",
  "scopes": ["models:read", "chat:write"],
  "expires_at": 1790000000,
  "last_used_at": null,
  "created_at": 1780000000,
  "api_key": "sk-完整金鑰只會在這次回傳"
}
```

列出與撤銷金鑰：

```http
GET /api/v1/auths/api_keys
DELETE /api/v1/auths/api_keys/{key_id}
Authorization: Bearer <USER_JWT>
```

### 4.2 取得可用模型

```bash
curl "https://ai.interact-vision.com.tw/api/v1/models" \
  -H "Authorization: Bearer $INTERACT_API_KEY"
```

只使用實際回傳的 `data[].id`。不要猜測供應商模型名稱，也不要把模型 ID 永久寫死。

模型資料中的 `interact` 欄位會標示：

- `source = workspace`：工作區模型，可套用其已核准的知識庫、工具、技能與篩選器。
- `source = enterprise_base`：企業授權的原始模型。
- `capabilities`：API 呼叫時可用的能力摘要。

### 4.3 呼叫聊天 API

```bash
curl "https://ai.interact-vision.com.tw/api/v1/chat/completions" \
  -H "Authorization: Bearer $INTERACT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "從-models-回傳值選擇",
    "messages": [
      {"role": "user", "content": "請整理本週待辦"}
    ],
    "stream": false,
    "max_completion_tokens": 1200
  }'
```

Python：

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["INTERACT_API_KEY"],
    base_url="https://ai.interact-vision.com.tw/api/v1",
)

models = client.models.list()
response = client.chat.completions.create(
    model=models.data[0].id,
    messages=[{"role": "user", "content": "請整理本週待辦"}],
    max_completion_tokens=1200,
)
print(response.choices[0].message.content)
```

API Key 目前只允許：

- `GET /api/models`
- `GET /api/v1/models`
- `POST /api/chat/completions`
- `POST /api/v1/chat/completions`

工作流、使用者、內部工具、檔案、Channel 與管理端點會拒絕 API Key 存取。

## 5. 方案 B：帳密登入並取得 JWT

這是平台內**現有 AI 功能的登入端點**，尚不能替代 Website 通用 SSO。計費時會將 AI 使用者對映到主站企業帳務；同一平台的產品定位不代表目前的帳密與工作階段已可互換。

### 5.1 登入

```http
POST https://ai.interact-vision.com.tw/api/v1/auths/signin
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "使用者密碼"
}
```

成功回傳：

```json
{
  "token": "JWT",
  "token_type": "Bearer",
  "expires_at": 1790000000,
  "id": "webui-user-id",
  "email": "user@example.com",
  "name": "使用者名稱",
  "role": "user",
  "profile_image_url": "/api/v1/users/.../profile/image",
  "permissions": {}
}
```

目前正式環境 JWT 期限為 4 週。App 不應假設期限固定，應以 `expires_at` 為準。

外部 App 的**登入保存期最長為 10 天**，即使平台回傳的 JWT 尚未到期，也不得讓同一個 App 工作階段持續超過 10 天。有效登入截止時間應取以下兩者較早者：

```text
effective_login_expires_at = min(expires_at, signed_in_at + 10 天)
```

到達有效登入截止時間後，App 必須清除本機與後端工作階段，要求使用者重新登入；目前沒有 Refresh Token 可延長工作階段。這是外部 App 的安全上限，不代表平台 JWT 的原始期限已改為 10 天。

### 5.2 讀取登入狀態

```http
GET https://ai.interact-vision.com.tw/api/v1/auths/
Authorization: Bearer <USER_JWT>
```

### 5.3 登出

```http
POST https://ai.interact-vision.com.tw/api/v1/auths/signout
Authorization: Bearer <USER_JWT>
```

登出會使該 JWT 失效。不要只在 App 本機刪除 token。

### 5.4 前端安全限制

- Web App 建議使用 Backend for Frontend，由 App 後端持有 JWT 或 API Key。
- 不要把 API Key 放進 JavaScript bundle、公開環境變數、網址 query 或 localStorage。
- 原生 App 應使用 Keychain／Keystore；但正式整合仍優先採用短效授權碼流程，而非長期保存密碼。
- 正式環境目前允許跨來源 API 請求，但 Cookie 與 `Access-Control-Allow-Origin: *` 的組合不適合作為跨站登入設計；跨站呼叫請使用 Bearer token。
- 正式環境禁止自助註冊，帳號必須先由既有管理流程建立或同步。

## 6. 計費流程

每次受計費推論依序執行：

```text
1. 驗證 JWT／API Key
2. 檢查使用者、企業與模型權限
3. 估算本次最高可能用量
4. 主站台建立 reservation 並預留企業 Token
5. 執行模型、工具與必要的內部推論
6. 彙整供應商回傳的實際 usage
7. commit 實際用量並釋放未使用的預留額度
```

目前內部計費權重為：

```text
billable_tokens = input_tokens + output_tokens * 6 + compute_tokens * 6
```

這只是平台的計費 token 權重，不等於 App 應顯示的台幣金額。台幣單價、模型費率與最終金額由主站台結算規則決定。

### 6.1 自有 API Key／自架模型

使用者改用自己的 OpenAI、Gemini 或相容模型憑證時，仍會走同一層平台計費：

- 呼叫前仍會預扣。
- 完成後仍依實際 input／output／compute token 結算。
- API Key ID、使用者、企業、模型與使用渠道仍會寫入用量紀錄。
- 自有供應商憑證只改變模型的上游來源，不會繞過平台費用。

### 6.2 失敗與中斷

| 狀況 | 計費處理 |
| --- | --- |
| 模型尚未執行就失敗 | 取消 reservation，不扣模型用量 |
| 模型已執行後失敗 | 以已發生的實際或保守估算用量結算，狀態為 `failed` |
| Streaming 被使用者中斷 | 結算中斷前已產生的用量，狀態為 `cancelled` |
| 主站台暫時無法接收 commit | WebUI 將結算保存於本機佇列並重試，不直接抹除已發生費用 |

## 7. 平台內部 AI 結算與 Website 直接扣款

以下端點是同一平台內，Interact AI 呼叫 Website 帳務的服務對服務介面：

```text
POST /api/integrations/open-webui/users/resolve
GET  /api/integrations/open-webui/users/{companyUserId}/wallet
POST /api/integrations/open-webui/usage/authorize
POST /api/integrations/open-webui/usage/commit
POST /api/integrations/open-webui/usage/cancel
```

它們使用伺服器端 `Service Token`，目前是 `open-webui` 專用合約。其他 App 不應：

- 共用 WebUI 的 Service Token。
- 從瀏覽器或行動 App 直接呼叫。
- 略過 `authorize` 直接 `commit`。
- 自己編造 `company_user_id`、`company_member_id` 或 token 用量。

如果新 App 只需要 AI 功能，請呼叫第 4 節的受計費 AI API，扣款會自動完成。

如果 Website、CRM 或其他 App 有非 AI 收費項目，主站台必須提供產品 client、工具、費率、餘額查詢、預扣／結算／取消與操作回查合約，詳見 [Website 直接扣款規格](./interact-website-app-integration-target-zh-TW.md#4-餘額與-app-功能用量扣款現在就需要)。外部產品使用自身的受限授權，不能沿用 `open-webui` 的 Service Token。此合約是必要的待開發能力，無須先呼叫模型或建立 AI Key。

兩種用量必須共用主站企業錢包及原子預留檢查，並集中在 Website 查帳。新增直接扣款時須驗證與既有 AI 預扣並發不超扣；保留用量類別、產品／工具、操作者、費率與操作 ID。相同模型用量只由 AI 結算一次。

## 8. 企業綁定與 CRM 模式

CRM 目前保留專用的企業綁定與 Agent Token 相容流程：

```text
GET  /company-portal/integrations/connect
POST /api/integrations/crm/bind/exchange
POST /api/integrations/crm/token
POST /api/integrations/crm/revoke
```

流程包含 state、PKCE、一次性 code、App client ID／secret 與短效 Agent Token。這套流程目前：

- 只註冊 `crm` 產品。
- 用於企業綁定與 CRM 對 WebUI 的服務呼叫。
- 不是一般使用者登入，也不能直接複製成其他產品名稱使用。

**目標方向：** CRM 與其他 App 的 AI 存取使用 Interact AI 產出的 API Key，AI 費用回到同一 Website 錢包。現有 CRM 尚未全面改成 API Key；公開 Key 目前只支援模型與聊天，無法直接取代全部工作流及 Agent scope，因此遷移須先完成權限與功能驗收。本次文件修正不更換現有 CRM 憑證、不要求本機使用者逐一建立主站帳號，也不影響 `service@chengsyin.com` 的既有連線。

新 App 的使用者登入、產品授權與非 AI 扣款依 [Website 直連版規格](./interact-website-app-integration-target-zh-TW.md) 建置；這些需求不必複製一套 CRM 專用綁定流程。

## 9. 錯誤處理

| HTTP | 常見原因 | App 建議 |
| --- | --- | --- |
| `400` | 請求格式錯誤、模型不存在、上游錯誤 | 顯示可理解訊息，不盲目重試 |
| `401` | JWT／API Key 錯誤、過期或撤銷 | 清除工作階段並重新登入／換 Key |
| `402` | 餘額不足、企業停用、計費授權失敗 | 引導至主站台儲值或聯絡管理員 |
| `403` | scope、模型 ACL 或帳號權限不足 | 不重試，請管理員調整權限 |
| `404` | 直接 provider proxy 被封鎖，或資源不存在 | 改用 `/api/v1/chat/completions` |
| `409` | API Key 數量已達上限 | 先撤銷不用的 Key |
| `429` | 登入或服務頻率限制 | 指數退避後重試 |
| `5xx` | 服務暫時異常 | 記錄 request 時間與摘要，再有限次重試 |

### 9.1 重試與重複扣款

目前公開聊天 API 尚未接受由呼叫端指定的 `Idempotency-Key`。因此：

- 收到明確 `4xx` 不要重試。
- 連線逾時但不知道伺服器是否已執行時，不要立刻用相同內容無限重送。
- App 應保存自己的 operation ID、送出時間與結果狀態。
- 需要金流等級的安全重試時，應先擴充公開 API 的 idempotency key，再啟用自動重送。

## 10. 最小驗收清單

正式 App 上線前至少驗證：

1. 錯誤、過期與撤銷 API Key 都回 `401`。
2. API Key 無法存取模型與聊天以外的內部端點。
3. `/models` 只回傳該使用者有權使用的模型。
4. 餘額不足時，推論在執行前回 `402`。
5. 成功推論後，主站台用量紀錄包含正確企業、使用者、API Key ID、模型與 token。
6. 使用自有供應商 API Key 時仍產生平台用量與扣款。
7. Provider 執行後才失敗時仍會留下 `failed` 用量。
8. Provider 尚未執行的錯誤不會產生模型用量。
9. Streaming 中途斷線會結算已產生 token。
10. App 不會在 log、錯誤追蹤、瀏覽器或分析工具中洩漏完整 JWT／API Key。

## 11. 目前已知限制

- 主站台尚未提供通用 OIDC／OAuth SSO，不能讓其他 App 直接共用主站登入狀態。
- 既有主站扣款端點是 WebUI 專用的服務合約，不是公開的通用扣款 SDK。
- Website 直接扣款 API 仍須實作；AI／非 AI 共用帳務的整合目標不代表新 API 已發布。
- 現有 CRM Agent Token 尚未全面遷移成 API Key；不得直接替換而造成工作流或 Agent 權限失效。
- 公開聊天 API 尚無呼叫端 idempotency key。
- Interact AI 內建下載 Tour 目前可能因持久化 `webui.url` 設定而顯示 `localhost`；本手冊所列正式 Base URL 才是正確值。
- API Key 的 scope 目前是固定集合，尚不能由使用者自行縮小到單一模型。

## 12. 原始碼核對位置

- 登入與 API Key：`backend/open_webui/routers/auths.py`
- Bearer／API Key scope 驗證：`backend/open_webui/utils/auth.py`
- 模型清單與聊天入口：`backend/open_webui/main.py`
- 主站預扣、結算、取消與用量權重：`backend/open_webui/utils/interact_billing.py`
- 延遲結算佇列：`backend/open_webui/models/interact_billing.py`
- CRM 企業綁定範例：`manufacturing-crm/src/lib/platform-integration.ts`
