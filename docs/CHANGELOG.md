# CHANGELOG

Harness 執行記錄。**最新的在上面**（reverse chronological）。

每條由 planner / evaluator 在自己跑完後追加，main Codex 不直接寫。
格式：
```
## YYYY-MM-DD hh:mm — <sprint 或 feature> — <agent> — <一句話結果>
- 連結：spec / contract / qa-report / ADR
- 驗證：Unit <result>；Integration <not required / pass / fail>；E2E <not required / pass / fail>；External Smoke <not requested / pass / fail>（blocking: <true/false>）
- 備註：循環次數、未解問題、後續工作（若有）
```

---

<!-- 新條目從這裡開始 -->

## 2026-07-26 — earnings-week-forward — evaluator — ✅ Pass
- 連結：docs/qa-reports/earnings-week-forward.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙箱內 329 passed/12 errors（既有 `test_local_integration.py` socket bind 沙箱限制，非回歸）、停用沙箱重跑 341/341 passed（與 generator 自報一致）；🔴 0、🟡 1（「本週」在財報行事曆規則（未來視角）與市場摘要規則（過去 7 天）中語意相反，雖有工具情境句區隔，對 llama-3.3-70b 仍有理論上混淆風險，屬 prompt 純文字驗收的固有限制，不阻塞）；循環次數 1；未解 P0/P1 0/0。Diff 僅動 `src/agent/prompts.py` 兩處工具選擇策略段落 + `tests/test_prompts.py` 2 個新測試，符合限定範圍；未修改既有 `days_back`/市場摘要 baseline 規則。可合併。

## 2026-07-26 — deploy-pipeline-hardening（round 2，最終） — evaluator — ✅ Pass
- 連結：docs/qa-reports/deploy-pipeline-hardening.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 停用沙箱重跑 332/332 passed（與 round 1、generator 自報一致）；`sam validate --lint` valid；🔴 0、🟡 0；循環次數 2；未解 P0/P1 0/0。round 1 的兩個 🔴 已修正並複驗（親自用 stub aws/sam binary 重跑，非僅信自報）：🔴1 `smoke-test-processor` 改用 `if ! INVOKE_META=$$(... 2>&1); then exit 1; fi` 包住 `aws lambda invoke`，親測成功/FunctionError/CLI 呼叫本身失敗三分支皆正確 fail-closed（CLI 失敗分支不再誤判為通過），暫存 response file 三分支皆確認清理，且完整 `make deploy` 鏈路（stub sam+aws）exit code 正確傳播；🔴2 `.github/workflows/deploy.yml` 在 `SAM deploy` step 後新增 `Post-deploy smoke test` step 跑 `make smoke-test-processor STACK=earnings-agent REGION=ap-northeast-1`，確認位置/credentials 沿用/region 一致/working directory/YAML 語法皆正確。新增 1 條 🟢（`2>&1` 混合 stdout/stderr 在極低機率「invoke 成功但 stderr 有雜訊」情境下仍可能誤判，不阻塞）；round 1 的 🟡（`$(STACK)`/`AppName` 命名耦合）本輪未處理，維持邊界情境不影響合併。可合併。

## 2026-07-26 — deploy-pipeline-hardening（round 1） — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/deploy-pipeline-hardening.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙箱內 320 passed/12 errors（既有 `test_local_integration.py` socket bind 沙箱限制，非回歸）、停用沙箱重跑 332/332 passed（與 generator 自報一致）；`sam validate --lint` valid；🔴 2、🟡 1；循環次數 1；未解 P0 2。🔴1：`smoke-test-processor` 只檢查回應內 `.FunctionError`，未檢查 `aws lambda invoke` 指令本身的 exit code——invoke API 呼叫層級失敗（函式不存在/權限/節流/網路）時 stdout 為空、jq 靜默回傳空字串，被誤判為「通過」且 exit 0（已用 stub aws binary 實測重現此假通過）。🔴2：`.github/workflows/deploy.yml` 的 `deploy` job（push to main 實際生產部署路徑，即造成 2026-07-24 事故的路徑）仍直接呼叫 `sam deploy`，未串接本次的 smoke test，需向使用者確認是否也要補上。其餘需求（processor invoke payload 無副作用已用新測試鎖住、alarm 沿用既有資源改 Period 300→60 的取捨合理）皆已驗證通過。可合併前需先修 🔴1，🔴2 待使用者決定範圍。

## 2026-07-26 — trading-day-summary-gate — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/trading-day-summary-gate.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙箱內 318 passed/12 errors（既有 `test_local_integration.py` socket bind 沙箱限制，非回歸）、停用沙箱重跑 330/330 passed（與 generator 自報一致）；🔴 0、🟡 1（`push_daily_summary()` 非交易日 skip log 用 `date.today()`（系統/UTC）而非 `_is_trading_day()` 實際判斷的 America/New_York 日期，Lambda 環境下兩者在 10:00 Taipei notify 執行時系統性差一天，會誤導依賴 log 日期欄位的每日健檢流程，不影響推播正確性本身）；循環次數 1；未解 P0/P1 0/0。需求 1-5（NY 日期判斷、週末不打 API、假日清單+tradingHour 空字串、半日仍推、API 失敗 fail-open、scheduler_handler.py/refresh_news 完全未動）皆已逐條驗證通過，8 條新測試涵蓋週末/全日休市/半日/平日/fail-open/API 參數/skip 整合，全 mock 無真實 API；既有 `push_daily_summary` 測試補的 `_is_trading_day → True` monkeypatch 經實測確認為必要修補（今日為週日，若無此 monkeypatch 既有測試會被新 gate 擋下失敗）。可合併，🟡 可視情況先合併後補或本輪回修。

## 2026-07-26 — rate-limit-and-replay（round 2，最終） — evaluator — ✅ Pass
- 連結：docs/qa-reports/rate-limit-and-replay.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙箱內 310 passed/12 errors（既有 `test_local_integration.py` socket bind 沙箱限制，非回歸）、停用沙箱重跑 322/322 passed（與 generator 自報一致）；🔴 0、🟡 0；循環次數 2；未解 P0/P1 0/0。round 1 的 🔴（`increment_rate_limit` UpdateExpression 子句順序 `ADD...SET` 違反 DynamoDB 官方 `SET,REMOVE,ADD,DELETE` 固定順序）已修正為 `SET...ADD`；round 1 的 🟡（webhook 內同步呼叫 Telegram 有超過 10s Lambda timeout 風險）已修正為新增 `send_plain_message(timeout=3.0)` 且無 Markdown 重試；另外 `check_rate_limit()` 呼叫本身也補了 fail-open try/except，新增 `test_rate_limit_check_failure_fails_open_and_still_enqueues` 實際檢查 SQS 收到訊息（非只信任回傳值）驗證行為。可合併。

