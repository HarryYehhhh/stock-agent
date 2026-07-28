# Contract: request-trace-and-corrections

對應 spec: docs/specs/request-trace-and-corrections.md
相關決策: docs/decisions/0013-request-trace-persistence.md、docs/decisions/0014-corrections-controlled-feedback-loop.md

> 路徑用中性描述，generator 對照 CLAUDE.md 解讀為具體值。
> 本專案：應用程式碼 `src/agent/`、storage `src/agent/db/backend.py`（`StorageBackend` Protocol + `LocalSQLiteBackend` + `DynamoDBBackend`）、agent loop `src/agent/agent.py` `run_agent()`、routing `src/agent/processor_handler.py`、檢索 `src/agent/knowledge/retrieval.py`、知識庫資料在專案根 `knowledge/`、IaC `infra/template.yaml`、測試 `tests/`（fixtures `tests/conftest.py`）、測試指令 `PYTHONPATH=src uv run pytest tests/ -v`（或 `make test`）。
> 交付順序建議：P1（Feature 1 trace 最小可用）→ P1（Feature 2 corrections 最小可用）→ P2。兩個 Feature 的 P1 都必須交付；P2 可視情況。

---

## P1 — Feature 1：Request Trace（最小可用）

### 1A. Storage：traces 表（`src/agent/db/backend.py`）
- [ ] `StorageBackend` Protocol 新增：
  - `save_trace(self, trace: dict) -> None`
  - `list_traces(self, chat_id: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]`
- [ ] `LocalSQLiteBackend`：新增 `traces` 表（欄位見 spec §1.1；`playbook_hits` / `tools` / `usage_by_iteration` 以 JSON 字串存），`save_trace` 寫入、`list_traces` 依 `chat_id`（可選）與 `since`（`ts >=`）過濾、`ts` 新到舊排序、`limit` 上限。schema 建立比照既有 `_init_schema` 慣例。
- [ ] `DynamoDBBackend`：`traces` 表 PK `chat_id`、SK `sk`（`f"{ts}#{trace_id}"`）；`save_trace` put 一筆（float 分數用 `Decimal(str(...))`，比照 `upsert_earnings_calendar` 既有模式；巢狀結構可存 map/list 或 JSON 字串——generator 擇一並在兩端保持 `list_traces` 回傳結構一致）；`list_traces`：帶 `chat_id` → Query 主表，否則 → Query `trace-time-index` GSI（PK `bucket="trace"`、SK `ts`），`since` 走 SK 條件、新到舊、`limit`。
- [ ] 新增 config accessor `traces_table()`（env 覆寫 + 預設 `earnings-agent-traces`），比照既有 `news_table()` 等。

### 1B. 儀表化 run_agent（`src/agent/agent.py`）
- [ ] 在 `run_agent()` 累積 `RequestTrace`（dataclass 或 dict builder），涵蓋 **所有 return 路徑**：`fast_path`、`completed`、`groq_error`、`tool_use_failed_exhausted`、`loop_exceeded`，`outcome` 對應正確。
- [ ] 記錄：`chat_id`（None → `"__none__"`）、`ts`、`trace_id`、`user_message`、`fast_path`、`playbook_hits`（沿用已算出的 `_retrieve_playbook_hits` 結果）、`history_msg_count` / `history_char_count`、每次工具呼叫 `{iteration, name, args(經 `_sanitize_error`), duration_ms, result_type, result_size, ok}`、`iterations`、`usage`（總計）+ `usage_by_iteration`（讀 `response.usage`）、`total_ms`、`reply_char_count`、`model`、`ttl`（30 天）、`bucket="trace"`。
- [ ] 結束前 `get_backend().save_trace(trace)`，**包 try/except、失敗記 `log.warning`、絕不 raise**。
- [ ] **零侵入**：不改任何送給 Groq 的 `messages`；不改任何回覆字串；不改現行 `playbook_injection` / `groq usage` / `_run_tool` log。
- [ ] 工具耗時量測包住既有 `_run_tool` 呼叫處（不改 `_run_tool` 回傳契約）。

### 1C. 查表 CLI（`src/agent/trace/`，比照 `agent.audit` 結構）
- [ ] `python -m agent.trace list [--chat-id X] [--since ISO8601] [--limit N] [--json]`：預設印精簡表格（`ts` / `chat_id` / `outcome` / `prompt` / `completion` / `iterations` / `#tools` / `total_ms`）；`--json` 印完整紀錄（供離線分析）。
- [ ] CLI 透過 `get_backend().list_traces(...)` 讀取（本地讀 SQLite、Lambda 環境讀 DynamoDB，沿用 `selected_backend_name()`）。

