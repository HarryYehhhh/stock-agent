# QA Report: tech-stock-focus

- **Contract**: 無 contract（feature skill，直接對照需求描述 + 程式碼品質驗收）
- **Date**: 2026-07-31
- **Verdict**: ✅ Pass（round 2 🔴 Critical 已修復；round 3 為文件對齊追加輪，round 2 的 🟡 P2 已一併解決，全數驗收通過）

## 複驗記錄（round 3：文件對齊 — CLAUDE.md / README.md / tool description）

範圍：`git diff`（未 commit，前兩輪已合併於 `b782201`）涉及 `CLAUDE.md`、`README.md`、`src/agent/tools/market_summary_tool.py` 三個檔案，無其他程式邏輯改動。核心驗收標準是「敘述必須與 code 一致」，逐項對照 code 如下：

- **`market_summary_tool.py:18`**："Big 7 tech stock news" → "major tech stock news"。解決了 round 2 記錄的 🟡 P2。`grep -rn "Big 7 tech stock news\|MARKET_SUMMARY_TOOL\[.description.\]"` 確認無任何測試斷言這段文字，改動不影響測試。
- **DynamoDB 表總數與 schema**：`infra/template.yaml` 實際有 **11** 個 `AWS::DynamoDB::Table` 資源（`ConversationsTable`/`SubscribersTable`/`ProcessedUpdatesTable`/`WatchlistTable`/`EarningsCalendarTable`/`EarningsNotificationsTable`/`CompanySummariesTable`/`NewsTable`/`RateLimitsTable`/`TracesTable`/`CorrectionsTable`），與 CLAUDE.md「11 DynamoDB tables」及 Schema 表逐條核對：
  - 每張表的 PK/SK 型別、GSI 名稱（`earning-date-index`/`published-at-index`/`trace-time-index`/`status-index`）、TTL 啟用與否，皆與 `infra/template.yaml` 的 `KeySchema`/`GlobalSecondaryIndexes`/`TimeToLiveSpecification` 逐一比對相符。
  - TTL 天數（conversations 30天、earnings-calendar 45天、earnings-notifications 30天、company-summaries 30天、news 14天、traces 30天、corrections 90天、market-summary 快取項目 2天、dedup 1小時）皆與 `src/agent/db/backend.py`（`CONVERSATION_TTL_SECONDS`/`EARNINGS_CALENDAR_TTL_SECONDS`/`EARNINGS_NOTIFICATIONS_TTL_SECONDS`/`COMPANY_SUMMARIES_TTL_SECONDS`/`NEWS_TTL_SECONDS`/`MARKET_SUMMARY_TTL_SECONDS`/`CORRECTIONS_TTL_SECONDS`/`DEDUP_TTL_SECONDS`）及 `src/agent/agent.py`（`TRACE_TTL_SECONDS = 30 * 24 * 3600`）的常數值完全一致，無虛構數字。
