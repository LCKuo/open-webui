# InteractAgentHub 自訂功能檢查

執行：

```bash
npm run check:interact
```

後端測試會依序尋找 `PYTHON` 環境變數、專案 `.venv`、`python3`、`python` 與 Windows `py -3`，不需要把執行檔路徑寫死在專案內。

這個指令依序檢查：

1. Interact 工作流節點目錄與範本的前端測試。
2. 企業權限、計費、Channel、LINE 選單、Email、工作流與資料庫錯誤處理的後端測試。
3. 完整前端 production build；建置程序使用 8 GB Node heap，避免大型客製版本在產生 chunks 時撞到預設記憶體上限。

上游 Open WebUI 的 `npm run check` 目前會回報大量既有 Svelte 型別問題，因此不把它當成 Interact 客製功能的放行條件。升級上游版本時仍應保存輸出並比較錯誤數量，避免新增回歸。