### 1D. IaC（`infra/template.yaml`）
- [ ] 新增 `TracesTable`：PK `chat_id`(S) + SK `sk`(S)、GSI `trace-time-index`（PK `bucket`(S) + SK `ts`(S)、`ProjectionType: ALL`）、`BillingMode: PAY_PER_REQUEST`、TTL on `ttl`。風格逐項比照既有 `NewsTable`（含 GSI 寫法）。
- [ ] Processor function 加 `TracesTable` 的 DynamoDB 讀寫權限與 `TRACES_TABLE` 環境變數（比照既有表注入方式）。scheduler 若也 trace 則同步授權（generator 依實作範圍決定；至少 processor 必須有）。

---

## P1 — Feature 2：Corrections 受控回饋迴路（最小可用）

### 2A. Storage：corrections 表（`src/agent/db/backend.py`）
- [ ] `StorageBackend` Protocol 新增：
  - `save_correction(self, chat_id: str, correction_text: str, context_question: str | None, context_answer: str | None) -> str`（回傳 `correction_id`）
  - `list_corrections(self, status: str | None = None, limit: int = 50) -> list[dict]`
  - `update_correction_status(self, correction_id: str, status: str) -> bool`（狀態限 `approved` / `rejected`；回傳是否有更新到）
- [ ] `LocalSQLiteBackend`：`corrections` 表（欄位見 spec §2.2）；`list_corrections` 依 `status` 過濾、`created_at` 排序；狀態更新 `pending→approved/rejected`。
- [ ] `DynamoDBBackend`：PK `correction_id`(S)、GSI `status-index`（PK `status`(S) + SK `created_at`(S)）；`list_corrections(status)` → Query GSI；`update_correction_status` → `update_item` SET `status`；TTL on `ttl`（90 天）。
- [ ] 新增 config accessor `corrections_table()`（預設 `earnings-agent-corrections`）。

### 2B. `/correct` 指令（`src/agent/processor_handler.py`）
- [ ] routing 加 `/correct` 與 `/correct <text>`：
  - 無內文 → 回用法提示（比照 `WATCH_USAGE` 風格），不存。
  - 有內文 → `get_history(chat_id, limit=2)` 抓上一組 user/assistant 當 `context_question`/`context_answer`（無合法上一組則存 None），`save_correction(...)`，回覆「✅ 已收到你的糾正，將由維護者審核後決定是否納入知識庫」之類。
- [ ] **不呼叫 `run_agent`、不注入、不改模型行為**。
- [ ] 指令列進 `WELCOME` 說明（一行）。

### 2C. Corrections CLI（`src/agent/corrections/`，比照 `agent.audit`）
- [ ] `python -m agent.corrections`：
  - `list [--status pending]`：表格印 `correction_id` / `created_at` / `status` / 內文摘要。
  - `show <id>`：印完整 correction（含 context Q/A）。
  - `approve <id>` / `reject <id>`：`update_correction_status`。
  - `export [--status approved]`：把條目輸出成可直接貼進 `knowledge/corrections.md` 的 `## <標題>` markdown section（含糾正內文；格式相容 `retrieval.py` 的 `## ` parser 與既有 knowledge 編寫規則）。
- [ ] CLI 透過 `get_backend()` 讀寫（沿用 backend 選擇）。

### 2D. 注入路徑（零新 runtime 碼，靠測試與文件釘住）
- [ ] **不新增任何 runtime 注入程式碼**：approved correction 進注入層的唯一途徑是維護者把 `export` 產物貼進 `knowledge/corrections.md` 並經 PR。`knowledge/corrections.md` 一旦存在即被既有 `search_knowledge_base`（`knowledge/*.md` glob）索引。
- [ ] `run_agent()` 注入層（system message、playbook、history 組裝）**完全不讀 corrections 表**。

---

## P2 — 增強（可選，不阻擋 DoD 但建議）
- [ ] Trace summary 一行 structured log（`aws_lambda_powertools` Logger），讓 production 也能走 Logs Insights 快速掃（DB 表仍為主）。
- [ ] `make` 目標：`make traces`（= `python -m agent.trace list`）、`make corrections`（= `python -m agent.corrections list --status pending`）。
- [ ] `python -m agent.trace list` 支援 `--outcome` 過濾。
- [ ] `docs/runbook.md` 補一段：如何查 trace、如何審 corrections、promote 流程（export → 貼 `knowledge/corrections.md` → PR）。

