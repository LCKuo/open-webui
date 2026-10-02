# Interact Website 直連 App 整合規格（交付範圍與後續規劃）

更新日期：2026-09-22

> **狀態：Website SSO 與產品授權已於 2026-09-22 發布；其餘標為擬議的完整模型仍是目標規格。** 正式 OIDC issuer 為 `https://interact-vision.com.tw/sso`，身分、entitlement 與租約實際使用 `/sso/app/*`，請依[FormAI Website SSO 修改手冊](./formai-website-sso-integration-zh-TW.md)及 discovery 串接，不要照本文舊擬議路徑直接呼叫。通用功能扣款以[功能扣款 API 手冊](./interact-website-function-billing-api-zh-TW.md)為準。App 不收取主站密碼或重用主站 Cookie；本次沒有修改 FormAI 原始碼。

多產品的授權資料模型、平台 admin 與客戶自助介面，以及 CRM 漸進遷移方案，另見[多產品授權與 Admin 管理規劃](./interact-product-licensing-admin-plan-zh-TW.md)。

**固定產品原則：Website + Interact AI 是同一套 Interact 平台。** Website 是使用者最常接觸的主入口，管理公司帳號、平台方案、延伸產品授權、共用錢包及所有用量帳務；Interact AI 是平台內負責 API Key、模型權限、推論與 AI 用量彙整的功能，不另列成獨立產品或建立第二份訂閱。App 型延伸產品使用 Website 帳號登入，並受產品自己的同時使用人數上限約束；CRM 等 Web SaaS 則在產品內自行新增不限數量的本機使用者，**不為每位 CRM 使用者配置主站席位**。

CRM／其他 App 的 AI 用量，目標是使用 **Interact AI 產出的 API Key** 呼叫 AI，再由平台內部交給 Website 結算。Website 同時必須提供可由產品後端直接使用的扣款 API，支援不經過 AI 的付費功能；這是本次規格的必要能力，不能以「已有 AI API」取代。登入、AI 存取及直接扣款各有適用憑證，但公司、授權與帳務仍由同一平台管理。

## 1. 系統邊界與交付狀態

| 能力 | 現況 | 目標責任 |
| --- | --- | --- |
| Website 既有帳號登入新 App | 已提供 OIDC Code + PKCE；FormAI 需依新手冊修改 App | Website 發行可驗證、可撤銷的 App 授權；新 App 不接觸主站密碼 |
| 帳號、公司、成員角色查詢 | 已提供 `GET /sso/app/me`；非本文下方舊擬議 JSON | Website 提供與登入主體綁定的最小必要資料 |
| SaaS 到期日與產品授權 | 已提供新產品 `GET /sso/app/entitlements` 及租約；CRM 未遷移至新模型 | Website 提供產品 entitlement；Web SaaS 內部使用者不限，App 另檢查同時使用上限 |
| Token 餘額、預留與用量紀錄 | 已實作公司／產品綁定的扣款憑證及查詢 API；**不得沿用 AI Service Token** | 與 AI 結算共用企業錢包；使用者 OAuth 授權型式仍待後續擴充 |
| AI 推論與 AI 用量扣款 | Interact AI 使用者 API Key 可用；現行 Website 與 AI 的登入憑證尚不能互換 | CRM／其他 App 使用 Interact AI 產出的 API Key，AI 用量交由 Website 結算；現有 CRM Agent Token 保留過渡相容 |
| 不經過 AI 的付費功能 | 已實作直接扣款、預留／結算／取消、冪等及對帳；見正式合約手冊 | Website 直接提供受限後端扣款 API，不要求 AI Key、不必先呼叫模型 |

下文以 `https://interact-vision.com.tw` 為主站。標為 **[擬議]** 的路徑、欄位、scope 與錯誤碼須由主站與 App 雙方實作、確認並版本化後才能當成正式規格。現有 AI 串接詳見 [Interact AI 手冊](./interact-app-auth-billing-integration-guide-zh-TW.md)。

### 1.1 統一平台的兩種用量路徑