## 2026-07-26 — rate-limit-and-replay（round 1） — evaluator — ❌ Fail
- 連結：docs/qa-reports/rate-limit-and-replay.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙箱內 309 passed/12 errors（既有 `test_local_integration.py` socket bind 沙箱限制，非回歸）、停用沙箱重跑 321/321 passed（與 generator 自報一致）；🔴 1、🟡 1；循環次數 1；未解 P0 1（`DynamoDBBackend.increment_rate_limit` 的 `UpdateExpression` 子句順序為 `ADD ... SET ...`，疑似違反 DynamoDB 官方要求的 `SET, REMOVE, ADD, DELETE` 固定順序，moto 測試偽陽性通過，真實環境可能對每則訊息拋 `ValidationException` 且 webhook 未包 try/except，等同全站中斷；建議改成 `SET ... ADD ...`）。其餘需求項目（rate limiter 限流邏輯、通知只發一次的原子性、infra 權限範圍、上一題重放邏輯與不寫史）皆已驗證正確。
## 2026-07-26 — market-summary-cache — evaluator — ✅ Pass
- 連結：docs/qa-reports/market-summary-cache.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 299/311 passed（沙箱下 12 個 `test_local_integration.py` 因 socket bind 被擋，停用沙箱重跑全綠 26/26，非 regression，合計 311/311）；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0/0。`generate_market_summary` 以 `(summary_date, days_back, news_ids_hash)` 為 key 快取 Groq 輸出，命中不打 Groq 也不重打 `fetch_index_quotes()`；`get_market_summary`/`push_daily_summary` 呼叫端相容性核對皆通過；SQLite `market_summaries` 補三欄 + ALTER TABLE migration（冪等、僅 `_init_schema()` 執行一次）；DynamoDB `insert_market_summary` 由 no-op 改為寫入既有 NewsTable（`bucket="market_summary"`，~2 天 TTL，未動 infra）；ADR-0006 §3 以附加修訂段落記錄，未重寫原文；測試涵蓋同天不重打 Groq（兩個 backend）、news 集合變重打、days_back 不共用、SQLite 舊 schema migration、DynamoDB TTL 屬性正確共 5 類情境。

## 2026-07-25 — time-consistency-fixes — evaluator — ✅ Pass
- 連結：docs/qa-reports/time-consistency-fixes.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 304/304 passed（沙箱下 12 個 `test_local_integration.py` 因 socket bind 被擋，停用沙箱重跑全綠，非 regression）；🔴 0、🟡 0、🟢 2；循環次數 1；未解 P0/P1 0/0。兩條 days_back=1 byte-for-byte 凍結測試獨立比對 diff 確認測試本身未被修改，且 PASS；days_back>1「當日」標註在 system prompt 與 user_content 均到位；`tool_use_failed` 400 重試邏輯與非 `tool_use_failed` 仍拋出、`MAX_TOOL_LOOPS` 耗盡三種情境皆有對應測試且斷言 call_count/訊息內容；確認重試提示不會污染對話歷史（`processor_handler.py` 只存 `text`/`reply`，非整個 `messages`）。

## 2026-07-25 — summary-timewindow — evaluator — ✅ Pass（round 2 複驗，最終狀態）
- 連結：docs/qa-reports/summary-timewindow.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 297/297 passed（沙箱下 12 個 `test_local_integration.py` 因 socket bind 被擋，停用沙箱重跑全綠，非 regression）；🔴 0、🟡 0（round 1 的 1 條已回修）、🟢 2；循環次數 2；未解 P0/P1 0/0。回修採修法 (a)：`SUMMARY_SYSTEM_PROMPT_TEMPLATE`/`user_content` 的日期區間提示改為僅 days_back>1 生效，evaluator 獨立比對 `git show HEAD:src/agent/prompts.py` 舊版常數確認 days_back<=1 逐字（byte-for-byte）恢復，並以 mutation 測試驗證新增回歸測試具備真實偵測力。

## 2026-07-25 — summary-timewindow（round 1） — evaluator — ⚠️ Pass with notes
- 連結：docs/qa-reports/summary-timewindow.md（round 1 段落；本輪因 harness 限制未能落地檔案，由主流程代為寫入）
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 295/295 passed（沙箱下 12 個 `test_local_integration.py` 因 socket bind 被擋，停用沙箱重跑全綠，非 regression）；🔴 0、🟡 1（`SUMMARY_SYSTEM_PROMPT_TEMPLATE` 對 days_back=1 也新增了「須註明日期區間」指示，非需求 5 要求的零變化，會影響 `push_daily_summary()` 每日排程推播的實際文字）、🟢 2；循環次數 1；已於 round 2 回修並複驗通過。

## 2026-07-25 — market-summary-logging（follow-up） — evaluator — ✅ Pass
- 連結：docs/qa-reports/market-summary-logging.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 223/223 passed + 12 errors（`test_local_integration.py` 沙箱 socket bind 限制，停用沙箱重跑 26/26 passed，非 regression）；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0/0。承接上一輪 evaluator suggestion，補上 `push_daily_summary()` 兩條 skip log 的 `summary_date` 上下文，diff 僅 +3/-3 行，surgical。

## 2026-07-25 — market-summary-logging — evaluator — ✅ Pass
- 連結：docs/qa-reports/market-summary-logging.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 235/235 passed（沙箱環境 socket bind 限制與本次改動無關，停用沙箱後全綠）；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0/0。`market_summary.py` 統一改用 `aws_lambda_powertools.Logger`（與 `scheduler_handler.py` 一致），補上 `refresh_news`/`push_daily_summary`/`sync_earnings` 關鍵節點的 structured log，修好 Lambda 環境下 log 靜默的 observability 缺口；已用暫時破壞 log 訊息文字的方式驗證新測試 `test_push_daily_summary_logs_pushed_count` 會真的 fail。

## 2026-07-25 — prompts-module — evaluator — ✅ Pass
- 連結：docs/qa-reports/prompts-module.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 274 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，非回歸）、非沙盒重跑 286 passed, 0 failed（與 generator 自報一致）；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。純重構驗收：獨立用 `git show HEAD:<原檔>` 抽出 `SYSTEM_INSTRUCTION`/`SUMMARY_SYSTEM_PROMPT`/`COMPANY_PROMPT_TEMPLATE` 三個原始字串（不信任 generator 自查），與新 `src/agent/prompts.py` 現值逐一 `==` 比對皆 True；三檔 diff 純「刪定義 + 加 import」，import 排序、blank line 間距均正確；`prompts.py` 除 `from __future__ import annotations` 外零依賴；排除範圍確認守住——`processor_handler.py` diff 為空、`agent.py:77` user_content f-string 組裝邏輯未動；`test_agent.py` 既有斷言透過 re-export 沿用無需修改。

## 2026-07-25 — knowledge-base — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/knowledge-base.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 251 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，非回歸）、非沙盒重跑 263 passed, 0 failed；🔴 0、🟡 1（BM25 對「小語料+heading 常見疑問句式 bigram」誤中無關查詢的通用弱點，contract 指定的「今天天氣如何」測試字面通過但為 ADR 已接受 trade-off 的具體表現，建議補記 ADR Consequences，非阻塞）、🟢 3（`_truncate` 缺單元測試、`top_k` schema 缺 min/max、單一自訂 query 排序非 top-1）；循環次數 1；未解 P0/P1 0。Contract 14 項 deliverable 全數核對通過，種子檔指數↔ETF 對照（DJIA→DIA/SPY/QQQ/IWM）正確，無循環引入（`from agent.tools import TOOL_FUNCTIONS` + `import agent.agent` 實測 6 個工具），`SYSTEM_INSTRUCTION`/`tools/__init__.py` 均為 surgical append。

