# Contract: snapshot-audit（Phase 2 資料快照稽核 + 離線 LLM-judge）

對應 spec: docs/specs/snapshot-audit.md
相關 ADR: docs/decisions/0010-snapshot-audit-offline-llm-judge.md

## 前置
- 純本機離線 CLI，**不**改線上 runtime（`run_agent()` / prompts / tools / Lambda / IaC）。
- 沿用既有：`agent.knowledge.retrieval.retrieve_scored(query, top_k, source_dir)`、`db/download_remote_store._scan_all` 的 boto3 scan 模式、`agent.agent.MODEL` / `_client()`、`agent.config`（`groq_api_key` / `aws_region` / `conversations_table` / `earnings_calendar_table`）。
- 不新增第三方檢索 / NLP 依賴（ADR-0008 stdlib-only）。

## Deliverables

### A. CLI 骨架
- [ ] 新增套件（建議 `src/agent/audit/`），`python -m agent.audit <subcommand>` 提供兩個子命令：`dump`、`judge`。模組拆分（`__main__.py` / `dump.py` / `judge.py`）由 generator 定。

### B. `dump` 子命令
- [ ] 參數：`--date <YYYY-MM-DD>`（預設今天）、`--table earnings|conversations|all`（預設 all）。
- [ ] earnings：boto3 全表 scan `earnings_calendar_table()` → 寫 `snapshots/<date>/earnings.md`：
  - 首個 `## ` 之前為 metadata（標題、產生時間、來源表、row 數）——parser 忽略此段。
  - 每 symbol 一個 `## <SYMBOL>` section；body 逐筆 `- <earning_date>（<盤前/盤後/盤中/時段未定>）EPS 預估 $X.XX，營收預估 X.XB`，缺值省略該欄（`extended_hour` 空 → 「時段未定」）。
  - symbol 字母序、每 symbol 內日期升序（diff 穩定）；`Decimal` 型 eps/revenue 正確格式化。
- [ ] conversations：boto3 全表 scan `conversations_table()` → 寫 `audit/<date>/conversations.md`：按 `chat_id` 分組、組內按 `timestamp` 升序，列 `role` / `timestamp` / `content`。
- [ ] 目錄不存在時自動建立。

### C. `judge` 子命令
- [ ] 參數：`--date`（預設今天，須為已 dump 的日期）、`--chat-id`（可選過濾）、`--limit <N>`（預設 10）、`--top-k <K>`（預設 3）。
- [ ] 從 DynamoDB 讀 conversations（非讀 md），配對 Q/A：同 chat_id 依 `timestamp` 升序，每則 user 配緊接下一則 assistant，無後續 assistant 的 user 跳過；`--limit` 取最新 N 筆 Q/A、`--chat-id` 過濾。
- [ ] 每筆：query = `問題 + 回答` → `retrieve_scored(query, top_k, source_dir=snapshots/<date>/)`。
  - 檢索空命中 → verdict `no-data`，**不呼叫 Groq**。
  - 有命中 → 餵 Groq judge（`MODEL`、temperature=0），judge 回傳 `{"verdict": grounded|ungrounded|no-data, "unsupported_claims": [...], "reason": "<繁中>"}`，tolerant 解析（抓第一個 `{...}`）。
- [ ] 記錄 `response.usage` token（比照 `market_summary.py`）。
- [ ] 寫 `audit/<date>/report.md`：檔頭統計摘要（三類各幾筆 + 引用 snapshot 日期 + model），每筆一 `## ` section 含 `chat_id` / 問題 / 回答（過長截斷）/ verdict / 檢索來源（`檔名#標題` + 分數）/ unsupported_claims / reason。

### D. 錯誤處理
- [ ] `judge` 指定日期的 `snapshots/<date>/earnings.md` 不存在 → 明確報錯提示先跑 `dump`（非 traceback）。
- [ ] DynamoDB scan 失敗 → 印錯誤訊息、非 0 exit（比照 `download_remote_store`）。

### E. .gitignore
- [ ] `snapshots/` 與 `audit/` 加入 `.gitignore`（隱私：conversations / report 內含使用者對話與 chat_id）。

## Out of scope
- news / market summaries 快照（news 列 Future work）。
- 線上即時驗證、排程自動化、scheduler 新聞預篩。
- 改線上 bot 行為 / prompts / tools / Lambda / IaC。
- embeddings / 向量 / 新第三方套件。
- 修改 `retrieval.py` 的 BM25 / 斷詞核心（僅**使用** `retrieve_scored`，可傳 `source_dir`；若需新增純加法的 helper 不改既有行為則可）。

## Tests required（moto mock DynamoDB + mock Groq）
- [ ] dump earnings：seeded earnings（含 Decimal eps/revenue、空 hour、多 symbol）→ 斷言 md 有 metadata 段、每 symbol 一 `## ` section、格式/時段標籤/排序正確；且 `retrieve_scored` 對產出檔能命中對應 symbol section。
- [ ] dump conversations：seeded 多 chat_id / 多 timestamp → 斷言分組與時序。
- [ ] judge 正常路徑：dump 出的 earnings.md + seeded conversations + mock Groq 回 canned JSON → 斷言 report 含 verdict / 來源 / 統計。
- [ ] judge 空命中短路：query 無法命中任何 section → 斷言 verdict `no-data` 且 **Groq client 未被呼叫**。
- [ ] `--limit` 上限與取最新；`--chat-id` 過濾。
- [ ] Q/A 配對：user→下一 assistant；懸空 user 跳過；fast-path 型回答（純字串答案）與 LLM 回答都被納入配對。
- [ ] tolerant JSON 解析：judge 回傳被文字包裹的 JSON 仍能解析出 verdict。
- [ ] CLI argparse smoke（子命令與參數預設值）。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v` 全綠。
- 本機實測：`python -m agent.audit dump`（真實 DynamoDB）產出 `snapshots/<date>/earnings.md` + `audit/<date>/conversations.md`；`python -m agent.audit judge` 對既有對話跑一輪產出 `audit/<date>/report.md`（Groq 額度允許則真跑，附一則真實 verdict 佐證；額度不足則 judge 部分以 mock 補證據，但 dump 必須真跑通並附產出檔片段）。
- evaluator QA report 無 P0/P1。
