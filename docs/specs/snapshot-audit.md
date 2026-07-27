# Spec: 資料快照稽核 + 離線 LLM-judge 驗證管線（Phase 2）

## Goal
一支純本機離線 CLI 工具：把 DynamoDB 資料 dump 成帶日期、人可 review 的 markdown 快照，並對既有對話的每組 Q/A 用 BM25 檢索快照 + Groq LLM-judge 逐筆稽核「bot 回答是否有資料依據」，輸出稽核報告。

## Background
這是 `docs/specs/playbook-grounding.md` 明列的 **phase 2**，也是使用者最初核心訴求「驗證 LLM 的回答依據」。playbook-grounding（phase 1）已把工具路由指引 pre-inject，但沒有任何機制事後檢查「bot 實際回答的內容是否真的被資料支持」。本 phase 補上這個離線稽核閉環：

1. **資料快照 dump** — 把當前 DynamoDB 內容固化成 markdown，成為稽核的「資料依據 haystack」，同時人可 review / git 追蹤。
2. **LLM-judge 驗證** — 對 conversations 的 Q/A 逐筆，用 Q+A 檢索快照，餵 Groq judge 判定 grounded / ungrounded / no-data。

沿用既有資產（不重寫）：
- `src/agent/knowledge/retrieval.py`：stdlib-only BM25 + CJK bigram，`retrieve_scored(query, top_k, source_dir)` 已參數化來源目錄（ADR-0008 / 0009）。
- boto3 全表 scan 模式：`src/agent/db/download_remote_store.py` 的 `_scan_all`。
- Groq SDK 與 config：`agent.agent.MODEL`（`llama-3.3-70b-versatile`）、`_client()`、`agent.config.groq_api_key()` / `aws_region()` / `conversations_table()` / `earnings_calendar_table()`。
- CLI 形式：比照 `python -m agent.knowledge.debug`，本工具為 `python -m agent.audit <subcommand>`。

## 定位（不可逾越的邊界）
- **純本機離線批次工具**，不部署 Lambda、不改線上 bot runtime、不進 `run_agent()` 熱路徑。
- 「離線」= 後處理批次，非 airgapped：仍讀 DynamoDB（需 AWS 憑證）、仍呼叫 Groq（judge）。
- 不新增檢索依賴（維持 ADR-0008 stdlib-only）。

## User stories
- As a 維護者, I want 把 DynamoDB 的財報行事曆與對話 dump 成帶日期的 md, so that 我能人工 review 與用 git 追蹤某時間點的資料樣貌。
- As a 維護者, I want 對既有對話逐筆自動判定「bot 的回答有沒有資料依據」並列出不被支持的聲明, so that 我能量化 bot 幻覺 / 答非所問的比例，而不用逐則手動核對。
- As a 維護者, I want judge 一視同仁對待 fast-path 與 LLM 回答, so that 兩條回覆路徑的品質都被稽核。

## Design（已定案方向，本 spec 補齊細節）

### 1. 快照範圍（v1）
| 表 | 角色 | 產物 | 首版納入 |
|---|---|---|---|
| `earnings-agent-earnings-calendar` | 稽核 haystack（檢索索引來源） | `snapshots/<date>/earnings.md` | ✅ |
| `earnings-agent-conversations` | 稽核對象（Q/A 來源）+ 人工 review dump | `audit/<date>/conversations.md` | ✅ |
| `earnings-agent-news` | （未來）stock-news 回答的 haystack | — | ❌ 見 Future work |
| market summaries | — | — | ❌ 見下 |

**market summaries 不納入且無獨立產物**：確認 `infra/template.yaml`，production **沒有** market summaries 的獨立 DynamoDB 表；它以 `bucket="market_summary"` 的短 TTL（2 天）快取條目存在 `NewsTable` 內（`db/backend.py` `DynamoDBBackend.insert_market_summary`），非可靠、非可稽核的持久資料源，故 v1 不納入。`news`、`market_summaries`、`company_summaries` 等在 `LocalSQLiteBackend` 才有的本地表不代表 production 有對應可 dump 的資料。

### 2. 目錄佈局與檢索隔離
```
snapshots/            # 資料 haystack，judge 檢索來源（gitignored）
  <date>/
    earnings.md       # 每個 symbol 一個 ## section；retrieve 的 source_dir=snapshots/<date>/
audit/                # 稽核產物（gitignored）
  <date>/
    conversations.md  # conversations 的人工 review dump（不進檢索索引）
    report.md         # judge 稽核報告
```
- **檢索隔離關鍵**：`retrieve_scored` 對 `source_dir` 做非遞迴 `*.md` glob。data haystack（`snapshots/<date>/`）與稽核產物（`audit/<date>/`）分屬**兩棵樹**，確保 `conversations.md` / `report.md` **不會**被 judge 的檢索索引誤收（否則對話內容污染 haystack）。
- 帶日期：以 `<date>` **子目錄**承載日期戳（honors「帶日期快照」需求；需求範例 `snapshots/2026-07-27-earnings.md` 的扁平命名改為 `snapshots/2026-07-27/earnings.md`，理由：子目錄天然把單次快照的 haystack 隔離成一個乾淨的 `source_dir`，扁平命名會讓 `snapshots/*.md` glob 跨日期累積、污染檢索）。