## 2026-07-25 — knowledge-base — planner — spec + contract + ADR 完成
- 連結：docs/specs/knowledge-base.md, docs/contracts/knowledge-base.md, docs/decisions/0008-knowledge-base-retrieval-strategy.md
- 備註：新增本地 Markdown 知識庫 `knowledge/`（2-3 種子檔：指數代號對照 / 財報術語 / 分析框架）+ 第六個工具 `search_knowledge_base`，目標「回答可附來源、可驗證」。關鍵決策寫進 ADR-0008：斷詞用輕量規則（lowercase；英數 word；CJK 相鄰字元 bigram，len==1 退 unigram），計分自含 BM25（k1=1.5/b=0.75、heading tokens 2x 加權、分數<=0 濾掉使無命中回空），**不用 embeddings、不加第三方套件、索引不落地**（process 內 lazy build + @lru_cache）。工具 `search_knowledge_base(query, top_k=3)` 回傳 `{"results":[{source,heading,content}]}`、top_k clamp 5、單 section content 1500 字元安全截斷。知識庫模組只依賴 stdlib、不 import agent 層（避開 market_summary_tool 那類循環引入）。SYSTEM_INSTRUCTION surgical append：何時查庫、指數代號別用 get_quote 亂猜（DJIA→DIA/SPY/QQQ，對應實際發生過的 bug）、引用附來源、查無明說不編造。**部署（md 進 Lambda image）明確 out of scope**。驗收情境：中文問「道瓊怎麼查」top-1 命中指數對照 section、內文含 DIA、附來源 `檔名#標題`。無阻擋性 open question。備註：本環境 rg 不可用故未實際列讀參考專案 `knowledge_base_qa_bot/scaffold/markdown_kb/`，BM25 section 索引模式依需求描述自含定義，generator 實作時可再參考該專案但不依賴之。

## 2026-07-25 — news-quality — evaluator — ✅ Pass
- 連結：docs/qa-reports/news-quality.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 244 passed/12 errors（既有 socket bind 沙盒限制，非回歸）、非沙盒重跑 256 passed, 0 failed；🔴 0、🟡 0；循環次數 2；未解 P0/P1 0。round 1 的 2🔴（`subscribe` 缺 word boundary 命中 subscriber、獨立 `top gainer/loser` 單數命中真實個股事件標題）與 2🟡（`stock to watch` 單數、`most active...stocks` 跨標點容錯）已由 generator 回修：`subscribe`/其餘關鍵字全補 `\b`、獨立 top gainer/loser 規則移除只留成對 `gainers and/& losers`、`most active...stocks` 規則整條移除、`stocks to watch` 強制複數。5 個反例逐一實測確認不再誤殺，且垃圾偵測能力（含 "most active...— join us" 混合案例）未退化；5 個反例已併入正式回歸測試。

## 2026-07-25 — news-quality — evaluator — ❌ Fail
- 連結：docs/qa-reports/news-quality.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 240 passed/12 errors（既有 socket bind 沙盒限制，非回歸）、非沙盒重跑 252 passed, 0 failed；🔴 2、🟡 2；循環次數 1；未解 P0 2、P1 2。`JUNK_HEADLINE_PATTERN` 的 `subscribe`（無 word boundary，命中 subscriber/subscribers）與獨立的 `top\s+(?:gainers?|losers?)`（單數形式命中真實個股事件標題如 "Tesla was the top gainer...after earnings beat"）皆會過殺真新聞，違反「寧可漏殺不過殺」要求，需回修；`stocks? to watch` 與 `most active...stocks` 風險較低列 Warning。SYSTEM_INSTRUCTION 補句、市場摘要 refresh_news 套用過濾、import 方向皆無問題。

## 2026-07-25 — market-summary-tool — evaluator — ✅ Pass
- 連結：docs/qa-reports/market-summary-tool.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 222 passed/12 errors（既有 socket bind 沙盒限制，非回歸）、非沙盒重跑 234 passed, 0 failed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。第五個工具 `get_market_summary` 現場呼叫 `generate_market_summary(days_back)`、回傳僅含 `summary` 不含 news/indexes、`days_back` 1-7 越界由既有 `market_summary._date_from()` clamp 防呆、`tools/__init__.py` 掛載、`SYSTEM_INSTRUCTION` 補工具選擇句。延遲 import 解循環（tools → market_summary_tool → agent.market_summary → agent.agent → agent.tools）已用 `python -c "from agent.tools import TOOL_FUNCTIONS"` 實測驗證無誤。

## 2026-07-25 — chat-cli — evaluator — ✅ Pass
- 連結：docs/qa-reports/chat-cli.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 219 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，非回歸）、非沙盒重跑 231 passed, 0 failed；`tests/test_chat_cli.py` 7/7 passed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。

## 2026-07-24 — ecr-lifecycle-policy — evaluator — ✅ Pass
- 連結：docs/qa-reports/ecr-lifecycle-policy.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 212 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，非回歸）；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。三個 companion-stack ECR repo（`processorfunction78019991repo`/`schedulerfunction07e3fe86repo`/`webhookfunction2cdba7a2repo`）已實際用 `aws ecr get-lifecycle-policy` 逐一驗證，皆回傳 keep-last-3（`imageCountMoreThan 3`/`tagStatus any`/`expire`）policy；並用 `aws lambda get-function` + `aws ecr describe-images` 交叉確認現役 Lambda image tag 是 push 時間最新，落在保留範圍內不會被清除。改動範圍僅 `Makefile`/`docs/runbook.md`/新增 `infra/ecr-lifecycle-policy.json`，未觸碰 `template.yaml`/`samconfig.toml`。

## 2026-07-24 — observability-golive — evaluator — ✅ Pass
- 連結：docs/qa-reports/observability-golive-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 212 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、依指示非沙盒重跑 224 passed, 0 failed（12 條 socket-bind 測試含改動過的 `test_processor_subscribe_and_scheduler_share_local_store` 全綠）；`cd infra && sam validate --lint` 通過；🔴 0、🟡 0（🟢 1：`summary_notified`/`mark_summary_notified` 複用 `earnings_notifications` 表 + sentinel key 略微 overload 表名語意，ADR-0007 已載明此 trade-off，不需現在改）；循環次數 1；未解 P0/P1 0。Contract 逐項核對全數 ✅：handler 依 `mode` 分流 `sync`/`notify`，缺/未知 mode 在跑任何階段前 fail-fast raise；`_run_sync`/`_run_notify` 各兩階段獨立容錯 + 完成後 re-raise；`push_daily_summary(refresh=False)` 跳過抓取；摘要冪等 guard（`summary_notified`/`mark_summary_notified`）複用既有表、無新表/IAM，且有專屬測試證明與 earnings `mark_notified` 同表不互相誤判；`infra/template.yaml` 舊 `Daily`/`DISABLED`/UTC 完全清除，`SyncSchedule`(08:00)/`NotifySchedule`(10:00) 的 cron/時區/Input/RetryPolicy/State 逐字對照 contract；SNS Topic + email subscription + 三個 CloudWatch Alarm 齊備、`AlarmEmail` Parameter 無 Default、`samconfig.toml` 僅加註解未填真實信箱；stale banner 拆分後仍生效有專屬回歸測試（走 `handler({"mode":"notify"})` 全路徑）；本地 dev 路徑（`__main__` CLI、`make daily-summary`/`sync-earnings`/`notify-earnings`）逐行核對零改動；`test_local_integration.py` 的 `{"mode":"notify"}` 修正未弱化原測試意圖。diff 手術式，12 個檔案每行皆對應 contract 條目。PR body 需依 contract DoD 明列 cutover 順序（先停本地 launchd 再部署 ENABLE），屬 Phase 3 ops、非本次驗收阻擋項。

