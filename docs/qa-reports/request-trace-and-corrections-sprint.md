# QA Report: request-trace-and-corrections-sprint

- **Contract**: docs/contracts/request-trace-and-corrections-sprint.md
- **Spec**: docs/specs/request-trace-and-corrections.md
- **ADR**: docs/decisions/0013-request-trace-persistence.md、docs/decisions/0014-corrections-controlled-feedback-loop.md
- **Date**: 2026-07-29
- **Verdict**: ⚠️ Pass with issues（僅 1 個 🟡，不阻擋合併）

## Contract checklist

### Feature 1：Request Trace

| Deliverable | Status | Note |
|---|---|---|
| 1A. Protocol 新增 `save_trace`/`list_traces`，SQLite `traces` 表 + `list_traces` 過濾，DynamoDB `traces` 表（PK `chat_id`+SK `sk`、GSI `trace-time-index`），`traces_table()` accessor | ✅ | `db/backend.py:147-163`（Protocol）、`:358-402`（SQLite schema，比照既有 `_init_schema` 慣例）、`:1543-1616`（DynamoDB，巢狀欄位存 JSON string，兩端 `list_traces` 回傳結構一致）、`config.py:98-100` |
| 1B. `run_agent()` 涵蓋所有 return path（fast_path/completed/groq_error/tool_use_failed_exhausted/loop_exceeded）落 trace，欄位齊備，`save_trace` 包 try/except 不 raise，零侵入 messages/回覆 | ✅ | `agent.py:136-183`（`finalize()` closure）+ 5 處 return path 全部改走 `finalize(...)`（diff 逐行核對，見下方 diff review）；`_run_tool` 呼叫契約與回傳未動，只在外層量測耗時 |
| 1C. `python -m agent.trace list`（`--chat-id`/`--since`/`--limit`/`--json`），走 `get_backend().list_traces()` | ✅ | `src/agent/trace/__main__.py`；實測見下方「DoD 實測證據」 |
| 1D. IaC `TracesTable`（PK+SK+GSI+PAY_PER_REQUEST+TTL），Processor 讀寫權限+`TRACES_TABLE` env，Scheduler 同步授權 | ✅ | `infra/template.yaml:212-241`（風格逐項比照 `NewsTable`）；Processor（`:345-348`）、Scheduler（`:393-394`）皆有 `TracesTable` CrudPolicy；`sam validate --lint` 通過 |

### Feature 2：Corrections

| Deliverable | Status | Note |
|---|---|---|
| 2A. Protocol 新增 `save_correction`/`list_corrections`/`update_correction_status`，SQLite `corrections` 表 + 狀態機，DynamoDB（PK `correction_id`、GSI `status-index`、TTL 90 天），`corrections_table()` accessor | ✅ | `db/backend.py:153-163`（Protocol）、`:386-402`（SQLite）、`:1618-1673`（DynamoDB，狀態更新用 `ConditionExpression="attribute_exists"` 正確回報 not-found）、`config.py:102-104` |
| 2B. `/correct` routing（無內文回用法不存、有內文存 pending+context Q/A+回覆確認），不呼叫 `run_agent`/不注入，`WELCOME` 補說明 | ✅ | `processor_handler.py:148-161`；不 `save_message`，避免糾正內文污染對話歷史；`WELCOME` 已加一行（`:48`） |
| 2C. `python -m agent.corrections`（list/show/approve/reject/export），export 產 `## ` markdown | ✅ | `src/agent/corrections/__main__.py`；`export` 產物可被 `parse_sections()` 解析（測試 `test_export_produces_heading_section` 驗證） |
| 2D. 注入路徑零新 runtime 碼，`run_agent()` 注入層完全不讀 corrections 表 | ✅ | `agent.py` 全文無 `corrections`/`db.corrections` import；結構性測試 `test_corrections_isolation.py` spy 斷言一次完整 `run_agent()`（playbook 命中+工具呼叫）中 `list_corrections`/`save_correction`/`update_correction_status` 零呼叫 |

### 共通

| 項目 | Status | Note |
|---|---|---|
| `tests/conftest.py` 建 `traces`（GSI `trace-time-index`）+ `corrections`（GSI `status-index`）兩 moto 表，既有測試不回歸 | ✅ | `tests/conftest.py:180-227`；全套 531 passed |
| `sam validate --region ap-northeast-1 --lint` | ✅ | `infra/template.yaml is a valid SAM Template` |
| 測試離線、決定性、不打網路 | ✅ | 新測試全用 `local_store`/`aws`(moto) fixture + mock Groq client，未見網路呼叫 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：518 passed，13 個 `test_local_integration.py` 的 `ThreadingHTTPServer` socket bind `PermissionError`（沙盒網路限制導致的既有已知現象，與本次改動無關，非本次新增測試）
- 停用沙盒重跑：**531 passed**，0 failed，0 skipped（與 generator 自報一致）
- 新增測試：8 個檔案、41 個測試函式（`test_correct_command.py` 5、`test_corrections_backend.py` 8、`test_corrections_cli.py` 8、`test_corrections_isolation.py` 1、`test_corrections_kb_reuse.py` 3、`test_trace_backend.py` 4、`test_trace_cli.py` 4、`test_trace_instrumentation.py` 8）
- 既有 suite regression：✅ 無

## DoD 實測證據（chat_cli/local_server 等效驗證）