## Out of scope（本 sprint 不做）
- Trace 儀表板 / 告警 / Logs Insights 儀表化。
- LLM 自動偵測糾正、自動分類 correction 主題。
- approved correction 自動寫 md / 自動開 PR / 熱 reload。
- corrections 走 playbook pre-injection。
- 補寫上線前歷史請求的 trace。
- 任何新第三方套件 / embeddings / 向量檢索。

## Tests required

### Trace
- [ ] **各 outcome 落 trace**：mock Groq 分別造出 `completed`（含 1+ 工具呼叫）、`loop_exceeded`、`groq_error`（如 429）、`tool_use_failed_exhausted`；fast-path 命中造出 `fast_path`。各 case `save_trace` 被呼叫一次且 `outcome` 正確。
- [ ] **欄位齊備**：`completed` 且有工具呼叫的 case，trace 內 `playbook_hits`（含 source+score）、`history_msg_count`/`history_char_count`、`tools`（含 `name`/`args`/`duration_ms`/`result_size`/`ok`）、`usage` 總計 = 逐迭代加總、`iterations`、`total_ms >= 0`、`reply_char_count` 皆正確。
- [ ] **零侵入**：注入命中的 case，送給 Groq 的 `messages` 與未加 trace 前一致（可用 spy 斷言 messages 內容），回覆字串不變。
- [ ] **落地失敗不影響回覆**：`save_trace` raise 時 `run_agent` 仍正常回傳原回覆（用 monkeypatch 讓 backend.save_trace 拋例外）。
- [ ] **backend 對稱**：SQLite 與（moto）DynamoDB 兩實作 `save_trace`+`list_traces` round-trip 一致（同輸入回傳等價結構）；`list_traces` 的 `chat_id` / `since` / `limit` 過濾正確；DynamoDB 無 chat_id 時走 GSI。
- [ ] **CLI**：`agent.trace list`（含 `--json`）對已寫入的 trace 有輸出；`--chat-id` 過濾生效。

### Corrections
- [ ] **`/correct` 存 pending**：帶內文 → `save_correction` 存 `pending` + 抓到的 context Q/A；回覆確認訊息；無內文 → 回用法、不存。
- [ ] **狀態機**：`approve`/`reject` 改狀態；`list_corrections(status="pending")` 只回 pending；approved/rejected 不再出現在 pending 清單。
- [ ] **backend 對稱**：SQLite 與（moto）DynamoDB 的 save/list/update round-trip 一致；DynamoDB `list_corrections(status)` 走 `status-index`。
- [ ] **隔離不變式（安全關鍵）**：一次完整 `run_agent()`（注入命中 + 工具呼叫）期間，backend 的 `list_corrections` / correction 讀取方法**零呼叫**（spy 斷言）——結構性證明 corrections 不進注入層。
- [ ] **注入複用**：在 `knowledge/corrections.md` 放一個測試用 `## ` section 後，`search_knowledge_base` / `retrieve` 能命中它並帶 `檔名#標題` 來源；且既有 `knowledge/*.md` 檢索行為不回歸。
- [ ] **CLI**：`agent.corrections` 的 list/show/approve/reject/export 各有一測；`export` 產物含 `## ` 且解析得出 section。

### 共通
- [ ] `tests/conftest.py` 建立 `traces`（含 `trace-time-index` GSI）與 `corrections`（含 `status-index` GSI）兩 DynamoDB 表，既有 fixtures/測試不回歸。
- [ ] `sam validate --region ap-northeast-1 --lint` 通過（若動 `infra/template.yaml`）。
- [ ] 測試離線、決定性、不打網路、不碰真實 AWS / Groq / Finnhub（沿用既有慣例）。

## Definition of done
- 全部 P1 deliverable 打勾（兩 Feature 的最小可用皆交付）。
- `PYTHONPATH=src uv run pytest tests/ -v`（`make test`）全綠。
- chat_cli 或 local_server 實測：一次請求後 `python -m agent.trace list` 查得到該筆 trace（含 token / tools / 耗時）；`/correct` 後 `python -m agent.corrections list --status pending` 查得到（在 QA report 附證據）。
- evaluator 的 QA report 無 P0/P1。