## 2026-07-24 — observability-golive — planner — spec + contract + ADR 完成（PR-B）
- 連結：docs/specs/observability-golive.md, docs/contracts/observability-golive-sprint.md, docs/decisions/0007-schedule-split-observability-golive.md；roadmap 更新於 docs/TASKS.md（Roadmap v5 → PR-B + cutover 順序）
- 備註：go-live PR。使用者 2026-07-24 定案寫進 ADR-0007（部分取代 ADR-0006 §5/§7）：(1) 排程拆兩個同一 SchedulerFunction、Input payload 分流——08:00 台北 sync（sync_earnings + refresh_news，Finnhub 寫 DynamoDB）/ 10:00 notify（_notify_local + push_daily_summary(refresh=False)，讀庫→Groq→Telegram）；核對 market_summary.py 確認 refresh_news 在 push_daily_summary 內，故 notify 加 `refresh=False` 參數跳過抓取避免雙抓 Finnhub。(2) 缺/未知 mode → handler fail-fast raise（計入 Errors → alarm，勝過靜默於錯誤時點跑全套）。(3) RetryPolicy 2 次 / 3600s（保證 sync 重試在 notify 前收斂、notify 重試不跨日）。(4) 補大盤摘要 per-(subscriber,date) 冪等 guard（`summary_notified`/`mark_summary_notified`，複用 earnings_notifications 表 + sentinel key，不新增表/IAM），使重試不重複推播摘要（earnings 已冪等）。(5) SNS Topic + email subscription（AlarmEmail SAM Parameter，不 hardcode）+ 三個 CloudWatch Alarm（Scheduler Errors / Processor Errors / Scheduler Duration≥250s）。(6) 兩排程 State: ENABLED、Asia/Taipei。確認 SAM ScheduleV2 支援 Input/RetryPolicy/State/tz 且同 function 可掛多 events。stale 標註沿用既有 _is_stale/_stale_banner（僅需確認 notify path 仍生效 + 測試）。邊界：deploy / 部署後驗證 / email 確認 / 停 launchd 皆 Phase 3 ops；schedule DLQ、Telegram 失敗 log alarm、摘要冪等專用表已評估未採。cutover 順序（寫進 TASKS.md + 需入 PR body）：先停本地 launchd → 再部署含 ENABLE 的 template（避免雙推）。IaC 靜態驗證：要求 tests 用 CFN intrinsic-aware YAML loader 斷言 template 結構 + 有 sam CLI 時 sam validate --lint。無阻擋性 open question。

## 2026-07-24 — cloud-daily-pipeline — evaluator — ✅ Pass
- 連結：docs/qa-reports/cloud-daily-pipeline-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 191 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 203 passed, 0 failed（與 generator 自報一致）；🔴 0、🟡 0（🟢 2：容錯測試用 `pytest.raises(Exception)` 略寬鬆；`insert_market_summary` Protocol `-> None` 與 local 既有 `-> int` 型別提示落差，皆為既有狀態或不影響驗收，不需改）；循環次數 1；未解 P0/P1 0。Contract 逐項核對全數 ✅：`insert_news`/`list_news_since`/`insert_market_summary` 三方法欄位形狀、去重語意、GSI 查詢、分頁與 no-op 皆對齊 spec 與 `upload_local_store._news_item`；三方法升格進 Protocol 且 docstring 精確更新；`_notify_live`/`PROMPT_TEMPLATE`/`selected_backend_name` 分岔死碼移除乾淨、無 orphan import、`DEFAULT_WATCHLIST`（seed 用）正確保留；`handler()` 單次跑 sync→notify→summary，階段容錯 + 最終 raise 有專屬測試覆蓋（非僅 happy path）；`get_watchlist(chat_id)` 改必填、Protocol 同步、呼叫端本就一律帶 `chat_id` 無破壞性。特別查核點全數確認：`infra/template.yaml` 的 `SchedulerFunction` `Daily` event 逐字仍 `State: DISABLED`（僅新增 `NewsTable` IAM/env 兩處）、無 SNS/alarm、未 deploy；`market_summary.py` 零改動（比 contract 允許的 1 行例外更保守）；`push_daily_summary` 在 dynamodb backend 端到端測試證明 `refresh_news→insert_news→list_news_since→select_news→generate_market_summary→push` 整條鏈路真正跑通，`id==headline` 斷言直接對應關鍵設計決策。無 production 行為變更，可獨立 deploy。

## 2026-07-24 — cloud-daily-pipeline — planner — spec + contract + ADR 完成（PR-A）
- 連結：docs/specs/cloud-daily-pipeline.md, docs/contracts/cloud-daily-pipeline-sprint.md, docs/decisions/0006-daily-pipeline-to-cloud.md；roadmap 重排於 docs/TASKS.md（Roadmap v5）
- 備註：方向修正——daily pipeline 由 hybrid local-first（ADR-0005）搬上雲端 EventBridge（走「B」，ADR-0006）。原 PR-2/PR-3 重排為 PR-A（雲端 pipeline 完整化）＋ PR-B（observability + schedule ENABLE）＋ Phase 3 ops cutover。**本次只出 PR-A 的 spec/contract**。快取策略自行定案（無阻擋問題）：earnings_calendar/company_summaries/earnings_notifications 維持 DynamoDB（PR-1 已實作）；news 落 DynamoDB（`insert_news`/`list_news_since`，NewsTable 已存在，`list_news_since` 回列 `id=headline` 使 market_summary.py 免改）；market_summaries **雲端不持久化**（純稽核、production 不讀回、news_id 稽核鏈無法對映 DynamoDB → `insert_market_summary` no-op），三方法升格進 Protocol。scheduler_handler 移除 `_notify_live` + `get_watchlist(None)` DEFAULT_WATCHLIST 死碼、`_run()` backend 分岔；`handler()` 單次跑 sync→notify→summary，階段容錯 + 最後 re-raise 使失敗計入 Lambda Errors。**PR-A 刻意維持 schedule DISABLED（無 production 行為變更、可獨立 deploy）**；SNS/alarm 與 schedule ENABLE + 時區（建議 Asia/Taipei 10:30，沿用現行本地）留 PR-B。排序關鍵：observability 與 schedule ENABLE 綁 PR-B（第一次雲端 run 起即可觀測失敗，硬需求）；cutover 雙推風險（冪等 store 分屬兩 backend）由 Phase 3 ops「先停本地 launchd 再部署 ENABLE」規避。邊界：deploy / 部署後驗證 / 停 launchd 皆屬 Phase 3（infra-engineer / 使用者），不在 generator/evaluator 範圍。無阻擋性 open question。

