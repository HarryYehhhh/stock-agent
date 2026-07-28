# ADR-0014: Corrections 回饋迴路——DB 待審隔離 + 人工核准 + PR 促進，絕不自動注入

- **Date**: 2026-07-29
- **Status**: Accepted
- 相關：ADR-0009（playbook pre-injection；本 ADR 引以為戒的「無條件把內容推進 prompt」風險）、ADR-0008（`knowledge/*.md` BM25 檢索，本 ADR 複用其為注入路徑）

## Context
使用者要把「使用者的糾正」回流成知識。但本專案發生過**歷史污染事件**（ADR-0009 Context）：對話歷史全量進 prompt 導致錯誤內容自我強化污染。因此任何「使用者輸入 → 知識 → 影響模型」的迴路，**絕不能自動、無審核地進入注入層**，否則等於重開污染的門。

需決定：(1) 怎麼偵測糾正；(2) 糾正存哪、什麼狀態；(3) 核准後「變成可注入的知識」的具體路徑與把關；(4) 注入用哪套機制。

關鍵限制（轉為安全特性）：`knowledge/` 是 baked 進 container image 的，本就**無法 runtime 熱更新**——這剛好可當作「使用者輸入到達 prompt 前必經一次 code/PR/deploy」的天然閘門。

## Decision
1. **偵測從簡：顯式 `/correct <text>` 指令**。不猜、不上 LLM 判斷「哪句是糾正」——成本低、誤判低。提交時一併抓上一組 Q/A（`get_history(limit=2)`）當 reviewer 上下文。
2. **隔離：`corrections` 表 + 狀態機**。`/correct` 一律存 `pending`，狀態 `pending → approved / rejected`。存的當下**不注入、不進 `run_agent`、不改模型行為**。DynamoDB（PK `correction_id`、GSI `status-index`）+ SQLite 雙實作，TTL 90 天（遺棄的 pending 自然過期）。
3. **注入路徑 = 術語 KB（`knowledge/corrections.md`）+ PR 把關，零 runtime 新碼**。approved correction 要「變成可注入」，唯一途徑是維護者用 CLI `export` 產出 markdown，貼進 `knowledge/corrections.md`，經 **PR review → merge → deploy**。該檔一旦存在即被既有 `search_knowledge_base`（`knowledge/*.md` glob，ADR-0008）索引，模型**主動查詢**時才看到，且附 `檔名#標題` 來源標註。
4. **`run_agent()` 注入層完全不讀 `corrections` 表**（以測試釘死的不變式）。使用者輸入 → DB 待審 → 人工核准 → 人工寫 md → PR review → deploy，**四道關卡沒有一道自動**。版控本身就是隔離牆。
5. **維運 CLI**：`python -m agent.corrections list/show/approve/reject/export`，讀 production DB（比照 `agent.audit`）。

## Consequences
- 正面：結構性杜絕「使用者輸入自動進 prompt」——每一步都要人。直接回應歷史污染事件的教訓。
- 正面：複用既有 `knowledge/*.md` 檢索，注入路徑零新 runtime 程式碼；correction 走「模型主動查 + 附來源」的保守路徑，天然不會每題硬塞。
- 正面：`knowledge/` 無法熱更新的既有限制被轉化為安全特性（必經 PR/deploy）。
- 正面：偵測用顯式指令，實作小、無誤判、無額外 token。
- 負面 / trade-off：approved 到「真的生效」之間仍需人工貼 md + 開 PR，非一鍵——刻意如此（把關 > 便利）。用 `export` 產可貼 markdown 降低摩擦。
- 負面 / trade-off：靠使用者主動 `/correct`，不會捕捉「沒下指令的隱性糾正」——可接受（LLM 猜測成本與誤判都高，違反 simplicity）。
- 負面 / trade-off：correction 走 `search_knowledge_base` 需模型自覺查詢，非保證注入；但這正是相對 pre-injection 更安全的取捨（見 ADR-0009 對無條件注入的顧慮）。若日後證明不夠有效，再另案評估 playbook 路徑。

## Alternatives considered
- **LLM 自動偵測糾正 + 自動分類**：省使用者一個指令，但成本高、誤判高、且「自動」本身與安全目標衝突。否決。
- **approved correction runtime 從 DB 讀並注入**：即時生效、免 PR，但把使用者輸入直接接回注入層——正是要避免的自動注入模式，狀態 gating 一旦有 bug 就重演污染。否決。
- **approved 自動寫入 `knowledge/corrections.md` + 自動開 PR**：少一步人工，但少一道人眼把關，且 knowledge 仍需 deploy 才生效、自動 PR 價值有限。否決，保留人工 promote。
- **corrections 走 playbook pre-injection（ADR-0009 機制）**：命中即注入、保證性強，但更接近污染成因（無條件推進 prompt）。校正資訊選更保守的術語 KB 路徑。否決（本 phase）。
- **corrections 併入既有 `knowledge/*.md` 現有檔而非獨立 `corrections.md`**：省一個檔，但混淆「人工策展知識」與「使用者衍生待信任知識」的來源分界，也不利於單獨 review / rollback。否決，獨立成檔。