### 3. earnings.md 格式（相容 retrieval.py parser）
- 首個 `## ` 之前放**快照 metadata**（標題 + 產生時間 + 來源表 + row 數）；parser 忽略首個 heading 前內容，故 metadata 人可讀但不進索引。
- 每個 symbol 一個 `## <SYMBOL>` section（heading 得 BM25 2x 權重）；body 逐筆列該 symbol 的財報日期：`- <date>（盤前/盤後/盤中/時段未定）EPS 預估 $X.XX，營收預估 X.XB`（缺值省略該欄）。
- symbol 以字母序、每 symbol 內日期升序排列，確保 diff 穩定。
- **為何 symbol 分 section 對 date 型問題也夠**：judge 的檢索 query = 使用者問題 **+ bot 回答**；廣問題（「本週有哪些公司財報」）的 bot 回答會列出具體 symbol，這些 symbol token 命中對應 section。故不需另做 date 分組 section（避免內容重複與索引膨脹）。

### 4. conversations dump（人工 review）
- `audit/<date>/conversations.md`，按 `chat_id` 分組（`## chat_id=<id>` 或 `## <id>`），組內按 `timestamp` 升序列出每則 `role` / `timestamp` / `content`。
- 純人工 review / git-diff 用途；judge **不**從此檔讀 Q/A（見決策 D2）。

### 5. LLM-judge 流程
對每組 Q/A（見 D3 配對）：
1. query = `使用者問題 + bot 回答`（拼接），對 `snapshots/<date>/`（earnings haystack）跑 `retrieve_scored(query, top_k, source_dir)`。
2. 若檢索**無命中**（空）→ 直接判 `no-data`，**不呼叫 Groq**（省 token）。
3. 有命中 → judge prompt = 「使用者問題 + bot 回答 + 檢索到的 section（含 `檔名#標題` 來源）」餵 Groq（`MODEL`，temperature=0）。
4. judge 回傳精簡結構化判定：`{"verdict": "...", "unsupported_claims": [...], "reason": "..."}`（tolerant 解析：抓第一個 `{...}`）。
5. 三種 verdict：
   - `grounded`：回答的事實聲明都被檢索 section 支持（或回答無可查證的事實聲明，如反問澄清）。
   - `ungrounded`：有聲明不被支持 → `unsupported_claims` 列出。
   - `no-data`：檢索無相關快照可據以判定。
6. fast-path 與 LLM 回答一視同仁（都在 conversations，judge 不分來源）。

### 6. 報告格式（`audit/<date>/report.md`）
- 檔頭：稽核日期、引用的 snapshot 日期、model、統計摘要（grounded / ungrounded / no-data 各幾筆）。
- 每組 Q/A 一個 `## ` section：`chat_id`、問題、回答（過長截斷）、`verdict`、檢索命中的來源（`檔名#標題` + 分數）、`unsupported_claims`、`reason`。

### 7. Token 節約
- judge prompt 精簡；回答與 section 內容在 prompt 內設字元上限截斷。
- `--limit`（預設 10）上限單次稽核的 Q/A 筆數；預設取**最新**的 N 筆。
- 檢索空命中短路，不呼叫 Groq。
- temperature=0（判定決定性、可重跑）。
- 記錄 `response.usage` token（比照 `market_summary`）。

### 8. CLI（`python -m agent.audit <subcommand>`）
- `dump`：`[--date <YYYY-MM-DD>]`（預設今天）`[--table earnings|conversations|all]`（預設 all）。讀 DynamoDB → 寫 `snapshots/<date>/earnings.md`、`audit/<date>/conversations.md`。
- `judge`：`[--date <YYYY-MM-DD>]`（預設今天，指定要 dump 過的日期）`[--chat-id <id>]`（可選過濾）`[--limit <N>]`（預設 10）`[--top-k <K>]`（預設 3）。讀 conversations（DynamoDB）+ 檢索 `snapshots/<date>/` → 寫 `audit/<date>/report.md`。
- 模組結構（如 `agent/audit/__main__.py` + `dump.py` + `judge.py`）由 generator 定。