## 2026-07-24 — dynamodb-downsync — evaluator — ✅ Pass
- 連結：docs/qa-reports/dynamodb-downsync-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 188 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 200 passed, 0 failed（191 既有 + 9 新增於 `test_download_remote_store.py`）；🔴 0、🟡 0（🟢 1：`_overwrite_local_store` 每次呼叫都新建 `LocalSQLiteBackend` 實例觸發 schema 檢查，CLI 單次執行下無害，不需改）；循環次數 1；未解 P0/P1 0。Contract 逐項核對全數 ✅：下行模組完整覆蓋語意（scan 全成功才單一 sqlite transaction DELETE+INSERT）、pagination（`_scan_all` 正確跟進 LastEvaluatedKey）、型別轉換（active Bool→INTEGER、position→INTEGER、subscribed_at 缺漏回填 now）、失敗語意分兩層（scan 例外寫入前中止 / transaction 例外靠 `with conn:` 自動 rollback，皆有測試覆蓋不留半空表）、下行不越界（不 import 任何非 watchlist/subscribers table accessor，靜態 + 測試雙重確認）；上行只移除 conversations/subscribers/dedup/watchlist 四段迴圈與對應 orphan import，diff 精準手術式；上行回歸測試已用 `git stash` 實測驗證對舊 code 確實 fail（stash 後 subscribers/watchlist/conversations/dedup 計數非 0）；`Makefile`/`scripts/run-downsync.sh`/`scripts/com.earnings-agent.downsync.plist` 與既有 `sync-dynamodb`/`run-daily.sh`/daily plist 逐行逐 key 風格一致，`plutil -lint` 通過，Hour/Minute 正確設為 0/0（台北午夜）；`docs/local-daily-schedule.md` 完整補上 00:00 小節並更正 10:30 上行的過時描述。

## 2026-07-24 — dynamodb-downsync — planner — spec + contract + ADR 完成
- 連結：docs/specs/dynamodb-downsync.md, docs/contracts/dynamodb-downsync-sprint.md, docs/decisions/0005-dynamodb-source-of-truth-downsync.md
- 備註：確立 DynamoDB 為 Telegram 輸入（watchlist / subscribers）的 source of truth，方向反轉——新增每日 00:00（台北）下行同步（DynamoDB → 本地 sqlite）完整覆蓋 watchlist + subscribers，並移除 10:30 上行對 watchlist / subscribers / conversations / dedup 的上傳（修掉「stale 本地 watchlist 在 10:30 把使用者已 /unwatch 的股票回寫雲端」的 bug）。ADR-0005 取代 ADR-0004 §9 關於 watchlist/subscribers 上行的部分。關鍵決策：下行只搬 watchlist+subscribers（conversations 本地每日任務不讀、不下行）；上行只搬本地產生的快取 earnings_calendar/company_summaries/news；完整覆蓋語意=先全量 scan（處理 pagination）成功後、單一 sqlite transaction 內 DELETE 全表再 INSERT，寫入中途失敗 rollback 不留半空表；空狀態採嚴格鏡射（雲端刪光本地也刪，不加保護網）；seed_watchlist_if_empty 不改（唯一有效 seed 路徑在雲端 /subscribe，本地每日 pipeline 不 seed，下行整表覆蓋消除分岔）；新增獨立 00:00 launchd plist + run-downsync.sh + make sync-from-dynamodb。Contract 含關鍵回歸測試：上行不再回寫 watchlist/subscribers/conversations/dedup（改動前應失敗）、下行刪除傳播 / atomicity 不留半空表 / pagination / dry-run / 冪等。不改 infra、不 deploy（所需 DynamoDB 表已存在）。無阻擋性 open question。

## 2026-07-24 — dockerfile-groq-dep — evaluator — ✅ Pass
- 連結：docs/qa-reports/dockerfile-groq-dep.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 179 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 191 passed, 0 failed（含新增 `tests/test_dockerfile_deps.py`）；🔴 0、🟡 0（🟢 1：Dockerfile pip install 區塊解析 regex 依賴後方空行，未來格式微調有極小機率誤判但會安全 assert 失敗而非靜默通過）；循環次數 1；未解 P0/P1 0。無正式 contract，對照需求驗收：`Dockerfile:7` `google-genai>=1.0.0` → `groq>=0.11.0` 修正雲端 ProcessorFunction/SchedulerFunction 的 `Runtime.ImportModuleError: No module named 'groq'`（已用 `git stash` 重現修復前 test fail、獨立 `docker build` + `docker run --entrypoint python` 驗證 `agent.agent`/`agent.processor_handler`/`agent.scheduler_handler` 皆可 import，image 已清除）；Makefile `ssm-put`/`set-webhook`/`set-webhook-local`/`delete-webhook` 共 7 處金鑰相關 recipe 行補 `@`（全檔掃描確認無遺漏其他 target），實跑 `make delete-webhook TELEGRAM_BOT_TOKEN=FAKE` 確認無明文洩漏；diff 完全手術式，僅 Dockerfile 一行 + Makefile 7 處 `@` + 新測試檔。

## 2026-07-24 — dev-env-file — evaluator — ✅ Pass
- 連結：docs/qa-reports/dev-env-file.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 148 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 160 passed, 0 failed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。無正式 contract，對照需求驗收：`make dev` 加 `--env-file .env`（Makefile:20）+ `python-dotenv` 補進 dev deps（確認修復前 lockfile 確實缺此依賴）+ 新增 `tests/test_dev_env_file.py` 先重現 `KeyError` bug 再驗證 `uvicorn.Config(env_file=...)` 修法有效（已讀 uvicorn 原始碼確認此路徑與 CLI `--env-file` 等價）；diff 完全手術式，僅 Makefile/pyproject.toml/uv.lock/新測試檔；python-dotenv 正確限定在 dev group，不影響走 SSM 的 production Lambda。

## 2026-07-24 — check-bot — evaluator — ✅ Pass
- 連結：docs/qa-reports/check-bot.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 176 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 188 passed, 0 failed（30 新測試皆針對純函式，不碰網路）；🔴 0、🟡 0（🟢 1：`probe_groq` 例外訊息截斷後印出，目前實測不含金鑰值但長期風險留意）；循環次數 1；未解 P0/P1 0。無正式 contract，對照使用者需求驗收：五項健檢（本地 server／ngrok tunnel／Telegram webhook／金鑰／Groq）皆逐項對應 PASS/FAIL/WARN 分類規則；純判斷邏輯（`classify_server_probe`／`find_ngrok_public_url`／`webhook_url_matches`／`is_placeholder`／`mask_key_status`／`webhook_info_warnings`）與 I/O 探測分離；所有網路呼叫皆設 10s timeout；`run_checks()` 雙層 try/except 確保單項失敗不中斷後續；`scripts/check-bot.sh` 無 `set -e`；exit code 規則（有 FAIL 非 0、全 PASS/WARN 為 0）已用真實 `.env` 實跑 `make check-bot` 驗證（0 FAIL、1 WARN、exit 0）；金鑰安全審查逐路徑確認 `mask_key_status`／`check_secrets`／`probe_telegram_webhook`（webhook URL 不含 bot token）／`probe_groq`（實測假金鑰觸發 401，錯誤訊息不含金鑰值）皆未洩漏原值；唯讀確認無任何 DB 寫入或 Telegram 發訊呼叫；smoke-test 用 `.env` 已核實透過 `git check-ignore` 命中 `.gitignore` 規則、未進版控。

