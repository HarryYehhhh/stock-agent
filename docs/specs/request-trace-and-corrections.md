# Spec: 每次請求結構化 trace + 使用者 corrections 受控回饋迴路

## Goal
讓每次 agent 請求留下一筆可查詢的結構化 trace（注入了什麼 context、叫了哪些工具、token 用量、耗時），並讓使用者的糾正能在**人工審核**後才進入知識注入層——絕不自動注入，結構性地重演不了歷史污染事件。

## Background
兩件事一起做，因為它們都圍繞「讓 agent 行為可觀測、可校正」：

1. **Trace**：使用者先前做 token 減量 e2e 實測（見 MEMORY「Token 減量 e2e 實測基準」）是手動從 CloudWatch logs 逐行判讀、跨行關聯（`playbook_injection`、`groq usage`、`tool succeeded` 散落多行）才拼出「這一題發生了什麼」。目標是把這種分析變成**查表**：一次請求 = 一筆結構化紀錄。

2. **Corrections**：本專案發生過「歷史污染事件」（ADR-0009 Context）——對話歷史全量進 prompt 導致錯誤內容自我強化。因此任何「把使用者輸入回流成知識」的機制，**絕不能自動、無審核地進入注入層**。糾正必須先進待審隔離區，人工核准後、且經過版控（PR）才可能被模型看到。

相關既有資產（沿用、不重寫）：
- `src/agent/agent.py` `run_agent()`：Groq tool-use loop；已有 `playbook_injection` / `groq usage` / `_run_tool` 成功 log（ADR-0009 觀測性）。
- `src/agent/db/backend.py`：`StorageBackend` Protocol + `LocalSQLiteBackend`（本地）/ `DynamoDBBackend`（production）；新表兩邊都要有。
- `src/agent/knowledge/retrieval.py`：BM25 檢索（ADR-0008），`search_knowledge_base` 掃 `knowledge/*.md`（不遞迴）。**corrections 的注入路徑直接複用這套——不新增任何 runtime 注入程式碼。**
- `src/agent/audit/`、`src/agent/knowledge/debug.py`：離線 CLI 的既有風格範本。

## User stories
- As a 維護者, I want 每次請求落一筆含 context/tools/tokens/耗時 的結構化 trace, so that 我做 token 減量或行為分析時是查表而不是判讀 log。
- As a 維護者, I want 本地 chat_cli 實測和 production 請求走同一套 trace 表, so that 我在本地跑 e2e 分析與線上分析用同一個查詢介面。
- As a 使用者, I want 用 `/correct` 明確提交對 bot 回答的糾正, so that 我的回饋不會石沉大海。
- As a 維護者, I want 糾正先進待審隔離區、我核准並經 PR 才進知識庫, so that 沒有任何使用者輸入能未經人工把關就影響模型回答（不重演歷史污染）。

## Design

### Feature 1：Request Trace

#### 1.1 一次請求一筆紀錄
在 `run_agent()` 內部累積一個 `RequestTrace`，涵蓋**所有 return 路徑**（fast-path 命中、正常完成、Groq 錯誤、tool_use_failed 用盡、超過最大迴圈），在函式結束前經 `StorageBackend.save_trace()` 落地一筆。欄位：

| 欄位 | 說明 |
|---|---|
| `chat_id` | 呼叫方 chat_id；`run_agent` 未帶 chat_id（scheduler / chat_cli）時記 sentinel `"__none__"` |
| `ts` | 請求開始時間（UTC ISO8601） |
| `trace_id` | 短亂數 / uuid，避免同秒碰撞、供 SK |
| `user_message` | 當前使用者問題（與 conversations 表同隱私等級，已存全文） |
| `fast_path` | bool；fast-path 命中直接回覆、未進 Groq loop |
| `playbook_hits` | list of `{source, score}`（注入層已算出的命中，分數存 Decimal） |
| `history_msg_count` / `history_char_count` | 注入的對話歷史則數 / 字元數 |
| `tools` | list：每次工具呼叫 `{iteration, name, args(遮蔽 token), duration_ms, result_type, result_size, ok}` |
| `iterations` | 實際發出的 Groq 請求次數 |
| `usage` | 總計 `{prompt_tokens, completion_tokens, total_tokens}` |
| `usage_by_iteration` | 逐迭代 `{prompt, completion}` |
| `total_ms` | run_agent 全程耗時 |
| `outcome` | `completed` / `fast_path` / `groq_error` / `tool_use_failed_exhausted` / `loop_exceeded` |
| `reply_char_count` | 最終回覆字元數 |
| `model` | 模型名 |
| `ttl` | Unix TTL（30 天，對齊 conversations） |
| `bucket` | 常數 `"trace"`，供時間序 GSI |