```text
使用者主要入口：Website
  → 管理公司／授權／儲值／共用餘額／AI 與非 AI 用量

CRM／其他 App 的 AI 功能
  → Interact AI API Key → 平台 AI 執行 → Website 預扣／結算

Website／CRM／其他 App 的非 AI 付費功能
  → Website 扣款授權 → Website 預扣 → 執行功能 → Website 結算／取消
```

兩條路徑共用 Website 帳務與企業錢包。Website 頁面自己的付費功能亦應使用同一帳務服務，不另寫一套直接減餘額的邏輯。AI API Key 只用來存取獲准的 AI API；直接扣款須持 Website 核發的產品範圍授權。文件中的路徑分工不代表使用者要另外購買一套 Interact AI 產品。

### 1.2 本次修改前的正式環境查核（2026-09-21）

- CRM 現行路徑為「向 Website 取得短效 Agent Token → 呼叫 Interact AI → Interact AI 向 Website 預扣／結算」。已經經過 Interact AI 的 API 計量，但憑證不是使用者自行建立的 API Key。
- 唯讀查核 `service@chengsyin.com` 的正式資料：現有使用紀錄的工具類別皆為 `ai-chat`。抽查最近 12 筆，其使用紀錄與對應扣款帳本金額一致；例如台灣時間 2026-09-21 21:27 的「CRM AI 潛在客戶探索」為 41,219 計價 Token，帳本為 -41,219。這是既有紀錄抽查，未另發起收費操作。
- Website 企業入口的使用紀錄讀取這份帳務資料。正式前端已有來源、模型、工作流、使用者及內容的 badge，但尚未提供本文要求的完整「AI 用量／功能扣款」分類；部分 CRM 工作流只顯示 Interact AI 來源，API Key 呼叫的來源亦可能直接顯示內部代碼。
- 正式部署尚無本文件擬議的 `/api/app-integrations/v1` 通用合約。因此第二種「延伸 App 不呼叫 AI、直接要求 Website 扣款」及其完整 badge，仍屬待實作項目。

### 1.3 本次交付（2026-09-21 已發布）

針對 1.2 的缺口，新增 `/api/app-integrations/v1` 功能扣款端點、Admin「功能扣款 API」管理入口，以及企業使用紀錄的「AI 用量／功能扣款」分類、來源與用途標籤。採公司與產品綁定的後端憑證；公司、價格不能由呼叫端任意指定。保留現有 CRM Agent Token，不遷移客戶登入或 AI 授權。精確欄位、操作流程、錯誤碼與限制請看[功能扣款 API 手冊](./interact-website-function-billing-api-zh-TW.md)。

2026-09-22 已另交付使用者 OIDC、`/sso/app/me`、entitlement、租約及產品授權管理；精確合約以新的 FormAI 手冊為準。第 4 節的舊擬議 URL、退款及泛用帳本游標並未因此全部實作。扣款操作查詢僅回傳該憑證自己的操作；企業總用量仍從 Website 使用紀錄查看。SSO access token 不代替 `iwb_` 後端扣款憑證。

## 2. App 登入：Website 是 App 使用者帳號來源

本節只適用採 Website 帳號登入的 App 型產品。CRM 與其他可自行建立本機使用者的 Web SaaS **不需要把每位本機使用者改成 Website 帳號**；產品實例在主站綁定公司、查詢公司授權即可，內部帳號與工作角色仍由產品管理。

### 2.1 目標流程

1. 平台管理員註冊 App：`client_id`、產品代碼、正式 callback allowlist、允許的 scope、登出網址與密鑰輪替方式。
2. App 將使用者導向 Website 的授權頁；使用者只在 Website 輸入既有帳密。使用 Authorization Code + PKCE（S256）、`state`、`nonce`；禁止 App 收集或轉送密碼。
3. Website 驗證帳號、企業歸屬與 App 授權後，將一次性 code 導回已註冊 callback；App 後端換取 ID token、access token。核對簽章、`iss`、`aud`、`exp`、`nonce`、`state`，以穩定的 `sub` 綁定本地帳號，**不以 Email 當主鍵**。
4. App 後端保存 token 與自己的 HttpOnly、Secure、SameSite 工作階段；瀏覽器不保存主站密碼、Service Token、AI API Key。使用者可切換有權限的企業，但每次切換須由 Website 確認授權，不接受任意填入公司 ID。
5. App 工作階段最長 10 天，且不得長於 Website token／授權有效期；登出、撤銷、成員被移除或產品停用時提前失效。逾期要求重新登入，不默默延長。

