# docs/qa-reports/

QA verification reports — evaluator 產出。檔名與 contract 對齊：`<sprint-name>.md`。
每份 report 必須分列 Unit、Integration、E2E 與 External Smoke 的 required / blocking、command、environment、scenarios、attempts、result 與 evidence；失敗時分類為 Product defect、Test defect、Environment failure、Flaky test 或 Unknown。
依賴外部 API 的阻擋式驗證預設使用 Integration test 的 fake / stub server，不呼叫真實第三方；External Smoke 預設 non-blocking，只有使用者或 contract 明確指定時才阻擋完成。
通用錯誤碼或外層 exception 只能列為 `Surface Error`，不可當作 Root Cause。失敗 report 還必須列出最深可行動根因、cause chain、去敏 diagnostics、已嘗試的安全修復與結果。若受權限阻擋，列出 operation、resource、執行身分 / sandbox、原始 denial、最小所需權限與下一個精確動作，不可只寫 `permission denied` 或 `error`。
格式範本見 `~/.codex/agents/evaluator.md`。
