# 執行計劃 — DB-first 架構：本地測完 → 一次部署 AWS

> 更新日期：2026-07-24（v5：**方向再修正——daily pipeline 上雲端**（走「B」，ADR-0006）。使用者定案不再停在 hybrid local-first：把本地 launchd daily pipeline 搬上雲端 EventBridge，全程直讀 DynamoDB。原 PR-2/PR-3 重排為 **PR-A（雲端 pipeline 完整化）＋ PR-B（observability + schedule ENABLE）**，接續 Phase 3 ops cutover。詳見下方「Roadmap v5」。v4 的 hybrid 敘述保留供追溯）。
> 更新日期：2026-07-24（v4：Phase 2/3 實際走向與原計劃分岔——PR-1 完成後，daily pipeline 未如原 PR-2/PR-3 計劃搬上雲，而是改為 **hybrid local-first + DynamoDB 為 source of truth**（ADR-0005，PR #22/#24）。原 PR-2/PR-3 標記為 superseded，Phase 3 部分已發生。詳見下方「現況架構」）。
> v3：發派對象改為現行 agent 陣容——實作類任務走 `feature`/`sprint` skill，infra 與監控分派 `infra-engineer`/`monitoring-engineer`；v2 取代原 T1–T5 清單。
> 背景：webhook 是 bot 全域設定，目前指向 API Gateway，但 DB-first watchlist 流程為 local-only、
> DynamoDB 新表未部署，故雲端跑不動流程 A。決策：**先在本地開發測試完成，再全部一起部署到 AWS**。
>
> 已定案的決策（2026-07-23 與使用者確認）：
> 1. 本地接 Telegram 訊息：**ngrok 切換**（dev 期間雲端 bot 暫停，唯一使用者、無實質影響）
> 2. 本地每日任務：**launchd/cron 自動跑**
> 3. Production 遷移：**拆 2–3 個 PR** → 實際只做了 PR-1，其餘由 ADR-0005 hybrid 方向取代（見下）
> 4. 雲端每日摘要送達方式：~~scheduler 每日推播給訂閱者~~ → **改由本地 launchd 每日推播**；雲端 `SchedulerFunction` 刻意 `State: DISABLED`（ADR-0005）

---

## 進度（2026-07-24）

- ✅ **已完成區（原 T1–T3）**
- ✅ **Phase 0（ngrok webhook 切換）** — 實作完成（PR #17）
- ✅ **Phase 1（launchd 每日排程）** — 實作完成（PR #17）；⏳ 連跑 2–3 天實地驗證待使用者執行
- 🔀 **Phase 2（production 遷移）** — **方向已調整**：只做完 **PR-1**（DynamoDB 表 + backend，#20/#21）。原 **PR-2/PR-3 superseded**，改由 ADR-0005 的 hybrid 方向取代（見下）。
- 🔀 **Phase 3（部署與切換）** — **部分已發生**：webhook 已切回雲端、雲端 processor 寫 DynamoDB；雲端 daily scheduler 刻意維持 `DISABLED`，daily pipeline 由本地 launchd 執行。

> 註：另有 fix-local-webhook-500 / -sqs-500 / fix-launchd-env-loading（PR #17）與 remove-watchlist-exchange（PR #18）於本地開發期一併修掉，見 `docs/CHANGELOG.md`。

---

## 現況架構（hybrid local-first，ADR-0005）

原 Phase 2/3 假設「把 daily pipeline 整包搬上 Lambda/EventBridge」。實際走向分岔為 **hybrid**：

- **雲端**：webhook 在 API Gateway，`processor` 處理 Telegram 輸入（`/watch`、`/unwatch`、`/subscribe`、`/unsubscribe`）並寫入 DynamoDB —— DynamoDB 為這些輸入的 **source of truth**。雲端 `SchedulerFunction` 刻意 `State: DISABLED`（`infra/template.yaml:310`），不跑雲端 daily 推播。
- **本地（launchd 兩個排程）**：
  - **00:00 下行**：`make sync-from-dynamodb`（`agent.db.download_remote_store`）把 DynamoDB `watchlist` + `subscribers` 完整鏡射回本地 sqlite（ADR-0005）。
  - **10:30 pipeline**：`sync-earnings` → `notify-earnings` → `daily-summary`，再上行**只搬本地快取**（`earnings_calendar` / `company_summaries` / `news`）到 DynamoDB。