- **`db/` 段落的敘述**：`DynamoDBBackend.__init__`（`db/backend.py:1080-1092`）確認 11 個 table handle 皆在 `__init__` 建立一次並重用，singleton 語意由 `get_backend()` 的 `@lru_cache(maxsize=1)` 提供——與 class docstring 幾乎逐字相符，CLAUDE.md 敘述準確。`insert_market_summary()`（`db/backend.py:1503-1529`）確認 DynamoDB 版確實把摘要快取寫進 `self._news`（即 `NewsTable`）並帶 `bucket="market_summary"`，`MARKET_SUMMARY_TTL_SECONDS = 2 * 24 * 3600`；local 版另有獨立 `market_summaries` table。CLAUDE.md「借 NewsTable bucket="market_summary"」的敘述準確。
- **EventBridge 排程**：`infra/template.yaml:417-436` 確認 `SchedulerFunction` 掛兩個 `ScheduleV2`：`SyncSchedule`（`cron(0 8 * * ? *)`, `Asia/Taipei`, `Input: {"mode": "sync"}`）與 `NotifySchedule`（`cron(0 10 * * ? *)`, `Asia/Taipei`, `Input: {"mode": "notify"}`），與 CLAUDE.md/README 的「08:00 sync / 10:00 notify」完全相符。`scheduler_handler.py` 的 `mode == "sync"` → `sync_earnings()` + 新聞 refresh、`mode == "notify"` → 財報通知（24h 內）+ `push_daily_summary(refresh=False)`，與敘述一致。
- **SNS/CloudWatch alarms**：`infra/template.yaml:442-520` 確認 1 個 SNS topic（`PipelineAlarmsTopic`）+ 1 個 email subscription + 4 個 `AWS::CloudWatch::Alarm`（`SchedulerErrorsAlarm`/`ProcessorErrorsAlarm`/`SchedulerDurationAlarm`/`DlqMessagesAlarm`），與 CLAUDE.md「SNS alarm topic + 4 CloudWatch alarms（scheduler/processor errors, scheduler duration, DLQ messages）」完全相符。
- **Processor routing**：`processor_handler.py:88-148` 確認指令集為 `/start`/`/help`、`/subscribe`、`/unsubscribe`、`/watchlist`、`/watch`/`/watch <SYMBOL>`、`/unwatch`/`/unwatch <SYMBOL>`、`/correct`/`/correct <text>`，與 CLAUDE.md 敘述逐一相符。
- **`tools/` 段落**：`get_market_summary` 補充「cache miss 也會對 12 檔 `TECH_SYMBOLS` 各打一次 `get_quote`」的敘述，與 `market_summary.py` 的 `fetch_stock_quotes(TECH_SYMBOLS)` 呼叫路徑一致（見 round 1/2 已驗證過的程式邏輯）。
- **README Architecture 段落**：三個 Lambda 的 timeout（webhook 10s、processor 300s）、單一 Docker image + `ImageConfig.Command` 差異化、DynamoDB on-demand billing、`infra/template.yaml` 統一佈建，皆與 `infra/template.yaml`（`WebhookFunction: Timeout: 10`、`ProcessorFunction: Timeout: 300`、`BillingMode: PAY_PER_REQUEST`）相符。`ls plan.md` 確認檔案確實不存在，移除該死連結正確。
- **README Quick start**：本輪未改動內容，逐條核對 `Makefile` 目標（`test`/`dev`/`ssm-put`/`build`/`deploy`/`set-webhook`）皆存在，generator「全部有效未改」的判斷正確。
- **測試**：`PYTHONPATH=src uv run pytest tests/ -v` → **555 passed / 13 errors, 0 failed**（13 個 error 為既有 sandbox socket bind 限制，與 round 2 一致），文件與 tool description 改動未影響任何測試。

**發現**：本輪核對的所有敘述皆與 code 相符，**無 Critical、無新增 P2**。額外觀察到一項與本輪改動範圍無關的既有殘留（見下方 🟢 Suggestions 新增項）：`src/agent/scheduler_handler.py:7` 的模組 docstring 仍寫「refresh Big7/AI news」，是本次 sprint 未觸及的殘留措辭（`grep -rn "Big7\|Big 7" src/` 唯一命中處），不影響本輪驗收的 CLAUDE.md/README/tool description 三個檔案，記錄供未來另案處理。

## 複驗記錄（round 2）

第一輪發現 🔴 Critical：`BIG7_ALIASES` 未涵蓋新增 5 檔（`AVGO`/`AMD`/`TSM`/`ORCL`/`NFLX`），導致 `is_headline_relevant()` fallback 只比對裸 ticker，真實 Finnhub headline（公司全名措辭）幾乎過不了 filter。已回送 generator 修正，本輪複驗結果：