主站至少要提供 **[擬議]** discovery、授權、換 token、公開金鑰、userinfo、撤銷／登出等標準能力。正式路徑和 token 格式應由主站發布的 discovery 文件決定，不在 App 寫死。遵循 [OAuth 安全最佳實務](https://www.rfc-editor.org/rfc/rfc9700.html)、[PKCE](https://www.rfc-editor.org/rfc/rfc7636.html)、[授權服務 metadata](https://www.rfc-editor.org/rfc/rfc8414.html) 與 [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0-18.html)。

**禁止捷徑：** 不可呼叫 NextAuth 的 `company-credentials` callback、重用主站 Cookie、讀取主站密碼雜湊、讓 App 直連主站資料庫，或把 Interact AI 的 `/api/v1/auths/signin` 當作 Website 帳密驗證。

### 2.2 身分與帳號資訊合約

主站應提供 **[擬議]** `GET /api/app-integrations/v1/me`，以 Website 發出的 App access token 辨識使用者，回傳最小必要資訊：

```json
{
  "subject": "stable-website-user-id",
  "email": "user@example.com",
  "email_verified": true,
  "display_name": "使用者名稱",
  "account_status": "active",
  "memberships": [
    {
      "company_id": "stable-company-id",
      "company_name": "公司名稱",
      "member_id": "stable-member-id",
      "role": "member",
      "status": "active"
    }
  ]
}
```

Website 負責判定 Email 是否驗證、帳號是否停權、成員是否仍屬該企業。稅籍、電話、帳單地址等個資應拆成額外 scope，沒有業務需求就不回傳。App 不可根據 Email 自行推定公司或擁有者角色；用穩定 ID 關聯、用權限變更事件或短 TTL 重新查詢。

## 3. 產品授權與公司狀態

平台方案與產品授權由主站 Admin 側邊欄的**授權中心**集中修改，分成「平台方案與 AI 功能」及「CRM／其他產品授權」。`Company User` 保留公司資料、成員身分、共用錢包／儲值／用量，並只顯示授權摘要與跳轉；不得再保留另一套可獨立存檔的授權表單。Web SaaS 的產品內帳號不占主站席位；API Key 由平台內的 Interact AI 功能發行與管理，不為 Interact AI 另建延伸產品授權。

企業 Profile 既有的**系統整合**頁是連線管理入口，不是第二個授權中心：顯示產品實例、綁定狀態、連線 scope、最近使用，以及解除／重新綁定。CRM 目前的綁定仍由 CRM 端發起、主站確認；解除綁定僅撤銷新 Token，不應刪除 CRM 業務資料或產品授權。App 的 Website OAuth 登入不應再要求客戶手動綁定一次；日後只把已授權的 App 連線／撤銷紀錄列在此處。方案是否有效仍由獨立 entitlement 判定，不能從「已連線」推論「已有授權」。

主站應提供 **[擬議]** `GET /api/app-integrations/v1/entitlements?product_key=<product>`。回應至少包含 `product_key`、`company_id`、`license_policy`（`instance_unlimited_users` 或 `concurrent_users`）、`status`（trial/active/past_due/suspended/expired/cancelled）、`starts_at`、`expires_at`、`grace_until`、`plan_code`、`features`、`limits`、`version`、`checked_at`。App 的 `limits` 包含 `max_concurrent_users`；Web SaaS 不包含人數上限。`expires_at` 才是可程式執行的 SaaS 到期依據；文件或人工約定的日期不能取代它。

網站須定義各狀態可用的操作，包含唯讀寬限期與舊資料匯出。App 每次建立工作階段、切換公司、執行付費操作前都要檢查；Web SaaS 由已綁定產品實例查公司授權，不逐一檢查本機使用者席位。其他操作可短暫快取，但停用／撤銷須有事件通知或短 TTL，不能只在登入時檢查一次。授權查詢不可用時，新的收費操作應失敗關閉（fail closed），不能當成仍有效。

App 的「同時使用人數」應計算不同活躍使用者，而非已建立帳號數、瀏覽器分頁數或 10 天登入保存期。主站須提供短效、可續租及可釋放的**原子使用租約**；同一人多裝置算一人，斷線到期回收。這是待實作能力，現有 API 尚不能保證限制人數。此策略適用於採同時使用限制的延伸 App；Interact AI 為平台功能，不另增加一份產品租約。現有 AI 子帳號及功能配額按原意保留，不在此次文件修正中改成同時在線人數。

## 4. 餘額與 App 功能用量扣款（現在就需要）

本 App 目前不使用 LLM，**不代表免費或不需串計費**。例如某個付費功能的執行次數、處理量、產出份數或其他可稽核事件，應由產品定價表轉為 Website 的扣款單位；計量項目須逐項與業務確認。這不是 AI input／output token，也不可假裝成 `ai-chat` 用量。免費操作不得建立扣款；同一次功能操作必須有唯一 `operation_id`。

此合約同時服務 Website 自身與延伸產品的不經 AI 付費功能。使用者可在 Website 統一儲值、查詢餘額及對帳，不必先開啟 Interact AI 或取得 AI Key。外部產品仍須註冊自己的 client、可扣款工具與費率，取得被限制的扣款權限後才可使用。

### 4.1 查詢與定價

主站應提供 **[擬議]** `GET /api/app-integrations/v1/wallet?company_id=<id>`，回應 `balance`、`reserved`、`available`、`unit_code`、`updated_at` 與 `ledger_cursor`。只有主站是餘額與帳務的權威來源；App 的顯示值不可拿來決定是否能扣款。

平台先註冊每個 App 的 `product_key`、`tool_key`、可計量事件、計價單位、費率版本、預扣上限、有效期限及可執行角色。App 只回報經核定的事件和實際數量；**費率由主站決定並在結算時保存快照**，不能讓 App 自己上傳價格或自行改餘額。預設沿用同一企業在 Website 的錢包，回應與帳本帶穩定錢包 ID，不因呼叫來自 AI 或其他 App 而建立不同餘額，也不能只用 Email 判斷歸屬。

AI 內部結算與直接扣款必須共用原子預扣／餘額檢查：新合約的 `available` 須考量既有 AI 預留，AI 預扣也須考量非 AI 預留。以現行帳務實際儲存方式計算，不可把已從餘額扣除的預留再扣一次；跨來源的並發驗收須包含此情境。

### 4.2 預扣、執行、結算

下列為 **[擬議]** 流程，不是現行可用端點：

```text
App 後端以 Website 發行的使用者授權（含已註冊 client_id、product scope）
  -> POST /api/app-integrations/v1/usage/reservations
     {operation_id, product_key, tool_key, company_id, estimated_quantity, metadata}
  <- {reservation_id, reserved_units, price_version, expires_at}
  -> 執行 App 工作
  -> POST /api/app-integrations/v1/usage/reservations/{id}/commit
     {operation_id, actual_quantity, outcome, usage_evidence}
  <- {usage_id, charged_units, released_units, balance, ledger_entry_id}
失敗且未產生成本時：POST /api/app-integrations/v1/usage/reservations/{id}/cancel
```

對 App，`company_id` 必須經主站比對授權主體的企業成員資格及必要的活躍租約；對 CRM 類 Web SaaS，主站應驗證已綁定的產品實例及其公司，不要求每位本機使用者擁有 Website 帳號。主站亦須確認 entitlement、工具權限、費率和可用餘額。背景工作若無使用者在線，須另外核發受限的產品服務身分及明確授權，**不得借用 open-webui 的 Service Token**。所有付費呼叫只由產品後端發出，不在前端呼叫。

`operation_id`／`Idempotency-Key` 在 reservation、commit、cancel 中應重複使用並由主站保證冪等；同鍵不同內容回衝突，不得重複扣款。同一 reservation 只能終結一次，逾期預留額度要由主站回收。工作開始前失敗須取消預扣；已產生部分成本時依可驗證實際用量結算。若 commit 回應逾時，先查詢該 `operation_id` 的狀態，再以同鍵安全重試，不能另開一筆扣款。

主站應提供 **[擬議]** `GET /api/app-integrations/v1/usage/operations/{operation_id}` 與分頁用量／帳本查詢，包含時間、產品、工具、操作者 `sub`、企業、數量、費率版本、預扣、實扣、取消／退回、來源請求 ID 與狀態。App 需保存自己的操作紀錄和對帳游標；人工退款、費率調整只能由主站留下反向帳務紀錄，不直接改舊資料。

帳本另須標示 AI／非 AI 用量類別及來源，並能在 Website 同一個用量入口篩選。若一次業務操作同時含 AI 推論及另行定價的 App 功能，保存共同的業務關聯 ID，以及各自唯一的計費操作 ID；AI 部分由 Interact AI 結算，App 僅提交自身服務費，禁止再提交相同模型用量。

### 4.3 錯誤與安全

| 狀況 | App 行為 |
| --- | --- |
| 未登入／token 過期 | 清除工作階段，重新走 Website 授權 |
| 公司成員資格或產品權限不足 | 拒絕操作；不改資料、不扣款 |
| 餘額不足 | 提示可用餘額與主站儲值入口；不執行工作 |
| 預扣／結算逾時 | 保留 `operation_id`，查詢主站狀態後冪等重試；不可假設未扣款 |
| 主站帳務服務不可用 | 不啟動新的付費工作；已發生用量進入有稽核的補結算佇列 |
| 重複提交 | 回同一筆操作結果；同鍵不同內容拒絕 |

主站與 App 需共同定義正式 HTTP 狀態、錯誤代碼、請求簽章／token audience、速率限制、時鐘容差、Webhook 驗簽與事件重送；本文件不把未實作的錯誤碼宣稱為已存在。

### 4.4 Website Profile／企業入口的統一使用紀錄

兩種扣款都須出現在現有的「使用紀錄」清單，不要求使用者另找一個產品頁面。沿用現有入口，將只稱為「AI 使用紀錄」的標題改為可包含兩種用量的名稱；支援按公司權限、日期、產品及計費方式篩選。

| 紀錄類別 | 必要 badge | 可展開的明細 |
| --- | --- | --- |
| Interact AI 回報的 AI 用量 | `AI 用量`、實際來源產品、用途／工作流；有模型時顯示模型 | Input／Output／Compute 用量、實扣數量、費率、操作與帳本關聯 |
| Website 直接處理的非 AI 費用 | `功能扣款`、實際來源產品、工具／用途 | 處理數量與單位、核定費率、實扣數量、操作與帳本關聯；不填虛構模型 Token |

例如 `AI 用量`、`CRM`、`BD 潛客探索`；或 `功能扣款`、`文件 App`、`文件轉檔`。直接由 Website 頁面發起的功能顯示來源 `Website`。這些為目標顯示方式，尚未全部實作。

用量類別、來源與用途必須來自後端驗證過的 client／產品、工具及工作流資料；不能僅依「是否帶 API Key」判斷 AI 用量，也不能相信呼叫端任意填寫的 badge。舊紀錄只按可查證資訊顯示，無法辨認的來源標示「未提供」，不靠模型名稱猜測產品。

成功結算須同時保留使用紀錄與扣款帳本，重試回到同一筆操作。預扣、待結算、已結算、取消及退款須有可辨識狀態；尚未收到結算不可顯示已扣款。部分失敗但已產生 AI 用量時，照實顯示失敗狀態與實扣金額。兩種路徑的共用錢包餘額、使用清單與明細需可相互對帳。

## 5. 使用平台 AI：CRM／其他 App 採用 Interact AI API Key

Website 與 Interact AI 是完整平台內的帳務端與 AI 執行端。**沒有 AI 功能時，不要求 AI Key，也不呼叫 AI 計費端點。**CRM／其他 App 加入 AI 功能時，目標是使用 Interact AI 為已驗證使用者發行的 API Key，由 Key 的企業歸屬對應 Website 帳務；產品後端安全保存，呼叫 `https://ai.interact-vision.com.tw/api/v1/models` 與 `/chat/completions`。AI API 的模型權限和 AI 用量扣款依 [現行 AI 手冊](./interact-app-auth-billing-integration-guide-zh-TW.md) 執行。缺少、過期或撤銷 AI Key 時，只停用需 AI 的功能；不影響已獲 Website 授權的非 AI 功能。

每個產品／使用用途的 Key 應能獨立辨識、輪替及撤銷，避免不同產品共用同一把 Key。現有 AI API Key 的 scope 固定為模型讀取與聊天寫入，**尚未提供產品級 scope**；Key 名稱可以標示產品，但不可把名稱誤認為安全隔離。若未來需產品級配額或隔離，應擴充平台 AI 存取合約，無須因此把 Interact AI 拆成另一個產品。

**兩條用量路徑不可混淆：** 非 AI 功能由 Website 的產品 `tool_key` 計量並扣款；AI 模型推論由 Interact AI API Key 觸發其既有 Website AI 結算。不要把同一次模型推論再偽裝為 App 功能用量重扣。若某功能明確包含 AI 推論以外、另行收費的 App 服務，需事先定價與告知，留下兩筆可辨識的用量事件，而不是暗中重複扣費。

同一平台的定位不代表現行登入憑證已可互換。目前 Interact AI 仍透過使用者對映主站企業帳務；上線前須驗證 API Key 擁有人、主站穩定身分與企業歸屬一致，並處理改 Email、跨公司及成員離職情況。逐步補齊穩定主體 ID 對映，屬於平台內部整合，不另建第二套客戶公司或錢包。API Key 不放在瀏覽器或日誌；撤權後應撤銷對應 Key。

**CRM 現況與遷移：** 現有 CRM 使用企業綁定及短效 Agent Token，並非已全面採用上述 API Key；公開 AI Key 目前也未開放所有工作流／Agent 管理端點。需先驗收所需能力與權限，再調整憑證流程。此次文件修正保留現有 CRM 連線與 `service@chengsyin.com` 可用性，亦不要求 CRM 本機使用者逐一新建 Website 帳號。

## 6. 主站與 App 的交付／驗收清單

主站實作前，這份文件只能當需求規格；不能拿來驗收一個已可登入或扣款的 App。正式交付至少需要：

1. 主站發布版本化的授權與 API 文件、正式／測試環境、App 註冊程序、scope 與 JWKS／密鑰輪替政策。
2. App 以現有 Website 帳號驗證登入，不持有密碼；Web SaaS 驗證公司／產品實例授權，本機使用者可無人數上限自行建立。
3. 帳號停權、成員移除、產品過期、取消訂閱及寬限期都能及時生效；用明確 entitlement 決定可用功能。
4. 餘額、預扣、成功結算、取消、部分結算、逾時重試、重複請求與退款均由主站帳本可追溯，並完成並發扣款測試。
5. 驗證不同公司、不同產品實例、不同操作者無法交叉查詢資料或代扣；App 同時活躍使用者上限可原子執行，CRM 等 Web SaaS 不受人數限制；前端不含 Service Token、主站密碼或 AI API Key。
6. 在完全不啟用 AI 的情況下，驗證 App 付費功能仍能由 Website 合約預扣、結算與對帳，且沒有產生 `ai-chat` 用量。
7. 日後加入 AI 時，驗證 Interact AI API Key 的擁有人及企業計費歸屬、可個別撤銷；同一次 AI 推論不重複計費，缺少 Key 不會阻斷非 AI 功能。CRM 過渡階段另驗收既有 Agent Token 的相容性。
8. 主站與 App 各自記錄 request／operation ID、狀態、時間與必要稽核資訊，但不記錄完整 token、API Key 或密碼。
9. AI 與非 AI 工作並發時，共用錢包不超扣、預留不重複計算，取消與重試可對帳；Website 能集中查看兩類用量。
10. 平台方案中的 AI 功能與延伸產品授權明確呈現，沒有新增獨立 Interact AI 訂閱、錢包或 App 同時在線名額。

在以上主站合約尚未發布前，可開發 App 的獨立業務功能與 mock 介面，但**不能宣稱已支援 Website 現有帳密登入、產品停用判斷或正式扣款**。