## 2026-07-24 — sync-local-to-dynamodb — evaluator — ✅ Pass
- 連結：docs/qa-reports/sync-local-to-dynamodb.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 146 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 158 passed, 0 failed；🔴 0、🟡 1（ADR-0004 §9 記載 upload_local_store 不搬 earnings_calendar/company_summaries，本次依使用者新指示推翻，ADR 文件未同步更新註記，屬文件與現況不一致但不阻塞）；🟢 2；循環次數 1；未解 P0/P1 0。無正式 contract，對照使用者需求驗收：`NewsTable`（PK=headline 去重、GSI published-at-index 支援近期查詢、TTL）+ SchedulerFunction 補 `State: DISABLED`（未動 IAM）+ `upload_local_store.py` 擴充 earnings_calendar/company_summaries/news（Decimal 轉換與既有 DynamoDBBackend 手法一致、TTL 對齊 backend.py 常數、nullable 欄位省略寫入、冪等由 put_item overwrite-by-key 保證且有專屬測試驗證）+ `sync-dynamodb` make target 串接 run-daily.sh 第四步（風格與既有三步一致）+ moto 測試新增 test-news 表與冪等測試；排除項（market_summaries 不同步、DynamoDBBackend 不新增 news 讀取方法、不執行 make deploy）皆核實遵守；範圍手術式，僅 9 個檔案改動且逐一對應需求。

## 2026-07-24 — dynamodb-backend-pr1-sprint（follow-up：補日期區間邊界測試）— evaluator — ✅ Pass
- 連結：docs/qa-reports/dynamodb-backend-pr1-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 145 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 157 passed, 0 failed；只補 `tests/test_dynamodb_backend.py` 新增 `test_dynamodb_watchlist_earnings_date_range_boundaries`，`src/` 零改動；🔴 0、🟡 0（解掉前次 🟡：`get_chat_watchlist_earnings` 缺日期區間過濾邊界測試）；循環次數 1；未解 P0/P1 0。

## 2026-07-24 — dynamodb-backend-pr1-sprint — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/dynamodb-backend-pr1-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 144 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 156 passed, 0 failed；🔴 0、🟡 1（`get_chat_watchlist_earnings` 缺日期區間過濾邊界測試，contract Tests required 明確列出但未覆蓋）；循環次數 1；未解 P0/P1 0。

## 2026-07-24 — dynamodb-backend-pr1（Phase 2 PR-1）— planner — spec + contract + ADR 完成
- 連結：docs/specs/dynamodb-production-tables.md, docs/contracts/dynamodb-backend-pr1-sprint.md, docs/decisions/0004-dynamodb-production-tables.md
- 備註：細化 Phase 2 PR-1（DynamoDB 四張新表 + DynamoDBBackend 補實作 + infra/IAM + 測試）。關鍵決策：多表風格 + application-side join；watchlist symbol GSI 省略；通知冪等改 conditional put（attribute_not_exists，同 claim_update_id）；TTL earnings_calendar 45 天 / notifications 30 天 / company_summaries 30 天 / watchlist 無 TTL；float↔Decimal 轉換保 parity；7 個原 local-only 方法升格進 StorageBackend Protocol（延伸 ADR-0003）；`get_watchlist(None)` 保留 DEFAULT_WATCHLIST 過渡分支（PR-2 移除）；upload_local_store 只搬 watchlist、不搬三個快取表。明確不改 scheduler_handler / processor_handler（PR-2）、不含 news/market_summaries（PR-3）。開放邊界：cutover 當天可能一次重複通知，以切換時機規避。

## 2026-07-24 — remove-watchlist-exchange — evaluator — ✅ Pass
- 連結：docs/qa-reports/remove-watchlist-exchange.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 131 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 143 passed, 0 failed；🔴 0、🟡 0（🟢 2：DROP COLUMN migration 依賴 SQLite ≥3.35、歷史 docs 仍留 exchange 字樣但使用者已明確排除範圍）；循環次數 1；未解 P0/P1 0。驗收範圍：`db/watchlist.py`（DEFAULT_WATCHLIST 28 筆只留 symbol）、`db/backend.py`（Protocol/Local/DynamoDB 三處 `add_to_watchlist` 拿掉 exchange 參數、watchlist CREATE TABLE 移除 exchange 欄、新增 `PRAGMA table_info` 偵測 + `ALTER TABLE DROP COLUMN` migration、`_migrate_watchlist_schema` 與六個查詢方法全數移除 exchange）、`scheduler_handler.py`（兩個 prompt template、`_company_summary`、`_notify_live` 移除 exchange）、`processor_handler.py`（`/watchlist` 只顯示 symbol）；grep 覆核 `exchange`/`NASDAQ`/`NYSE` 僅剩 migration 程式碼本身與模擬舊 schema 的測試 fixture，均合理保留非殘留；新增 `test_migration_drops_exchange_column_from_existing_per_subscriber_db` 用 `PRAGMA table_info` 斷言精準覆蓋 DROP COLUMN migration 行為；改動手術式，working tree 內其餘既有未提交檔案（Makefile/local_server.py/market_summary.py 等）核對均屬其他 phase 既有工作、本次未觸碰。

## 2026-07-24 — fix-launchd-env-loading — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-launchd-env-loading.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 130 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 142 passed, 0 failed；🔴 0、🟡 0（🟢 3：`set -e` 力度與腳本複雜度相稱、錯誤訊息路徑為字面字串已在已知限制揭露、plist 路徑寫死屬既有 Phase 1 設計不擴大）；循環次數 1；未解 P0/P1 0。驗收範圍：新增 `scripts/run-daily.sh`（cd → `.env` 存在檢查 exit 1 → `set -a; source .env; set +a` → 三個 make target 用 `&&` 串接）修復 launchd 不 source shell profile 導致 `.env` 變數未載入、`config.py` KeyError 的 bug；`bash -n`、`plutil -lint`、`env -i` 模擬 launchd 最小環境（複製腳本、末行改 `make -n` + echo 三把 key，不打真實 API）驗證三把 key 皆載入且三步 make 組成正確、exit 0；另模擬 `.env` 缺失情境驗證 exit 1、不執行任何 make target；plist `ProgramArguments` 改為呼叫該 script，其餘欄位（WorkingDirectory/PATH/log 路徑/StartCalendarInterval/RunAtLoad）逐項比對前次 QA report 記載值皆未變動；`docs/local-daily-schedule.md` 已安裝者 unload/load 提醒位置醒目、指令與既有安裝步驟一致；改動範圍手術式，working tree 內其餘未提交檔案（Makefile/market_summary.py 等）核對均屬其他 phase 既有工作、本次未觸碰。

## 2026-07-23 — fix-local-webhook-sqs-500 — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-local-webhook-sqs-500.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 130 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 142 passed, 0 failed；🔴 0、🟡 1（`_no_op_sqs()` 併發安全性依賴「窗口內無 await + 單一 uvicorn worker」隱含前提，未在程式碼中記錄，建議補註解，非阻塞）；循環次數 1；未解 P0/P1 0。驗收範圍：`local_server.py` 新增 `_no_op_sqs()` context manager，在呼叫 `webhook_handler.handler()` 期間把 `webhook_handler_module._sqs` 換成 no-op 假物件，修復本地 `/webhook` 首次投遞因無真實 SQS queue 而 `QueueDoesNotExist` → 500 的 bug；用 `git stash` 對修復本身做雙向 mutation test（拿掉修復 → 復現 500；還原修復 → 恢復 200）證實新增回歸測試確實鎖住此 bug；新測試刻意不借用會建立真實 queue 的 `aws` fixture，準確覆蓋既有 `test_rest_webhook_accepts_valid_secret` 未能觸及的「本地無 queue」情境；`webhook_handler.py` 零改動，Lambda 生產路徑（含真實 enqueue）不受影響。