- `src/agent/tools/news.py`：`BIG7_ALIASES` → `TECH_SYMBOL_ALIASES`，補齊 5 檔別名（`AVGO: Broadcom`、`AMD: Advanced Micro Devices`、`TSM: TSMC, Taiwan Semiconductor`、`ORCL: Oracle`、`NFLX: Netflix`）；`_BIG7_HEADLINE_PATTERNS` → `_TECH_SYMBOL_HEADLINE_PATTERNS`，pattern 建構邏輯不變（`re.escape` + `\b...\b` + `re.IGNORECASE`）。全域 `grep -rn "BIG7_ALIASES\|_BIG7_HEADLINE_PATTERNS"` 無殘留。
- `tests/test_tools.py::test_is_headline_relevant_matches_ticker_or_alias`：新增 9 組真實措辭 headline（"Broadcom unveils new AI chip"、"TSMC posts record quarterly profit"、"Taiwan Semiconductor raises capex guidance"、"Oracle wins major cloud contract"、"Netflix beats subscriber estimates" 等），其中多組**不含裸 ticker**，若別名缺漏會直接 fail，不再是假陽性測試。
- `test_is_headline_relevant_unknown_symbol_falls_back_to_ticker_match` 的示例從 `NFLX`（已入別名表，不再適合當「未知 symbol」示例）改為 `IBM`，邏輯正確。
- `tests/test_market_summary.py::test_select_news_covers_the_new_tech_symbols_beyond_the_original_big7` 整個重寫：改用專屬 `fake_company_news` 餵入 `REAL_HEADLINES` dict（每檔皆為公司全名措辭、無裸 ticker，如 `"AVGO": "Broadcom unveils new AI chip"`），端到端驗證 `refresh_news()` → `select_news()` 能正確選到這 5 檔的新聞。docstring 明確寫出這是為了避免上一輪「裸 ticker mock 掩蓋別名缺漏」的假陰性問題，判斷精準。
- 實跑測試：`PYTHONPATH=src uv run pytest tests/ -v` → **555 passed / 13 errors**（同一批既有 sandbox socket bind `PermissionError`，與本次改動無關，與 baseline 一致）。較第一輪的 548 passed 淨增 7（9 組新 parametrize case − 部分測試結構調整後的淨值），無 regression，無 failed。
- 結論：Critical 已確實解除，新測試不再依賴裸 ticker，能有效防止同類假陰性再發生。

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. 追蹤清單擴大為 12 檔，常數更名 `TECH_SYMBOLS` | ✅ | `src/agent/market_summary.py:36-39`：`TECH_SYMBOLS` = 12 檔，順序、內容與需求一致；全域無殘留 `BIG7` 常數引用（`grep -rn "\bBIG7\b" src/` 無結果） |
| 2. `generate_market_summary()` user content 新增個股報價區塊 | ✅ | `market_summary.py:342-346, 363-370`：新增 `fetch_stock_quotes(TECH_SYMBOLS)`，`[科技股個股報價]` 區塊插在指數之後、新聞之前；單檔失敗 skip 不中斷（`fetch_stock_quotes` 249-266 行邏輯與既有 `fetch_index_quotes` 一致，try/except + `current_price` 檢查）；cache hit 路徑（322-334 行）在呼叫 `fetch_stock_quotes` 之前就 return，確認不抓報價 |
| 3. `prompts.py` 僅兩處小修（措辭不限定 Big 7、刪成交量條件） | ✅ | 最終檔案 `grep -n "成交量\|Big 7" src/agent/prompts.py` 無結果；`SUMMARY_SYSTEM_PROMPT_TEMPLATE` 其餘結構改動確認為使用者手改（worktree 起始 `git status` 已顯示 `M src/agent/prompts.py`，generator 未觸碰） |
| 4. Token 預算 `MAX_NEWS_PER_SYMBOL` 3→2，取捨合理性 | ✅ | 見下方「Token 預算評估」；round 1 的前提缺陷（🔴-1）已於 round 2 修復，24 則預算上限現在對 12 檔皆可實際用滿 |
| 5. DB `category` 值 `"big7"` 保留不改 | ✅ | `market_summary.py:150`、`select_news()` 內部 dict key `"big7"` 均保留；`grep -rln "big7"` 掃過的 tests（`test_storage_backend.py`/`test_upload_local_store.py`/`test_download_remote_store.py`/`test_audit_dump.py`/`test_tools.py`）確認皆為既有 DB category 字串或 dict key，非殘留的 Python 常數引用 |
| 6. 測試覆蓋（12 檔報價區塊、單檔失敗 skip、新清單新聞選取，全 mock） | ✅ | `tests/test_market_summary.py` 新增 6 個測試：`test_fetch_stock_quotes_skips_failures`、`test_fetch_stock_quotes_covers_all_12_tech_symbols`、`test_generate_summary_user_content_includes_all_12_stock_quotes`、`test_generate_summary_skips_a_single_failed_stock_quote_without_aborting`、`test_select_news_covers_the_new_tech_symbols_beyond_the_original_big7`、baseline byte-for-byte 測試更新；全部走 `monkeypatch`/mock，無真實外部呼叫 |

## Token 預算評估（需求 4）