**硬性約束**：
- **不增加 LLM token 消耗**：trace 純觀測，不改任何送給 Groq 的 messages，只讀 `response.usage`、量測工具耗時、記錄已算出的 playbook 命中。
- **trace 落地失敗不得影響使用者回覆**：`save_trace` 包 try/except、記 warning、絕不 raise 到呼叫端。
- **不改變回覆內容**：所有現行行為（fast-path、注入、工具、錯誤回覆字串）逐字不變。

#### 1.2 落地位置 → StorageBackend（而非只進 CloudWatch）
決策見 **ADR-0013**：trace 以 `StorageBackend` 一筆紀錄落地（DynamoDB 於 production、SQLite 於本地），而非只寫 CloudWatch structured log + Logs Insights。關鍵理由：使用者的 token 分析主戰場是**本地 chat_cli e2e**，CloudWatch-only 方案抓不到本地 run；走 StorageBackend 讓本地與線上共用同一張表、同一查詢介面，且天然可用 moto 測試、TTL 原生。既有 `playbook_injection` / `groq usage` / `tool succeeded` log 全部保留不動。

- DynamoDB 表 `traces`：PK `chat_id`、SK `sk`（`ts#trace_id`）；GSI `trace-time-index`（PK `bucket`=`"trace"`、SK `ts`）供全域時間序查詢（比照既有 NewsTable `published-at-index`）；TTL on `ttl`。
- SQLite：新增 `traces` 表（本地開發捕捉 chat_cli run）。
- Protocol 新增 `save_trace(trace: dict)`（寫）與 `list_traces(chat_id=None, since=None, limit=...)`（讀，供 CLI）。

#### 1.3 查表 CLI
`python -m agent.trace list [--chat-id X] [--since ISO] [--limit N] [--json]`：印出精簡表格（ts / chat_id / outcome / tokens / iterations / tools / total_ms）；`--json` 出完整紀錄供 pandas 等離線分析。這就是「分析變成查表」的落點。風格比照 `agent.audit` / `agent.knowledge.debug`。

### Feature 2：Corrections 受控回饋迴路

決策見 **ADR-0014**。核心：**偵測從簡（顯式指令）、隔離必嚴、注入複用既有知識庫且需 PR 把關**。

#### 2.1 偵測：顯式 `/correct` 指令
在 `processor_handler.py` 加 routing：`/correct <text>`。優於用 LLM 猜「哪句是糾正」（成本與誤判都低，符合使用者建議）。
- 解析糾正內文；用 `get_history(chat_id, limit=2)` 抓上一組 Q/A 當 reviewer 上下文（無歷史則 context 為空，仍受理）。
- 存成一筆 `pending` correction，回覆使用者「已收到，待維護者審核」。
- **不觸發任何注入、不進 run_agent、不改模型行為**。

#### 2.2 隔離：corrections 表 + 狀態機
新表 `corrections`（DynamoDB + SQLite）。狀態 `pending → approved / rejected`。欄位：`correction_id`、`chat_id`、`created_at`、`correction_text`、`context_question`、`context_answer`、`status`、`ttl`（90 天，避免被遺棄的 pending 無限累積；approved 一旦被 promote 進 md 即長存於 repo，DB TTL 無妨）。
- DynamoDB：PK `correction_id`；GSI `status-index`（PK `status`、SK `created_at`）讓 `list_corrections(status)` 為 Query。
- Protocol 新增 `save_correction` / `list_corrections(status)` / `update_correction_status(id, status)`。

#### 2.3 注入：複用既有知識庫 + PR 把關（零 runtime 注入新程式碼）
approved correction 要「變成可注入的知識」，唯一途徑是**維護者把它寫進 `knowledge/corrections.md`**（一個新的術語 KB 檔，被既有 `search_knowledge_base` 的 `knowledge/*.md` glob 自動索引），經 **PR review → merge → deploy** 後才進 image、才可能被模型（透過主動呼叫 `search_knowledge_base`、且附 `檔名#標題` 來源標註）看到。

- 為什麼是 `search_knowledge_base`（術語 KB）而非 playbook pre-injection：術語 KB 由模型主動查詢並附來源，天然不會「每題硬塞」；pre-injection 保證性更強但更接近事件成因（無條件把內容推到 prompt）。校正資訊選較保守的路徑。
- **關鍵安全性質**：`run_agent()` 的注入層**完全不讀 corrections 表**。使用者輸入 → DB 待審 → 人工核准 → 人工寫入 md → PR review → deploy，四道關卡沒有一道是自動的。版控本身就是隔離牆。
- CLI `python -m agent.corrections`：`list [--status pending]` / `show <id>` / `approve <id>` / `reject <id>` / `export [--status approved]`（把 approved 條目輸出成可直接貼進 `knowledge/corrections.md` 的 `## ` markdown section，降低 promote 摩擦）。CLI 讀 production DB（比照 audit 讀 DynamoDB）。

