# ADR-0013: 請求 trace 以 StorageBackend 一筆紀錄落地（非 CloudWatch-only）

- **Date**: 2026-07-29
- **Status**: Accepted
- 相關：ADR-0003（StorageBackend Protocol 雙實作）、ADR-0004（DynamoDB production 表）、ADR-0009（既有 `playbook_injection` / tool log 觀測性）

## Context
使用者要為每次 agent 請求留一筆結構化 trace（注入的 context、工具呼叫與耗時、逐迭代 token、總耗時），動機是先前做 token 減量 e2e 實測時，是**手動從 CloudWatch logs 逐行判讀、跨行關聯**（`playbook_injection`、`groq usage`、`tool succeeded` 散落多行）才拼出一次請求的全貌。目標是把這種分析變成**查表**。

需要決定 trace 落在哪：(1) CloudWatch structured log + Logs Insights，或 (2) StorageBackend 一筆紀錄（DynamoDB / SQLite）。約束：不增加 LLM token、不引入顯著費用、要能離線測試、要對使用者的實際分析流程有用。

關鍵事實：`run_agent()` 不只被 production processor 呼叫，也被**本地 `chat_cli`**（使用者做 token 分析的主戰場）與 scheduler 呼叫。CloudWatch 抓不到本地 run。

## Decision
1. **trace 走 `StorageBackend`**：`run_agent()` 每次呼叫累積一個 trace，經新增的 `save_trace()` 落一筆——production 進 DynamoDB `traces` 表、本地進 SQLite `traces` 表。本地與線上共用同一張表、同一查詢介面。
2. **DynamoDB 表**：PK `chat_id`、SK `ts#trace_id`，GSI `trace-time-index`（PK `bucket="trace"`、SK `ts`）供全域時間序查詢（比照 NewsTable `published-at-index`），TTL 30 天（對齊 conversations）。
3. **trace all**：`run_agent` 未帶 chat_id（chat_cli / scheduler）時 chat_id 記 sentinel `"__none__"`，以捕捉本地實測路徑。
4. **零侵入 / 零額外 token**：trace 純觀測，不改任何送給 Groq 的 messages，只讀 `response.usage`、量測工具耗時、記錄已算出的 playbook 命中；`save_trace` 失敗包 try/except、絕不影響使用者回覆。
5. **既有 log 全留**：`playbook_injection` / `groq usage` / `_run_tool` 成功 log 不動；trace 是聚合層，非取代。
6. **查表 CLI**：`python -m agent.trace list`（`--chat-id` / `--since` / `--json`），比照 `agent.audit` 風格，這是「分析變成查表」的落點。

## Consequences
- 正面：本地 chat_cli 與 production 用同一張表、同一 CLI 分析——直接服務使用者的實際 token 分析流程，CloudWatch-only 做不到。
- 正面：一次請求一筆結構化紀錄，不再跨行關聯；沿用既有 StorageBackend 雙實作 + moto 測試 + TTL，零新依賴。
- 正面：DynamoDB/SQLite on-demand + TTL + 低流量，落在免費額度內；trace 不增 LLM token。
- 負面 / trade-off：多兩張表結構與 `infra/template.yaml` 改動、Protocol 多兩個方法（兩實作都要寫）。相對 CloudWatch-only 的「零基礎設施」較重，但換得本地覆蓋與查表體驗。
- 負面 / trade-off：DynamoDB 本身無 ad-hoc 聚合（Logs Insights 有）；跨請求統計（平均 token、p95 延遲）需 export 後用 pandas 等離線算。可接受——使用者要的是「查表 + 離線分析」，非線上儀表；且 P2 可補一行 summary log 讓 Logs Insights 仍可用。
- 負面：`run_agent` 每次多一次 DynamoDB put（300s Lambda 中延遲可忽略）。

## Alternatives considered
- **CloudWatch structured log + Logs Insights（唯一落點）**：零基礎設施、原生聚合查詢，但**抓不到本地 chat_cli run**（使用者分析主戰場）、與本地分析介面不一致、仍是「查 log」而非「查表」，且正是使用者覺得痛的既有方式的延伸。否決為唯一落點；保留為 P2 補充（一行 summary log）。
- **既有 conversations 表塞 trace 欄位**：省一張表，但污染對話資料模型、TTL / 查詢語義不同、trace 欄位巢狀複雜，破壞既有 schema。否決。
- **只 sentinel 不 trace 內部呼叫（chat_id=None 不落 trace）**：少了 scheduler 噪音，但也丟掉 chat_cli 本地實測 trace——與動機衝突。否決，改 trace all + sentinel + TTL + 可 chat_id 過濾。
- **獨立時序資料庫 / 第三方 observability SaaS**：查詢與聚合最佳，但引入依賴與費用，違反 ~$0/月 與離線可測約束。否決。
