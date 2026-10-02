# 通用功能扣款與使用分類發布驗收

日期：2026-09-21。結果：已發布。

## 範圍

- 新增公司／產品綁定的後端扣款憑證、核定功能費率、上限與稽核紀錄。
- 新增 `/api/app-integrations/v1` 餘額、費率、直接扣款、預留／結算／取消及操作查詢。
- Admin →「功能扣款 API」管理；現行平台方案表單集中至授權中心，未新增 CRM 席位或改變授權判斷。
- Profile 使用紀錄區分 AI 用量／功能扣款，顯示來源、用途及相關明細。
- 保留既有 CRM Agent Token、AI 計價方式、企業錢包與原有資料。

## Code Review

檢查重點：公司隔離、憑證保存與撤銷、管理員權限、請求驗證、價格可信來源、共同預留額度、原子交易、冪等、部分結算、到期取消、費率快照、歷史相容及使用紀錄顯示。

修正並補驗：

1. 明細欄會擠窄其他欄位：改成跨列展開，保留分類與實扣數量的可讀性。
2. 非 AI 用量不能套用 LLM 的 Token／台幣換算：保留實扣平台 Token、數量及核定費率，未知台幣費用不偽造。
3. 使用紀錄 CSV 的外部摘要可能被試算表當成公式：加入公式起始字元防護。
4. 未傳分頁筆數時，`Number(null)` 使預設值變成 1：修正省略／空字串的預設值並補回歸測試。

本次檢查未發現尚未修正的阻擋發布問題；測試不代表能保證任何情境零缺陷。

## 驗證

- 12 項隔離資料庫回歸測試全部通過。涵蓋同鍵並發重送、不同操作並發扣款不透支、跨憑證拒絕、取消／到期、費率快照、上限、交易失敗回滾、既有 AI authorize/commit 與新 App 共用預留、分類與匯出。
- ESLint（新增帳務、API、UI 模組）及 Next production build／TypeScript 通過。
- Playwright 實際操作管理畫面建立憑證與費率，透過 HTTP 扣款並核對 Profile 分類。1440px 桌面與 390px 手機檢查無頁面級溢位、無瀏覽器執行錯誤；表格在窄螢幕可水平捲動。
- 正式 Linux 主機的隔離空資料庫：管理員 API、直接扣款、重送／衝突、預留與部分結算、共用餘額、使用紀錄、管理與歷史頁 SSR、帳本筆數均通過。
- 正式站發布後：登入頁 200、既有發票教學頁 200、session API 200、新扣款 API 對未知憑證回 401、新管理 API 對匿名回 401。CRM Token 端點保留 POST-only（GET 405）。
- 外網 HTTPS：新扣款 API 正確回 401 JSON；新管理頁匿名導向管理員登入；CRM 登入頁與 Interact AI `/health` 均為 200。
- 未使用正式客戶餘額做測試扣款；正式 AppBillingClient、AppBillingOperation 初始均為 0。

## CRM 保護

比對 `service@chengsyin.com` 發布前後：企業狀態、平台計費狀態、AI 帳號狀態均為 active；登入／服務憑證指紋一致；CRM instance 綁定及 allowedScopes 不變；餘額均為 9,802,552 Token。

正式環境的 CRM 通知路由及 agent-actions 路由，以發布前來源快照保留，未帶入本機其他尚未發布的修改。這次未部署 Interact AI 或 CRM 容器，也未實際發送客戶郵件或發起付費 AI 推論。

發布後再次唯讀查核，CRM 的 `lastTokenIssuedAt` 從 `1790003689252` 前進至 `1790005435839`，確認既有綁定仍有新的 Token 簽發；企業狀態與餘額保持不變。

## 發布與回復

- 前版本：`jIqesoW7f53D0b9UTpZsX`。
- 新版本：`d4AnHbiehU8Uy5KpQGYqA`。
- 正式根目錄：`/home/ec2-user/auto_deploy_interact-20260903_100558`。
- 備份：`backup-app-billing-20260921-v2`，包含前版 `.next`、Prisma client／schema、SQLite 一致性備份與驗證結果。
- 資料庫只新增四張帳務設定／操作表與索引，不刪改舊表、公司餘額或 CRM 綁定。
- 回復程式時還原舊 `.next` 與 Prisma client 並重新載入 PM2；新增表可保留。不可直接覆蓋整份資料庫，以免抹掉發布後的真實用量。

## 串接限制

此版不是 OAuth 登入、產品 entitlement、並發席位或退款 API。新產品須先由管理員核定費率、建立專屬後端憑證，再依[串接手冊](../interact-website-function-billing-api-zh-TW.md)接入。這些設定沒有替任何正式客戶自動建立。

既有 build 的 middleware 慣例與 uploads 路由廣泛檔案追蹤警告仍存在，非本次功能新增錯誤。本次隔離建置排除了暫存依賴，未將其部署到正式站。