## Acceptance criteria

### Trace
- [ ] `run_agent()` 每次呼叫（含 fast-path、正常、三種錯誤路徑）都落一筆 trace，`outcome` 正確。
- [ ] trace 含：playbook 命中(source+score)、history 則數/字元數、每次工具呼叫(name/args/duration_ms/result 資訊/ok)、逐迭代與總 token、total_ms、reply 字元數。
- [ ] trace 走 `StorageBackend`：DynamoDB（含 `trace-time-index` GSI + TTL）與 SQLite 兩實作對稱；`save_trace` 失敗不影響回覆。
- [ ] 送給 Groq 的 messages 與所有回覆字串**逐字不變**（trace 零侵入行為 / 零額外 token）。
- [ ] `python -m agent.trace list` 能列表、可 `--chat-id` / `--since` 過濾、`--json` 出完整紀錄。

### Corrections
- [ ] `/correct <text>` 存一筆 `pending` correction（含上一組 Q/A 上下文）並回覆使用者已收到待審。
- [ ] corrections 表 DynamoDB（`status-index` GSI + TTL）與 SQLite 兩實作對稱；狀態機 `pending→approved/rejected`。
- [ ] `run_agent()` 注入層不讀 corrections 表（以測試結構性驗證：mock/spy backend 的 corrections 讀取方法在一次 run_agent 中零呼叫）。
- [ ] `python -m agent.corrections` 支援 list/show/approve/reject/export；export 出可貼進 `knowledge/corrections.md` 的 `## ` section。
- [ ] 一旦 `knowledge/corrections.md` 存在含 `## ` section，`search_knowledge_base` 能檢索到它、附 `檔名#標題` 來源（複用既有 retrieval，無新注入碼）。

### 共通
- [ ] `tests/conftest.py` 建好 `traces` / `corrections` 兩 DynamoDB 表（moto），既有測試不回歸。
- [ ] `PYTHONPATH=src uv run pytest tests/ -v` 全綠；測試離線、決定性、不打網路、不碰真實 AWS。
- [ ] 成本不增顯著費用：兩表 on-demand + TTL，量小在免費額度內；trace 不增 LLM token。

## 假設與取捨（背景 sprint，planner 自行決策並記錄）

- **A1（trace 落地 = StorageBackend，非 CloudWatch-only）**：使用者 token 分析主戰場是本地 chat_cli e2e，CloudWatch 抓不到本地 run。StorageBackend 讓本地/線上共用一張表、moto 可測、TTL 原生，直接命中「查表」訴求。代價：多兩張表 + template.yaml 改動。見 ADR-0013。
- **A2（trace all，含 chat_id=None）**：`run_agent` 亦被 chat_cli（本地實測）與 scheduler（每日公司摘要）呼叫且不帶 chat_id。為捕捉使用者實際的本地分析路徑，**trace 所有 run_agent 呼叫**，chat_id=None 時記 sentinel `"__none__"`。副作用：scheduler 每日多筆 `"__none__"` trace——量小、TTL 有界、可用 chat_id 過濾，可接受。若日後噪音困擾，再加 opt-out（本 sprint 不做）。
- **A3（corrections 偵測 = 顯式 `/correct`）**：不猜、不上 LLM 判斷。成本低、誤判低（採用使用者建議）。
- **A4（corrections 注入 = 術語 KB + PR，非 playbook pre-injection、非 runtime 讀 DB）**：選最保守、複用最多既有機制、且有版控把關的路徑，結構性杜絕自動注入。approved→可注入之間刻意保留「人工寫 md + PR」一步，因為 knowledge 是 baked 進 container image 的，本就無法 runtime 熱更新——這個限制正好成為安全特性。見 ADR-0014。
- **A5（`user_message` / correction 內文存全文）**：與既有 conversations 表同隱私等級（已存全文 + chat_id + TTL），不額外脫敏，維持一致。

## Non-goals / Out of scope
- Trace 的線上儀表板 / 自動告警 / Logs Insights 儀表（本 sprint 只到「可查表 + CLI」）。
- 用 LLM 自動偵測「哪句訊息是糾正」、自動分類 correction 主題。
- approved correction 自動寫入 `knowledge/corrections.md` / 自動開 PR / 自動 reload（刻意不做——PR 人工把關即隔離牆）。
- corrections 走 playbook pre-injection（ADR-0014 選術語 KB；日後若證明不夠有效再另案）。
- 為 trace / corrections 引入任何新第三方套件、embeddings、向量檢索。
- 追溯補寫歷史請求的 trace（只涵蓋上線後的新請求）。