- **資料流**：Telegram 輸入 → DynamoDB（權威）→ 本地 mirror（每日 00:00）；本地快取 → DynamoDB（僅快取，每日 10:30）。細節見 `docs/local-daily-schedule.md`、`docs/decisions/0005-*.md`。

### 這批「PR-2 期」實際交付（對照原計劃）
- ✅ **PR-1**：DynamoDB 4 表 + `DynamoDBBackend` 補實作（#20），日期區間邊界測試 follow-up、inspection make targets + 本地排程調 10:30（#21）。
  - 副作用：`add_to_watchlist`/`remove_from_watchlist` 在 DynamoDB 模式已實作，故 `/watch`、`/unwatch` **雲端已可用**（原 PR-2 目標之一，已由 PR-1 達成；`LOCAL_ONLY_MESSAGE` 現只在 `NotImplementedError` 時觸發）。
- ✅ **sync-local-to-dynamodb（#22）**：本地快取（news/earnings/company summaries）每日上行 DynamoDB。
- ✅ **dynamodb-downsync（#24，ADR-0005）**：反轉資料流方向，新增 00:00 下行，移除 watchlist/subscribers/conversations/dedup 的上行（修掉「stale 本地 watchlist 在 10:30 回寫覆蓋雲端刪除」的 bug）。

### 剩餘 / 待確認 — ✅ 已定案（走 B：上雲端，ADR-0006）
- ✅ **Open question 已定案**：daily pipeline **搬上雲端 EventBridge**（非永久留本地）。故 `_notify_live`、`get_watchlist(None)` 的 `DEFAULT_WATCHLIST` 分支、`_run()` 的 `selected_backend_name()` 分岔改造為雲端正式路徑或移除——見下方「Roadmap v5」PR-A。

---

## Roadmap v5（daily pipeline 上雲端，ADR-0006）

方向：把本地 launchd daily pipeline 搬上雲端 `SchedulerFunction`，全程直讀 DynamoDB（source of truth）。原 PR-2/PR-3 重排如下。

### PR-A — 雲端 daily pipeline 完整化（走 `sprint` skill）✅ spec/contract 已出
- **spec**：`docs/specs/cloud-daily-pipeline.md`；**contract**：`docs/contracts/cloud-daily-pipeline-sprint.md`
- **範圍**：`DynamoDBBackend` 補 `insert_news` / `list_news_since` / `insert_market_summary`(no-op)、升格進 Protocol；`scheduler_handler.handler()` 單次跑完整 pipeline（sync → 財報通知 → 大盤摘要）；移除 `_notify_live` 與 `get_watchlist(None)` 死碼、`_run()` backend 分岔；template `SchedulerFunction` IAM 補 `NewsTable` CRUD。
- **關鍵取捨（ADR-0006 §3 定案）**：news 落 DynamoDB（NewsTable 已存在）；market_summaries 雲端**不持久化**（純稽核、production 不讀回、no-op）。
- **重要**：schedule **維持 `DISABLED`**，故本 PR 對 production **無行為變更**，可獨立 deploy 無風險。
- **不含**：SNS/alarm、schedule ENABLE、deploy。

### PR-B — Observability + schedule ENABLE（走 `sprint` skill；go-live PR）
- **相依**：PR-A 合併。
- **範圍**：
  - SNS Topic + email subscription（email 位址以 SAM parameter 參數化，可搭 SSM，不 hardcode）。
  - CloudWatch Alarm：至少 `SchedulerFunction` 與 `ProcessorFunction` 的 Lambda `Errors` → SNS；（可選）scheduler duration 逼近 300s timeout、Telegram push 失敗。
  - `SchedulerFunction` `ScheduleV2`：`DISABLED` → `ENABLED`，`ScheduleExpressionTimezone: Asia/Taipei` + `cron(30 10 * * ? *)`（沿用現行本地 10:30 台北，Phase 1 已驗證；確切時間可於本 PR 與使用者最終確認，預設＝保留 10:30）。
- **硬需求**：observability 與 schedule ENABLE 綁同一 PR——第一次雲端 run（＝ENABLE）起就要能觀測失敗（ADR-0006 §6）。
- **雙推風險**：ENABLE 後若本地 launchd 仍跑會重複推播（冪等 store 分屬兩 backend、不共用）。故本 PR 的 cutover 部署必須與「停本地 launchd」協調（見 Phase 3）。