本機無 `.env`（無真實 `GROQ_API_KEY`），且 sandbox 網路白名單不含 `api.groq.com`，故以「local SQLite backend + mock Groq client，透過真實 `run_agent()` / `processor_handler._process_update()` 程式碼路徑」等效驗證，驗證後以正式 CLI（`python -m agent.trace list`、`python -m agent.corrections list --status pending`）查表：

```
--- python -m agent.trace list ---
ts                         chat_id          outcome                   prompt completion iter #tools  total_ms
2026-07-28T19:01:11.687419+00:00 999              completed                     42         17    1      0         8

--- python -m agent.corrections list --status pending ---
6e45c740-f8ff-433c-a95c-3d6695f621bd	2026-07-28T19:01:11.696461+00:00	pending	AAPL 財報應該是 8/1 盤後公布
```

一次 `run_agent("AAPL 分析師怎麼看？", [], chat_id="999")` 後可查得該筆 trace（含 token/iterations/total_ms）；一次 `/correct AAPL 財報應該是 8/1 盤後公布` 後可在 `corrections list --status pending` 查得該筆待審記錄。均與 DoD 描述一致。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）

- **`tests/test_trace_instrumentation.py`**：contract「Tests required › Trace」明確要求「`completed` 且有工具呼叫的 case，trace 內 `playbook_hits`（含 source+score）… 皆正確」，但目前唯一涵蓋 completed+工具呼叫的測試（`test_completed_outcome_with_tool_call_saves_full_trace`，query＝「AAPL 分析師怎麼看？」）不會命中 playbook（分數低於 `MIN_SCORE`），因此完全沒對 `trace["playbook_hits"]` 做斷言；另一個會命中 playbook 的測試（`test_zero_invasion_messages_and_reply_unaffected_by_trace`，query＝「本週有什麼公司發布財報」）只驗證送給 Groq 的 messages，沒有檢查落地的 trace 內容。`playbook_hits` 的正確 round-trip 只在 `test_trace_backend.py` 用手造 dict 驗證，未經過 `run_agent()` 實際計算路徑。
  - 影響：純測試覆蓋缺口，非功能缺陷——我用等效腳本手動跑過 `run_agent("本週有什麼公司發布財報", [])` 並讀出 `traces` 表，`playbook_hits` 正確含 `{"source": "earnings#財報行事曆時間查詢", "score": 10.71...}`，`agent.py` 的 `finalize()` closure 對 `playbook_hits` 外部變數的讀取（late-binding 閉包）行為正確。
  - 建議修法：在 `test_completed_outcome_with_tool_call_saves_full_trace`（或新增一測試）換一個會命中 playbook 的 query（例如「本週有什麼公司發布財報」+ mock 一次工具呼叫），並斷言 `trace["playbook_hits"]` 非空、含 `source`/`score`，把 contract 這條測試需求字面補齊。

### 🟢 Suggestions

- **`docs/CHANGELOG.md` planner 條目**與本次 generator 交付一致，內容詳實，未發現需調整之處。
- **`infra/template.yaml`**：`TracesTable`/`CorrectionsTable` 命名、GSI、TTL 寫法與既有 `NewsTable`/`RateLimitsTable` 風格完全一致，無需修改。
- Trace/Corrections 兩個 CLI 目前無 `make traces`/`make corrections` 快捷指令（contract P2，非必須），未來若常態使用可補上。

### ✅ What looks good

- `agent.py` 的 diff 極度 surgical：所有新增行都圍繞 `finalize()` closure 與其呼叫點，`_run_tool` 契約、送 Groq 的 `messages` 組裝邏輯、所有回覆字串逐字未動（我逐行核對 `git diff main -- src/agent/agent.py`）。
- `/correct` 指令刻意不呼叫 `save_message()`，避免糾正確認訊息污染對話歷史，也確保後續 `/correct` 的 `get_history(limit=2)` 取到的仍是真實對話而非糾正確認語——設計正確，優於字面規格的簡單實作。
- 隔離不變式測試（`test_corrections_isolation.py`）用 spy wrap 真實 backend 方法（而非純 Mock），在一次真實含 playbook 命中+工具呼叫的 `run_agent()` 執行後斷言零呼叫，結構性證明力足夠強，直接對應 ADR-0014 的核心安全訴求。
- DynamoDB 端巢狀欄位（`playbook_hits`/`tools`/`usage_by_iteration`）選擇存 JSON string 而非 Map/List，明確避開 float→Decimal 轉換問題，且兩端 `list_traces` 回傳結構完全一致（`_row_to_trace`/`_item_to_trace` 對稱處理），為 contract 允許的兩種做法之一，選擇合理且有註解說明理由。
- `knowledge/corrections.md` 正確地未被自動建立（維持 out-of-scope 邊界），`test_corrections_kb_reuse.py` 用 `tmp_path` 隔離驗證複用 `search_knowledge_base` 且不影響既有 `knowledge/*.md` 檢索行為。
- `sam validate --lint` 通過；IaC 改動風格與既有表格逐項比照，未發現隨意改動。

## 結論
- 是否可合併：**是**
- 最該先處理的 1 件事：`test_trace_instrumentation.py` 補一個「completed + 工具呼叫 + 命中 playbook」的組合斷言，把 contract 字面的 `playbook_hits` 測試需求補齊（功能本身已驗證正確，純測試覆蓋補強，不阻擋本次合併）。