## Acceptance criteria
- [ ] `python -m agent.audit dump` 從 DynamoDB 讀 earnings-calendar → 產生 `snapshots/<date>/earnings.md`：首個 `## ` 前有 metadata、每 symbol 一 `## <SYMBOL>` section、body 列日期/時段/EPS/營收，且 `retrieve_scored(query, source_dir=snapshots/<date>/)` 能命中對應 symbol section。
- [ ] `python -m agent.audit dump` 產生 `audit/<date>/conversations.md`：按 chat_id 分組、組內按 timestamp 升序。
- [ ] `python -m agent.audit judge` 對每組 Q/A：以 Q+A 檢索快照、命中則餵 Groq judge、產生 `audit/<date>/report.md`（含統計摘要 + 每筆 verdict / 來源 / unsupported_claims / reason）。
- [ ] 檢索空命中 → 判 `no-data` 且**不呼叫 Groq**（有測試鎖住）。
- [ ] `--limit` 上限生效（預設 10，取最新）；`--chat-id` 過濾生效。
- [ ] Q/A 配對正確：同 chat_id 依 timestamp，user 配下一則 assistant；無後續 assistant 的 user 跳過。
- [ ] fast-path 回答與 LLM 回答都被 judge（同一 conversations 來源，不分路徑）。
- [ ] `snapshots/` 與 `audit/` 已加入 `.gitignore`。
- [ ] `PYTHONPATH=src uv run pytest tests/ -v` 全綠（moto mock DynamoDB + mock Groq judge）。
- [ ] 本機實測：dump 真實 DynamoDB 成功；對既有對話跑一輪 judge 產出 report（Groq 額度允許則真跑；額度不足時 judge 以 mock 補證據，但 dump 必須真跑通）。

## Non-goals / Out of scope
- 線上 runtime 即時驗證（進 `run_agent()` 熱路徑做即時 grounding）。
- 排程 / 自動化（先純手動 CLI 觸發）。
- scheduler 寫入端新聞預篩選（另案）。
- embeddings / 向量 / 任何新第三方檢索或 NLP 套件（維持 ADR-0008）。
- 改動線上 bot 行為、prompts、tools、Lambda / IaC。

## Future work（本 phase 之後）
- **news haystack**：`earnings-agent-news` 是 production 表，用同一 dump 機制產 `snapshots/<date>/news.md`（每 symbol 或每分類一 section）納入 judge haystack，讓 stock-news / market-summary 型回答可被 ground，而非一律落 `no-data`。v1 為控制 scope 暫緩。
- **judge 讀快照而非 DynamoDB**：把 conversations 也 dump 成可機器解析格式，讓整條稽核從已提交/固定的快照完全可重現（見 D2 trade-off）。
- **稽核趨勢**：跨日期 report 的 grounded 比例趨勢彙整。

## Open questions / 決策
- **D1（已決，快照範圍）**：v1 只 dump earnings-calendar（haystack）+ conversations（稽核對象），此即需求「至少」兩表。news 延後（Future work，同機制、production 表存在）；market summaries 不做（production 無獨立表，僅短 TTL 快取條目）。
- **D2（已決，judge 的 conversation 來源）**：judge 直接從 **DynamoDB** 讀 Q/A，`audit/<date>/conversations.md` 僅人工 review。理由：避免脆弱的 markdown→Q/A round-trip parser（simplicity first）；來源即權威且最新。Trade-off：judge 讀即時對話但檢索日期快照，有輕微時間錯位、且非從固定快照完全可重現——記於報告 caveat，可接受（稽核為啟發式非證明）。反向（judge 讀快照）列 Future work。
- **D3（已決，Q/A 配對）**：同 chat_id 依 timestamp 升序，每則 user 配**緊接的下一則 assistant**；無後續 assistant 的 user 跳過；非 user/assistant role 忽略。`--limit` 取最新 N 筆 Q/A。
- **D4（已決，.gitignore）**：`snapshots/` 與 `audit/` **都 gitignore**。理由：(1) conversations dump 與 report 內嵌真實使用者對話內容與 chat_id，屬隱私，不應進版控；(2) 快照是從 DynamoDB（真相來源）再生的本機衍生物，非原始碼；(3)「git diff review」在工作目錄本地即可（比對兩個日期目錄的檔案），不需提交即可 review。earnings 雖為公開市場資料，為簡化 .gitignore 規則一併忽略。
- **D5（交由 generator）**：judge system prompt 措辭、JSON 解析容錯細節、prompt 內回答/section 的截斷字元上限，由 generator 定，但須：verdict 三分類、reason 用繁體中文、附 `檔名#標題` 來源、temperature=0。
- **D6（交由 generator）**：`top_k` 預設值（建議 3）與 metadata section 具體欄位，由 generator 定。