### Phase 3 ops（infra-engineer / 使用者；不在 code PR 內）
- 停本地 launchd（daily 10:30 pipeline + 00:00 downsync；downsync 上雲後失去用途）。
- `make deploy`（含 PR-B 的 ENABLE + SNS/alarm），部署後雲端驗證（`make logs-scheduler` 觀察首次 run、確認 email alarm 管道、確認無雙推）。
- 順序：先停本地 launchd → 再部署含 ENABLE 的 template（避免雙推窗口）。

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

### PR-2：scheduler 遷移到 sync/notify 拆分 — ⛔ SUPERSEDED（ADR-0005）
> 未執行。方向改為 daily pipeline 留在本地 launchd、雲端 scheduler DISABLED。`/watch`、`/unwatch` 的 DynamoDB 支援已由 PR-1 達成。原範圍保留供追溯：
- ~~Lambda 上改走 `sync_earnings` + `_notify_local` 同等路徑，移除 `_notify_live` 舊流程與全域 `DEFAULT_WATCHLIST` 依賴~~（`_notify_live` / `DEFAULT_WATCHLIST` 現為死碼，處置見上方「剩餘 / 待確認」）
- ~~EventBridge 排程設計~~
- ✅ processor 的 `/watch`、`/unwatch` 在 DynamoDB 模式解除「僅本地模式支援」限制（已由 PR-1 達成）

### PR-3：每日科技股摘要雲端化 — ⛔ SUPERSEDED（ADR-0005）
> 未執行。摘要仍由本地 launchd（`make daily-summary`）每日推播；news 已上行 DynamoDB（#22）但讀取仍走本地。原範圍保留供追溯：
- ~~news / market_summaries 儲存搬到 DynamoDB backend~~
- ~~SchedulerFunction 每日推播摘要給 active 訂閱者~~（改由本地 launchd 推播）
- ~~Finnhub / Groq 呼叫的 timeout 與 Lambda 300s 限制核對~~

## Phase 3 — 部署與切換 — 🔀 部分已發生

- **發派對象**：`infra-engineer` 準備 checklist；使用者執行部署；部署後觀察與健康檢查派 `monitoring-engineer`
- **相依**：~~Phase 2 三個 PR 全部合併~~ → 只做 PR-1；其餘由 ADR-0005 hybrid 取代
- **現況**：webhook 已切回雲端 API Gateway、雲端 `processor` 部署並寫 DynamoDB（依 ADR-0005 前提與 dockerfile-groq-dep 修復紀錄推得）；雲端 daily scheduler 維持 DISABLED，daily pipeline 由本地 launchd 執行。

> ✅ **雲端排程狀態（2026-07-24 已修正）**：EventBridge `SchedulerFunction` 現已在
> `infra/template.yaml:310` 明確設 `State: DISABLED`，`make deploy` 不會再把它拉回 ENABLED。
> 因此本地 launchd（00:00 下行 + 10:30 pipeline）與雲端不會雙推。
> （先前版本此處警告 disable 不在 template 內——該狀況已不成立。）
- **步驟**：
  1. `make ssm-put`（確認四把 key 都在 SSM）
  2. `make build` + `make deploy`
  3. `make set-webhook WEBHOOK_URL=<API Gateway>` 切回雲端
  4. 雲上手測：`/subscribe`、`/watch`、隔日通知、每日摘要
  5. `make logs-scheduler` / `logs-processor` 觀察數天
  6. 停用本地 launchd 排程（避免雙重推播）
- **驗收標準**：雲端跑通流程 A（watchlist 財報通知）與流程 B（每日摘要），本地排程已停

---

## 執行順序（實際 vs. 原計劃）

原計劃：
```
Phase 0（ngrok 切換）→ Phase 1（本地 CLI + launchd）
  → Phase 2（PR-1 DB 表+backend → PR-2 scheduler → PR-3 摘要雲端化）
  → Phase 3（部署、切回 webhook、停本地排程）
```

實際走向（分岔於 Phase 2 後）：
```
Phase 0 ✅ → Phase 1 ✅
  → PR-1（DB 表+backend）✅
  → sync-local-to-dynamodb（#22，快取上行）✅
  → dynamodb-downsync（#24 / ADR-0005，反轉方向：DynamoDB source of truth + 00:00 下行）✅
  → hybrid 上線：webhook 回雲端 + 雲端 scheduler DISABLED + 本地 launchd 跑 daily
  ⛔ 原 PR-2 / PR-3（scheduler & 摘要雲端化）superseded，未執行
```

