# QA Report: runbook-triage-method

- **Contract**: 無正式 contract（純文件任務，需求由主 Claude 於任務描述中給出：在 `docs/runbook.md` 的 `## Incident 排查` 補一節「回答內容錯誤但系統沒報錯」的排查方法論）
- **Date**: 2026-07-29
- **Verdict**: ⚠️ Pass with issues

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 只改 `docs/runbook.md`，不動程式碼、不改寫既有內容 | ✅ | `git diff --stat` 僅 `docs/runbook.md` +33/-0；既有 101 行內容逐字未動 |
| 新增小節放在 `## Incident 排查` 最後 | ✅ | 緊接在既有「Processor 有 LLM API 錯誤」小節之後 |
| 五組 `file:line` 引用準確 | ✅ | 逐一開檔核對，全部精準命中（見下方 Findings 的 ✅ 區） |
| 資料表欄位名稱與實際 schema 相符 | ✅ | `tools[]`、`duration_ms`、`updated_at` 等欄位皆核對 `agent.py` trace 建構處與 `db/backend.py` 存讀處，一致 |
| 技術主張成立（tool_use_failed 攔截點、trace fail-silent、cache write 失敗語意） | ✅ | 見下方逐項核對 |
| 風格對齊既有段落（簡潔條列） | ⚠️ | 見 🟡 |
| `make test` 全綠（扣除環境限制） | ✅ | 525 passed, 13 errors（環境限制，與既有 baseline 一致） |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 結果：525 passed / 13 errors
- 失敗測試：無（13 個 error 全部是 `tests/test_local_integration.py` 的 socket bind `PermissionError`，sandbox 環境限制，非本次改動造成）
- 既有 suite regression：✅ 無（本次不動任何程式碼，測試結果理論上應與 baseline 完全一致，實測相符）

## file:line 逐一核對

| 引用 | 核對結果 |
|---|---|
| `src/agent/agent.py:180`（`save_trace`，179-182 try/except） | ✅ 179 `try:` / 180 `get_backend().save_trace(trace)` / 181 `except Exception:` / 182 `log.warning(...)`，逐行相符 |
| `src/agent/agent.py:104`（`tool %s succeeded`） | ✅ 103 `log.info(` / 104 `"tool %s succeeded: args=%s result_type=%s result_size=%s",`，訊息字串所在行正確 |
| `src/agent/agent.py:114`（`tool %s failed`） | ✅ `log.warning("tool %s failed: %s", name, ...)` 精確落在第 114 行 |
| `src/agent/tools/earnings.py:102-105`（cache write try/except，訊息在 105） | ✅ 102 `try:` / 103 `get_backend().upsert_earnings_calendar(items)` / 104 `except Exception as e:` / 105 `log.warning("earnings calendar cache write failed: %s", e)`，且第 106 行 `return items` 在 try/except **之外**，確認「tool 本身可能已成功回傳資料」這個技術主張成立（cache 寫入失敗不影響已算好的 `items` 被回傳給 LLM） |
| `src/agent/agent.py:244-264`（`tool_use_failed` retry） | ✅ 244 `except BadRequestError as e:` 到 264 `continue`，剛好是完整的 retry 分支；且該 `except` 掛在呼叫 `_client().chat.completions.create()` 的 `try`（237-243）上，位於 `message.tool_calls` 解析與 `_run_tool` 呼叫（305-311）**之前**，證實文件「`_run_tool` 從未進入」的說法正確 |

## 技術主張核對

- **trace `tools[]` 欄位**：`agent.py:312-320` 建構 `tool_trace.append({...})` 含 `iteration`/`name`/`args`/`duration_ms`/`result_type`/`result_size`/`ok`；`db/backend.py:1543-1570`（`save_trace`）與 `1572-1592`（`_item_to_trace`）都原樣存讀 `tools` 欄位（JSON string），文件列出的 `name`/`args`/`ok`/`duration_ms`/`result_type`/`result_size` 六個子欄位全部存在（`iteration` 未提及但不影響正確性，僅是未窮舉）。
- **三層判斷邏輯**：讀 `agent.py:235-330` 的迴圈結構確認，`BadRequestError`（`tool_use_failed`）發生在 `_client().chat.completions.create()` 呼叫階段（237-243），比 `tool_calls` 解析與 `_run_tool` 呼叫（286-320）更早被攔截，故文件「模型發出了呼叫，但被伺服器端 schema 驗證擋掉，`_run_tool` 從未進入」的說法成立。
- **processor Lambda timeout 300s**：`infra/template.yaml:346` `Timeout: 300`，與文件一致。
- **`earnings.py:105` vs `agent.py:114` 的語意對比**：核對屬實——前者是快取寫入失敗（tool 已經算出 `items` 並會照常 `return`），後者是 `_run_tool` 的 `except Exception as e` 分支，代表 tool function 本身丟出例外、`_run_tool` 會回傳 `{"error": ...}` 給 LLM，兩者訊息長相相似但意義相反，文件描述正確。

## Findings

### 🔴 Critical（Must Fix）