- 舊：7 檔 × 3 則 = 21 則個股新聞上限；新：12 檔 × 2 則 = 24 則，+14%，同時每檔新聞行數從 3→2 略降深度換取廣度，理論上是合理取捨（總量增幅可控，且新增報價區塊本身只多 12 行短文字，非新聞正文，token 增量有限）。
- **但**此估算假設 12 檔在 `select_news()` 都能實際選到新聞。經 code review（見 🔴-1），`AVGO/TSM/ORCL/NFLX`（很可能還有部分情境下的 `AMD`）在真實 Finnhub headline 上幾乎選不到新聞，因為 `is_headline_relevant()` 的 fallback 只比對裸 ticker 字面（word-boundary），而這 4-5 檔的真實新聞標題慣用公司全名（"Broadcom"、"Taiwan Semiconductor"/"TSMC"、"Oracle"、"Netflix"），不會命中。也就是說 24 則的預算上限在生產環境很難被用滿，實際個股新聞則數可能還低於改動前的 21 則基準，只是「插了 5 個幾乎打不到新聞的坑位」。
- 結論：**24 vs 21 這個 token 數學本身合理，但支撐這個數學的『12 檔都有新聞可選』前提不成立**，需與 🔴-1 一併修。修完 🔴-1 後這裡不需要再重新評估數學本身。
- **Round 2 更新**：🔴-1 已修復（`TECH_SYMBOL_ALIASES` 補齊 5 檔別名），前提成立，24 則預算上限對 12 檔可實際用滿，取捨判定維持合理，本項改判 ✅。

## Test results

### Round 1
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（worktree 內執行，`/Users/yehhungwei/stock-agent/.worktrees/tech-stock-focus`）
- 結果：548 passed / 13 errors，0 failed
- 失敗測試：無（13 個 error 全為 `tests/test_local_integration.py` 內本地 TCP stub server 在 sandbox 下 `socket.bind` 觸發的 `PermissionError`，與本次改動無關）
- Baseline 比對：在同一 worktree 對 `main`（`git stash` 還原到改動前）重跑同一指令，結果為 542 passed / 13 errors（同一批 error）。本次改動淨增 6 passed（對應需求 6 新增的 6 個測試），無 regression。
- 既有 suite regression：✅ 無

### Round 2（複驗 🔴-1 修復）
- 測試指令：同上
- 結果：**555 passed / 13 errors，0 failed**（同一批既有 sandbox socket bind error，與 round 1 一致）
- 較 round 1 淨增 7（`test_is_headline_relevant_matches_ticker_or_alias` 新增 9 組 parametrize case，扣掉其他測試結構微調後的淨值）
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）— ✅ 已於 round 2 修復並複驗確認
- ~~**`src/agent/tools/news.py:52-82`**（`BIG7_ALIASES` / `is_headline_relevant()`）：`BIG7_ALIASES` 只涵蓋原 7 檔公司名別名，新增的 5 檔（`AVGO`/`AMD`/`TSM`/`ORCL`/`NFLX`）落入 `is_headline_relevant()` 的 fallback 分支（79-81 行），只比對裸 ticker 字面（`\b{symbol}\b`，word-boundary、不分大小寫）。實務上 Finnhub headline 幾乎不會用裸 ticker 稱呼這些公司——常見寫法是公司全名或另一種代碼："Broadcom"（AVGO）、"Oracle"（ORCL）、"Netflix"（NFLX）、"Taiwan Semiconductor" 或 "TSMC"（TSM——注意 `\bTSM\b` 連 "TSMC" 都不會命中，因為 M 後面緊接 C 沒有 word boundary）。`AMD` 因為業界常直接用裸 ticker 稱呼，命中率可能較好，但另外 4 檔（尤其 `TSM`/`ORCL`/`NFLX`/`AVGO`）在生產環境的新聞供給會被這個 filter 幾乎全數濾掉。~~
  - ~~**為何是問題**：這直接打中本次 sprint 的核心交付（需求 1：追蹤清單擴大為 12 檔）。12 檔清單擴大若有 4-5 檔實質上拿不到新聞，「科技股焦點」段落對這幾檔只剩報價數字沒有新聞脈絡，使用者體感上這幾檔的資訊價值遠低於其餘 7-8 檔，且需求 4 的 token 預算取捨（24 vs 21）前提也因此不成立（見上方評估）。~~
  - ~~**測試覆蓋盲點**：`test_select_news_covers_the_new_tech_symbols_beyond_the_original_big7`（`tests/test_market_summary.py:305`）雖然涵蓋了「新 5 檔新聞能流過 select_news」，但用的 mock headline 是 `f"{symbol} story {i}"`（例如 "TSM story 0"、"AVGO story 0"），字面就包含裸 ticker，剛好命中 fallback pattern，讓測試通過但完全沒有模擬真實世界的 headline 措辭，因此沒能抓到這個問題——目前測試套件對這個 gap 是假陰性（false negative）。~~
  - **歷史脈絡**：上一輪 `docs/qa-reports/news-headline-relevance.md`（2026-07-28）的 🟢 Suggestion 已明確預警「若之後 Big 7 名單變動需要同步維護 `market_summary.BIG7` 與 `BIG7_ALIASES` 兩處」——這次名單確實變動了，但兩處未同步維護，這正是當初預警的風險兌現。
  - **修復狀態（round 2）**：`BIG7_ALIASES` → `TECH_SYMBOL_ALIASES`，補齊 5 檔別名（`AVGO: Broadcom`、`AMD: Advanced Micro Devices`、`TSM: TSMC, Taiwan Semiconductor`、`ORCL: Oracle`、`NFLX: Netflix`）；回歸測試改用不含裸 ticker 的真實措辭 headline 端到端驗證，能有效防止別名缺漏再次滑過測試。詳見上方「複驗記錄（round 2）」。