## 2026-07-23 — fix-local-webhook-500 — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-local-webhook-500.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 129 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 141 passed, 0 failed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。驗收範圍：`src/agent/local_server.py` 新增 `_LOCAL_LAMBDA_CONTEXT`（`SimpleNamespace`，逐一比對 `aws_lambda_powertools` 原始碼確認四屬性無多無缺）修復本地 `/webhook` 500；`git stash` 實測復現修復前 `AttributeError: 'NoneType' object has no attribute 'function_name'` 與 root cause 吻合；新增 401/200 兩測試，200 案例以 `fake_send.assert_called_once()` 排除「`_process_update` 例外被吞」假陽性風險；`webhook_handler.py` 零改動，生產路徑不受影響。

## 2026-07-23 — phase1-local-daily-schedule — evaluator — ✅ Pass
- 連結：docs/qa-reports/phase1-local-daily-schedule.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 127 passed/12 errors（既有 socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 139 passed, 0 failed；🔴 0、🟡 0（🟢 3：`&&` 串接失敗會擋住後兩步——TASKS.md Phase 1 已定案「失敗留 log、不做告警」，非 Critical；`generate_market_summary` 既有行為固定打大盤報價非本次新增；plist 路徑寫死本機屬 Phase 1 階段性可接受）；循環次數 1；未解 P0/P1 0。驗收範圍：`push_daily_summary()`（`src/agent/market_summary.py`，訂閱者來源/錯誤處理比照 `scheduler_handler._notify_live` 既有模式）+ `Makefile daily-summary`（`.PHONY` 已補）+ `scripts/com.earnings-agent.daily.plist`（`plutil -lint` 通過、台北 08:00、WorkingDirectory/PATH/log 皆正確）+ `docs/local-daily-schedule.md`（load/unload/改時間/看 log/手動觸發，檔名與 plist label 逐字一致）；新增 4 個 `push_daily_summary` 測試案例（多訂閱者推播、無內容不發、單一失敗不中斷、無訂閱者不發），沿用既有 fixtures，無 flaky 風險。

## 2026-07-23 — phase0-webhook-switch — evaluator — ✅ Pass
- 連結：docs/qa-reports/phase0-webhook-switch.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 123 passed/12 errors（既有 socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 135 passed, 0 failed；🔴 0、🟡 0（🟢 2：不對稱行為說明、文件風格一致性，皆不要求修改）；循環次數 1；未解 P0/P1 0。驗收範圍：Makefile 新增 `set-webhook-local`/`delete-webhook`（`.PHONY` 更新、缺參數報錯風格與既有 `set-webhook` 一致、`set-webhook` 未被改動、URL 拼接 `<WEBHOOK_URL>/webhook`、`secret_token` 帶出，dry-run + 缺參數情境皆實測驗證）、`docs/deployment-flow.md` 新增本地開發 webhook 切換小節（步驟與 Makefile 指令逐字一致，另查核「本地 webhook_handler 會驗證 secret」之描述屬實）。
## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— evaluator — ✅ Pass
- 連結：docs/qa-reports/manual-test-watchlist-notify.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 基準 124 → 最終 129 passed（sandbox 擋既有 `finnhub_stub` 12 個 socket 測試，已用 `dangerouslyDisableSandbox` 重跑）；新增 `tests/test_watchlist_notify_flow.py` 5 案例對應 spec 全部 7 條 acceptance criteria，全鏈路驅動 `_process_update → sync_earnings → handler(_notify_local)`，僅打樁 Finnhub/Groq/Telegram 三個外部邊界；冪等斷言已用 mutation test（拿掉 `mark_notified`）驗證有效；🔴 0、🟡 0（🟢 2：一處恆真斷言、建議合併 T1/T2 分支避免基準漂移）；循環次數 1；未解 P0/P1 0；本任務為 agent 自動化整合驗證，非人工手動互動；`src/` 無改動，未發現既有真 bug

## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— planner — spec + contract 完成
- 連結：docs/specs/watchlist-notify-flow-verification.md, docs/contracts/watchlist-notify-flow-verification.md
- 驗證：Unit not run（planner 階段，且此環境無 Bash 無法親跑 pytest）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：把 T3 流程 A 由「使用者手動互動」改造成**全自動化 e2e 整合測試**（使用者明確偏好）。產出單一新測試檔 `tests/test_watchlist_notify_flow.py` 串鏈路 `/subscribe`→`/watch`→`sync_earnings()`→notify，覆蓋 subscribe 寫入/watch 加入/sync 寫 earnings_calendar/notify 對正確 subscriber 發正確 symbols/冪等/非訂閱者或無資料不誤發。驅動方式決策：步驟1-2 直呼 `processor_handler._process_update`（真實指令路由，不用 POST /webhook 因其只 enqueue SQS）、步驟3 直呼 `sync_earnings()`、步驟4-5 直呼 `scheduler_handler.handler`（local 走 `_notify_local`）。Mock 邊界決策：Finnhub 改用 **socketless** 假 client（monkeypatch `agent.tools._http._client`）而非既有 `finnhub_stub` 真 HTTP server，避開 sandbox 對 socket bind 的限制、且不動 test_local_integration.py 既有 fixture；Groq monkeypatch `scheduler_handler.run_agent`；Telegram monkeypatch `send_message`。無 ADR（純測試建置，無不可逆架構決策）。未發現既有真 bug；若 generator 揭露則比照 insert_news 先於 QA report 標明再最小修正。基準：planner 無 Bash 無法親跑，CHANGELOG 上次記錄 124/124（早於 T1 新增測試），generator/evaluator 須先跑 `PYTHONPATH=src uv run pytest tests/ -q` 確立確切基準 N。QA report 指定檔名 `docs/qa-reports/manual-test-watchlist-notify.md`，內容須標明為自動化驗證。

## 2026-07-23 — manual-test-market-summary（T3 流程 B） — evaluator — ✅ Pass
- 連結：docs/qa-reports/manual-test-market-summary.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 130/130 passed（sandbox 對本機 socket bind 有限制，`test_local_integration.py` 12 條以非 sandbox 模式重跑確認）；🔴 0、🟡 1（已修復）；循環次數 1；未解 P0/P1 0。手測涵蓋 `/market-summary`、`/news`，DB 權威查詢確認 `news`/`market_summaries` 有資料、摘要繁中且 `news_ids` 可稽核、dedup（`count(*)=count(DISTINCT headline)=54`）正確。測試中揪出 `insert_news()` 用不可靠的 `cur.rowcount` 累加 `INSERT OR IGNORE`、回傳「嘗試插入數」而非「實際新增數」的 bug，已改用 `conn.total_changes` 差值修復（`src/agent/db/backend.py`）並補 2 條迴歸測試（`tests/test_storage_backend.py`）。範圍僅涵蓋 T3 流程 B；流程 A（Telegram watchlist 財報通知）尚未測試，待補 `docs/qa-reports/manual-test-watchlist-notify.md`。

