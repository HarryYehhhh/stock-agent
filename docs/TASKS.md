# 執行計劃 — DB-first 架構：本地測完 → 一次部署 AWS

> 更新日期：2026-07-23（v3：發派對象改為現行 agent 陣容——實作類任務走 `feature`/`sprint` skill，infra 與監控分派 `infra-engineer`/`monitoring-engineer`；v2 取代原 T1–T5 清單）。
> 背景：webhook 是 bot 全域設定，目前指向 API Gateway，但 DB-first watchlist 流程為 local-only、
> DynamoDB 新表未部署，故雲端跑不動流程 A。決策：**先在本地開發測試完成，再全部一起部署到 AWS**。
>
> 已定案的決策（2026-07-23 與使用者確認）：
> 1. 本地接 Telegram 訊息：**ngrok 切換**（dev 期間雲端 bot 暫停，唯一使用者、無實質影響）
> 2. 本地每日任務：**launchd/cron 自動跑**
> 3. Production 遷移：**拆 2–3 個 PR**
> 4. 雲端每日摘要送達方式：**scheduler 每日推播給訂閱者**

---

## 進度（2026-07-24）

- ✅ **已完成區（原 T1–T3）**
- ✅ **Phase 0（ngrok webhook 切換）** — 實作完成（PR #17）
- ✅ **Phase 1（launchd 每日排程）** — 實作完成（PR #17）；⏳ 連跑 2–3 天實地驗證待使用者執行
- ⬜ **Phase 2（production 遷移，3 PR）** — 待辦（依 v3 決策仍在路線圖上，非封存）
- ⬜ **Phase 3（部署與切換）** — 待辦

> 註：另有 fix-local-webhook-500 / -sqs-500 / fix-launchd-env-loading（PR #17）與 remove-watchlist-exchange（PR #18）於本地開發期一併修掉，見 `docs/CHANGELOG.md`。

---

## 已完成（原 T1–T3，不需再做）

- ✅ T1 `/market-summary`、`/news` endpoint 整合測試（PR #14）
- ✅ T2 文件 drift 修正：manual-test 函式名、CLAUDE.md（PR #14）
- ✅ T3 手動測試 + QA report：`docs/qa-reports/manual-test-watchlist-notify.md`、`manual-test-market-summary.md`（PR #15 端到端驗證）
- ✅ 財報通知 local 路徑（sync/notify 拆分、per-subscriber、冪等、摘要快取）
- ✅ watchlist 指令 + symbol 驗證、DB Strategy Protocol、每日摘要函式層

---

## Phase 0 — Telegram 訊息導回本地（ngrok）✅ 實作完成（PR #17）

- **發派對象**：`feature` skill（generator 實作 → evaluator 驗收）
- **相依**：無，可立即執行
- **範圍**：
  - Makefile 新增 target：`set-webhook-local`（讀 ngrok URL 設 webhook 指向本地 `/webhook`）、`delete-webhook`（`deleteWebhook` API）；現有 `set-webhook` 保留作為切回雲端用
  - `docs/deployment-flow.md` 補一段「本地開發 webhook 切換」操作說明（ngrok 啟動 → 切換 → 測完切回）
- **驗收標準**：`ngrok http 8080` + `make set-webhook-local WEBHOOK_URL=<ngrok URL>` 後，Telegram 訊息進到本機 FastAPI；`make set-webhook WEBHOOK_URL=<API Gateway>` 可切回
- **注意**：secret_token 驗證要與 `webhook_handler` 一致，本地端也要帶

## Phase 1 — 本地每日自動運轉（launchd/cron）✅ 實作完成（PR #17）；⏳ 2–3 天實地驗證待執行