### 🟡 Warnings（Should Fix）— ✅ 已於 round 3 修復
- ~~**`src/agent/tools/market_summary_tool.py:18`**：`MARKET_SUMMARY_TOOL` 的 `description`（面向 LLM 的 tool schema 說明）仍寫 "Big 7 tech stock news, AI industry news and major index performance"，未反映追蹤清單已擴大到 12 檔科技股。~~
  - **修復狀態（round 3）**：改為 "major tech stock news"，確認無測試斷言此文字，改動安全。

### 🟢 Suggestions
- **`src/agent/market_summary.py:203-213`**（`select_news()`）：內部區域變數與回傳 dict key 沿用 `big7`（例如 `big7: list[dict] = []`），與外部常數 `TECH_SYMBOLS` 命名不一致。這是需求 5 明確要求保留（避免牽動 DB `category` 值與既有消費端），非缺陷，僅記錄供未來若真的要做全面更名時參考。
- **`src/agent/scheduler_handler.py:7`**（round 3 新發現，範圍外觀察）：模組 docstring 仍寫「sync earnings calendar and refresh Big7/AI news into DynamoDB」，是 `grep -rn "Big7\|Big 7" src/` 目前唯一的殘留命中，與本輪驗收的 `CLAUDE.md`/`README.md`/`market_summary_tool.py` 無關（generator 本輪未觸碰此檔，屬合理範圍界定）。不阻塞，記錄供未來若要徹底清理措辭時一併處理。

### ✅ What looks good
- `fetch_stock_quotes()`（`market_summary.py:249-266`）完整複用 `fetch_index_quotes()` 已驗證過的 skip-on-failure 模式（try/except + `current_price` 檢查），風格一致，未重新發明邏輯。
- Cache hit 路徑正確地在呼叫任何報價抓取（含新的 `fetch_stock_quotes`）之前就 return，符合「cache hit 不打 Finnhub」的既有設計與本次需求 2 的要求。
- `days_back=1` 的 byte-for-byte baseline 測試（`test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_baseline`）與 prompt 側對應測試都同步更新並鎖住新格式，避免生產環境（`push_daily_summary` 固定用 `days_back=1`）的 user_content 意外漂移。
- `TECH_SYMBOLS` 常數重新命名後，全域搜尋確認無殘留的舊 `BIG7` 常數引用；DB `category`/dict key `"big7"` 字串保留範圍精準，未過度更名波及既有資料相容性。
- 測試改動全部維持既有 mock 風格（`monkeypatch`、`MagicMock`），無真實外部呼叫，符合專案測試慣例。

## 結論
- 是否可合併：**是**（round 3 複驗：文件對齊改動全數與 code 相符，round 2 的 🟡 P2 已修復，555 passed / 13 既有 sandbox error / 0 failed，無 regression，無新增 Critical/P2）
- 最該先處理的 1–3 件事：
  1. （🟢 可選，不阻塞）`scheduler_handler.py:7` docstring 殘留「Big7」措辭，範圍外發現，未來另案順手清理即可。