## 2026-07-22 — storage-backend-strategy-sprint — evaluator — ✅ Pass
- 連結：docs/qa-reports/storage-backend-strategy-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 124/124 passed（sandbox 對 ~/.cache/uv 有寫入限制，已用非 sandbox 模式重跑）；🔴 0、🟡 0（🟢 1，Protocol `get_watchlist` 預設值與既有 `LocalSQLiteBackend` 簽名不完全對齊，非本次引入、不影響 runtime）；循環次數 1；未解 P0/P1 0

## 2026-07-22 — storage-backend-strategy-sprint — planner — spec + contract + ADR 完成
- 連結：docs/specs/storage-backend-strategy.md, docs/contracts/storage-backend-strategy-sprint.md, docs/decisions/0003-storage-backend-strategy.md
- 驗證：Unit not run（planner 階段）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：內部重構——引入 `StorageBackend`（`typing.Protocol`）+ `DynamoDBBackend` class，`get_backend()` 依 backend 名回傳對應實例（維持 `@lru_cache` singleton），收斂 11 處 `if selected_backend_name()=="local"` wrapper 分支（conversations×2/subscribers×3/dedup×1/watchlist×4/earnings×1）成單一 `get_backend().X(...)`。Scope 取捨：Protocol 只涵蓋跨 backend 共用的 11 方法，local-only 方法（news/summaries/notifications/earnings join/export）不進 Protocol、不在 DynamoDBBackend 補 stub（見 ADR-0003）。dynamodb degenerate 行為維持不變（watchlist add/remove→NotImplementedError、get_watchlist→DEFAULT_WATCHLIST、seed/upsert_earnings→no-op）。`scheduler_handler._run()` 的 local vs live-fetch 流程分支不在此 sprint 收斂（非 storage 分派）。測試面向：既有 moto 測試（test_db/test_dedup/test_webhook）即為 DynamoDBBackend 覆蓋；需修 conftest（移除已刪除的 `_table.cache_clear()`、在 autouse env fixture 每測試前清 `get_backend` 快取防跨測試洩漏）。DynamoDBBackend 建構與 no-op 方法不得觸網（test_tools 在 dynamodb 無 moto 下會走 upsert no-op）。

## 2026-07-22 — watch-symbol-validation — evaluator — ✅ Pass
- 連結：docs/qa-reports/watch-symbol-validation.md
- 驗證：Unit pass（`PYTHONPATH=src uv run pytest tests/ -v`，118/118 passed）；Integration pass（`/watch`、`/unwatch` 情境含在同一 suite）；E2E not required；External Smoke not requested（blocking: false）
- 備註：修正前次 sprint 🟡 issue——`/watch`、`/unwatch` 補上 `SYMBOL_RE.fullmatch()` regex 驗證，正確拒絕特殊字元／過長／多字參數，接受 `BRK.B`/`BF-B` 型態；新增 6 條測試。🔴 0、🟡 0；循環次數 1；未解 P0/P1：0/0。

## 2026-07-22 — per-subscriber-watchlist-sprint — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/per-subscriber-watchlist-sprint.md, docs/contracts/per-subscriber-watchlist-sprint.md
- 驗證：Unit pass（`PYTHONPATH=src uv run pytest tests/ -v`，110/110 passed）；Integration pass（scheduler/processor per-subscriber 情境含在同一 suite）；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環次數 1；🔴 0、🟡 1（`/watch`、`/unwatch` 的「格式無效」判定僅擋空字串/含空白，未擋特殊符號等其他無效格式，非阻塞）、🟢 3；未解 P0/P1：0/0。Contract deliverable 逐條核對全數 ✅（一項標 ⚠️ 見上述 🟡）。

## 2026-07-22 — per-subscriber-watchlist-sprint — planner — spec + contract + ADR 完成
- 連結：docs/specs/per-subscriber-watchlist.md, docs/contracts/per-subscriber-watchlist-sprint.md, docs/decisions/0002-per-subscriber-watchlist-migration.md
- 驗證：Unit not run（planner 階段）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：把全域 watchlist 改為 per-subscriber junction table（PK `(chat_id, symbol)` + `idx_watchlist_symbol`），`ticker` 欄位/命名改名為 `symbol`；含既有 local DB migration（既有 subscriber 綁定其舊全域清單，見 ADR-0002）；移除 `_seed_watchlist()` 全域覆蓋改為 `/subscribe` seed-if-empty；scheduler local 改為 earnings_calendar ⋈ watchlist ⋈ subscribers per-user join；新增 `/watchlist`、`/watch`、`/unwatch` 指令。DynamoDB per-user 本 sprint 不做，僅記錄設計方向（PK=chat_id, SK=symbol + symbol GSI）。Open questions：per-subscriber summary 會線性增加 run_agent 次數（小規模可接受）。

## 2026-07-21 23:28 — groq-migration-and-market-summary — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定後實作）；分支 `feat/groq-migration-and-market-summary`
- 驗證：Unit pass（86/86，`PYTHONPATH=src uv run pytest tests/ -q`）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：兩部分——(1) LLM 由 Google Gemini（`gemini-2.5-flash` / `google-genai`）遷移到 Groq（`llama-3.3-70b-versatile` / `groq` SDK）；同步更新 `agent.py` tool-use loop、`config.py`、`.env.example`（`GEMINI_API_KEY`→`GROQ_API_KEY`）、`Makefile ssm-put`、`pyproject.toml`/`uv.lock` 與 README/CLAUDE/AGENTS 文件。(2) 新增每日盤勢摘要 `market_summary.py`：抓 Big 7 + AI 產業英文新聞存進本地 SQLite `news` 表，再由 Groq 綜整成繁中每日 briefing 並存進 `market_summaries`（附來源 news ids 供稽核）；`backend.py` 新增 `news`/`market_summaries` 表與存取方法，`local_server.py` 新增 `/market-summary`、`/news` endpoint，新增 `tests/test_market_summary.py`。

## 2026-07-21 15:00 — earnings-notifications — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定計劃後實作）
- 驗證：Unit pass（79/79）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：local backend 通知改為「sync 未來 30 天財報到 SQLite → watchlist ⋈ earnings_calendar join → 推播」；新增 `earnings_notifications` 冪等表、`earnings_calendar.updated_at` 與日期 index（含舊 DB migration）；Finnhub 失敗時推播帶警告並用快取資料；dynamodb（Lambda）路徑維持原 live-fetch 行為；新增 `make notify-earnings`。

## 2026-07-20 15:18 — local-first-storage-sprint — evaluator — ⚠️ Pass with issues
- 連結：`docs/specs/local-first-storage.md`, `docs/contracts/local-first-storage-sprint.md`, `docs/decisions/0001-local-first-storage-backend.md`, `docs/qa-reports/local-first-storage-sprint.md`
- 驗證：Unit pass；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環 2 次；repo `.venv` 完整測試 55/55 通過；`make test` 因當前 PATH 缺少 `uv` 而未啟動 pytest；未解 P0/P1：0/0。