- **發派對象**：`feature` skill（generator 實作 CLI 與 plist → evaluator 驗收）＋使用者（launchd 安裝需本人執行）
- **相依**：Phase 0 完成
- **範圍**：
  1. **每日摘要 CLI 入口**：目前 `generate_market_summary` 只能從 FastAPI 觸發。新增 CLI（如 `python -m agent.market_summary` / `make daily-summary`）：`refresh_news` → `generate_market_summary` → **推播給 active 訂閱者**（對齊已定案的雲端行為，本地先驗證推播體驗）
  2. **launchd plist**（macOS）：每日定時跑 `make sync-earnings` → `make notify-earnings` → `make daily-summary`，log 導到檔案
  3. 失敗可見性：任一步驟失敗時至少留 log（不做告警系統，本地階段夠用）
- **驗收標準**：連續 2–3 天無人工介入，Telegram 每天收到財報通知（若 watchlist 有近期財報）與科技股摘要；log 可查
- **產出**：手測結果追加到 QA report 或 `docs/CHANGELOG.md`
- **本階段同時是驗證期**：收集 stale 資料、Groq rate limit、通知時間（時區）等問題，作為 Phase 2 spec 的輸入

## Phase 2 — Production 遷移（拆 3 個 PR，各走 `sprint` skill）

- **相依**：Phase 1 運轉數天且無重大問題後啟動；planner 先出 spec/contract，並把 Phase 1 發現的問題納入
- **共同原則**：production 行為對齊 local（存 DB → 讀 DB、per-subscriber、冪等）；moto 測試覆蓋新 backend 方法

### PR-1：DynamoDB 新表 + `DynamoDBBackend` 補實作
- watchlist（PK=chat_id, SK=symbol + symbol GSI，設計草案見 `docs/specs/per-subscriber-watchlist.md:65-73`）、earnings 快取、通知冪等、公司摘要快取等表 —— 具體表設計由 planner 細化（含 TTL 策略）
- `DynamoDBBackend` 移除 no-op / `NotImplementedError`，補齊 Protocol 之外 production 需要的方法
- `infra/template.yaml` 表定義 + 各 Lambda IAM 調整
- `db/upload_local_store.py` 是否納入新表（本地資料要不要搬上雲）由 planner 確認

### PR-2：scheduler 遷移到 sync/notify 拆分
- Lambda 上改走 `sync_earnings` + `_notify_local` 同等路徑，移除 `_notify_live` 舊流程與全域 `DEFAULT_WATCHLIST` 依賴
- EventBridge 排程設計（sync 與 notify 分兩個觸發、或同一次執行先 sync 後 notify）由 planner 決定，注意 UTC/台北時區
- processor 的 `/watch`、`/unwatch` 在 DynamoDB 模式解除「僅本地模式支援」限制

### PR-3：每日科技股摘要雲端化
- news / market_summaries 儲存搬到 DynamoDB backend（或由 planner 評估摘要是否需要持久化）
- SchedulerFunction 每日推播摘要給 active 訂閱者（已定案）
- Finnhub / Groq 呼叫的 timeout 與 Lambda 300s 限制核對

## Phase 3 — 部署與切換

- **發派對象**：`infra-engineer` 準備 checklist；使用者執行部署；部署後觀察與健康檢查派 `monitoring-engineer`
- **相依**：Phase 2 三個 PR 全部合併
- **步驟**：
  1. `make ssm-put`（確認四把 key 都在 SSM）
  2. `make build` + `make deploy`
  3. `make set-webhook WEBHOOK_URL=<API Gateway>` 切回雲端
  4. 雲上手測：`/subscribe`、`/watch`、隔日通知、每日摘要
  5. `make logs-scheduler` / `logs-processor` 觀察數天
  6. 停用本地 launchd 排程（避免雙重推播）
- **驗收標準**：雲端跑通流程 A（watchlist 財報通知）與流程 B（每日摘要），本地排程已停

---

## 執行順序

```
Phase 0（ngrok 切換，feature skill）
  ↓
Phase 1（本地 CLI + launchd，feature skill，運轉 2–3 天驗證）
  ↓
Phase 2（sprint × 3：PR-1 DB 表+backend → PR-2 scheduler → PR-3 摘要雲端化）
  ↓
Phase 3（部署 infra-engineer、觀察 monitoring-engineer、切回 webhook、停本地排程）
```