- **`docs/runbook.md:107`**：開場範例聲稱「2026-07-29 一次回答把財報日期講錯、把分析師人數講成百分比（bug 本身已由 PR #47、#48 修掉）」，但逐一核對 PR 內容後這個歸因不成立：
  - PR #47（`b2675e7`）commit message 明確只針對「Finnhub recommendation trend 的分析師人數說成百分比」，與財報日期無關。
  - PR #48（`6d29d57`）commit message 明確只針對 `search_knowledge_base` 的 `top_k` schema 驗證失敗與 CLAUDE.md/AGENTS.md 過時工具描述，同樣與財報日期無關。
  - 真正符合「META 財報日期 7/31 vs 實際 7/29」描述的紀錄是 `docs/qa-reports/snapshot-audit.md:74`（`真實 judge 跑抓到一則貨真價實的財報日期幻覺（META 7/31 vs 快照 7/29）`）與 `docs/CHANGELOG.md:100`——這是**離線稽核工具**（`snapshot-audit` sprint）在 `audit/2026-07-27/report.md`（日期是 **2026-07-27**，不是 2026-07-29）跑出來的一則幻覺案例，用來證明稽核工具本身有效，該 qa-report 從未宣稱這個幻覺已被任何 PR 修掉——反而在 🟡 Warnings 建議補一行「時間錯位」的 header caveat，暗示這類問題是已知限制而非已解決。
  - 也就是說，文件把兩個不相關的真實事件（percentage bug 現網事故，2026-07-29，PR #47 修）和（離線稽核發現的日期幻覺，2026-07-27，未見任何 PR 修復紀錄）拼接成「同一天、同兩個 PR 修掉」的單一敘事。這正好是本節「紀律」段落自己要求禁止的事（「推測要標成推測，不要當結論寫進報告」），且是整節的開場例子，容易誤導後人跑去 PR #48 找一個不存在的日期修法。
  建議修法：拆開兩件事的敘述——percentage 部分明確指向 PR #47；日期幻覺部分改引用 `docs/qa-reports/snapshot-audit.md`（離線稽核發現，2026-07-27），不要宣稱已被 PR 修掉；或者更簡單地拿掉具體 PR 編號與日期歸因，範例只需示意「這類問題長什麼樣」，不必附帶一個查證後站不住腳的修復記錄。

### 🟡 Warnings（Should Fix）

- **`docs/runbook.md:136`**：「2026-07-29 那次事故就同時刪掉 traces 表，導致該請求的 trace 遺失」——`traces`/`corrections` 表被刪確實有據（對應 `dec3aae` deploy-guard PR 要防的「stale 本機部署」事故），但「導致該請求（本節討論的幻覺請求）的 trace 遺失」這個因果連結，翻遍 `docs/CHANGELOG.md`、`docs/qa-reports/deploy-guard.md` 都找不到佐證——deploy-guard 的 qa-report 完全沒提到受影響的是哪個 chat_id/請求。這是把兩個只是剛好同一天（而這個 harness session 裡幾乎所有 sprint 都標記同一天）發生的事件，包裝成有因果關係的具體案例。
  建議修法：拿掉「導致該請求的 trace 遺失」這個具體因果宣稱，改成更保守的通用提醒，例如「可觀測性設施本身也可能故障或被清空（例如部署誤操作刪表），排查前應先確認 trace/log 是否完整存在，不要預設一定拿得到」。
- **風格與既有小節落差較大**：`## Incident 排查` 底下其他四個既有小節都是 2-10 行的純條列（`使用者說 Telegram 沒回覆`、`只有 webhook log`、`沒有 scheduler logs`、`Processor 有 LLM API 錯誤`），新小節長達 32 行、且用了既有小節從未出現過的粗體偽標題（`**排查順序**`、`**判斷「LLM 有沒有呼叫 tool」的三個層次**` 等）分四塊。內容本身仍是條列、非散文，且 generator 已說明取捨（主題有三層判斷邏輯，難用純條列表達）——這個理由站得住腳，不算違反「不寫散文」的要求，但份量與既有小節的比例落差是既有讀者體感會注意到的地方。非阻塞，供參考：若想更貼近既有風格，可考慮把「排查順序」與「三個層次」合併成一組條列（三層判斷本質上是排查順序第 6 步的展開），或拆成獨立的操作指引文件由本節連結。

### 🟢 Suggestions

- 無其他值得一提的細節；五組 file:line 引用、trace 欄位、Lambda timeout 等具體技術宣稱全數核對通過。

### ✅ What looks good

- 五組 `file:line` 引用（`agent.py:180`、`agent.py:104`、`agent.py:114`、`earnings.py:102-105`、`agent.py:244-264`）全部逐行核對正確，這是本文件的核心價值所在。
- `earnings.py:105` vs `agent.py:114` 的語意對比（快取寫入失敗 vs tool 執行例外）技術上完全正確，且準確指出「`return items` 在 try/except 之外」這個容易被忽略的細節，是全篇最有價值的觀察。
- `tool_use_failed` 三層判斷邏輯的攔截點描述（`_run_tool` 從未進入）讀碼確認完全正確。
- `git diff` 確認除新增段落外零改寫既有內容，surgical。
- `make test` 結果與環境限制的預期（525 passed, 13 errors）完全吻合。

## 結論

- 是否可合併：**建議先修正 🔴 後再合併**——只需調整 `docs/runbook.md:107` 這一段的敘述（拿掉不成立的 PR 歸因），不需要動到其餘任何內容，改動範圍極小，一輪即可修完。
- 最該先處理的 1–3 件事：
  1. （🔴 必修）`docs/runbook.md:107`：修正/拿掉「bug 已由 PR #47、#48 修掉」對財報日期幻覺的錯誤歸因，改引用實際來源（`snapshot-audit` 離線稽核，2026-07-27）或直接淡化成不涉及具體 PR/日期的示意例子。
  2. （🟡 建議修）`docs/runbook.md:136`：拿掉「導致該請求的 trace 遺失」這個查無實據的具體因果宣稱，改成通用提醒。
  3. （🟢 可選）評估新小節份量與既有 Incident 排查小節的風格比例落差，非阻塞。
