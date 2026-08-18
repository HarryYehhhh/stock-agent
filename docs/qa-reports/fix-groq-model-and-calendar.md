# QA Report: fix-groq-model-and-calendar

- **Contract**: 無（feature skill，無 contract 文件；對照使用者需求描述之三個 production bug）
- **Date**: 2026-08-18
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. Groq model 換成 `openai/gpt-oss-120b`，`agent.py`/`check_bot.py` 寫死處 | ✅ | `src/agent/agent.py:25` `MODEL`、`src/agent/check_bot.py:26` `DEFAULT_GROQ_MODEL` 均已改。全 repo grep `llama-3.3-70b-versatile` 只殘留於歷史文件（`docs/decisions/0010-*.md`）與 `agent.py:390` 一句歷史觀察註解，兩者皆不影響執行期行為，且與 generator「歷史文件刻意不改」的說明一致。README/AGENTS.md/CLAUDE.md 同步更新。 |
| 2. 財報行事曆殭屍 row 清理 | ✅ | `StorageBackend` Protocol 新增 `delete_earnings_calendar_except(symbol, keep_dates, date_from)`；`LocalSQLiteBackend`／`DynamoDBBackend` 皆實作，語意一致（`earning_date >= date_from` 且不在 `keep_dates` 才刪，過去日期不動）。DynamoDB 版有處理 `LastEvaluatedKey` 分頁。`sync_earnings()` 每 symbol fetch 後呼叫 cleanup，fetch 失敗與 cleanup 失敗分開 try/except，單一 symbol 失敗不中斷其他 symbol（`test_sync_earnings_cleanup_failure_does_not_block_other_symbols` 驗證）。 |
| 3. Groq 錯誤 fallback 不得被快取成個股摘要 | ✅ | `agent.py` 把 6 個 finalize 回傳字串抽成具名常數 + `NON_CACHEABLE_FALLBACK_MESSAGES` frozenset；純字串抽取，`run_agent()` 對 processor 聊天路徑的行為與回傳值逐行比對未變（見下方驗證）。`scheduler_handler._company_summary()` 於命中 fallback 訊息時改走 `raw_data_fallback()` 且不呼叫 `upsert_company_summary`，6 種 fallback 皆有 parametrize 測試覆蓋。 |
| 每項需求有對應測試，`make test` 全綠 | ✅ | 見下方 Test results；並用 `git stash` 隔離只還原 `src/`（保留新測試）驗證 12 個新測試在修復前確實全部 FAIL（見 Findings 下方驗證紀錄），修復後全部 PASS。 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（`dangerouslyDisableSandbox: true` 執行，因預設 sandbox 會擋 `test_local_integration.py` 用到的 `ThreadingHTTPServer.bind` — 屬 sandbox 限制而非程式問題）
- 結果：**580 passed**, 0 failed, 0 skipped
- 失敗測試：無
- 既有 suite regression：✅（580/580 全綠，無既有測試被改壞）
- 額外驗證：`git stash push -- src/agent/agent.py src/agent/db/backend.py src/agent/scheduler_handler.py src/agent/check_bot.py`（只還原程式碼、保留新測試）後重跑 `test_scheduler.py`/`test_storage_backend.py`/`test_dynamodb_backend.py`，12 個新增的重現測試（含 bug 2 的 4 個、bug 3 的 6 個 parametrize + `AttributeError`）全部 FAIL，`git stash pop` 還原後全部 PASS，證實測試確實在驗證修復本身而非誤判綠燈。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/scheduler_handler.py:68-69`（`sync_earnings`）**：當 `get_earnings_calendar()` 呼叫成功但回傳空 list（`rows == []`）時，`keep_dates` 為空，`delete_earnings_calendar_except(symbol, [], today)` 會刪掉該 symbol **所有** `earning_date >= today` 的快取 row。這在「Finnhub 對該 symbol 目前確實沒有排定財報」時是正確行為，但無法與「Finnhub 對該次呼叫回傳異常但仍是 200（例如已知的 free tier symbol 參數不穩定行為，見 `tools/earnings.py` 內 `# Defensive client-side filter in case Finnhub ignores the symbol param on free tier` 註解）」的情境區分——後者會誤刪掉真正即將發布、且應該在 24 小時內推播提醒的財報 row，造成與本次要修的 bug 2 對稱的「漏推播」風險。目前沒有測試涵蓋「fetch 成功回傳 `[]` 且該 symbol 先前確實有未來 row」時是否真的整批被刪除（現有的 `test_sync_earnings_cleanup_failure_does_not_block_other_symbols` 用空 fetch 結果，但把 `delete_earnings_calendar_except` mock 成拋例外，並未驗證成功刪除路徑的結果）。
  建議修法：至少寫一個測試明確斷言並記錄這個已知取捨（讓未來維護者不會誤以為是 bug）；更保守的做法可考慮「fetch 回傳空且該 symbol 於清理前有 `earning_date` 落在近 7 天內的快取 row 時記一則 WARNING log（不阻擋刪除，但方便事後從 CloudWatch 追蹤是否誤刪）」。是否要改變刪除邏輯本身，建議先跟 spec 提出者確認这是否為可接受的已知風險，而非直接動 code。

### 🟢 Suggestions
- **`src/agent/agent.py:390`**：短路邏輯的歷史觀察註解仍寫「llama-3.3-70b has been observed to fabricate answers」，指名的是已下架的舊 model；現在的行為描述（工具拋例外時不可信賴 tool loop 繼續）已與新 model 無關，維持原樣不影響正確性，但下次順手改动附近程式碼時可以一併把措辭改成不指名特定 model（不需要為此單獨開票）。

### ✅ What looks good
- Bug 1：改動範圍精準到位，`MODEL` / `DEFAULT_GROQ_MODEL` 兩處寫死位置皆已修正，且用 `test_uses_groq_model` 鎖死新值；trace 相關測試的 `model` fixture 字串同步更新，沒有殘留會影響執行期行為的舊 model 名。
- Bug 2：`StorageBackend` Protocol 契約清楚（docstring 說明語意與 TTL 邊界），SQLite 與 DynamoDB 兩個實作行為一致（皆用測試驗證「過去日期不動」「其他 symbol 不動」「新日期取代舊日期」三種情境），DynamoDB 版正確處理 `LastEvaluatedKey` 分頁，避免大量歷史 row 時漏刪。
- Bug 3：把散落在 `run_agent()` 六處的 fallback 字面量抽成具名常數是很乾淨的重構，`NON_CACHEABLE_FALLBACK_MESSAGES` 的 docstring 直接寫明本次 bug 的因果（CRCL 2026-08-18 事故），可追溯性好；`run_agent()` 本身的邏輯（各 `finalize()` 呼叫點的參數、順序、分支條件）逐行比對與修復前完全相同，字串抽取沒有夾帶行為變更，processor 聊天路徑不受影響。
- 三個修復都有對應的重現測試，且已用 `git stash` 隔離驗證這些測試在修復前確實會失敗（非事後補測試充數）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：
  1.（可選，不阻擋合併）針對 🟡 補一則測試/註解明確記錄「Finnhub 回傳空 list 時會清空該 symbol 未來快取」是已知且接受的行為，避免未來被誤判為新 bug。
  2. 無其他待辦。
