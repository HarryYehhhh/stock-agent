# QA Report: tech-stock-focus

- **Contract**: 無 contract（feature skill，直接對照需求描述 + 程式碼品質驗收）
- **Date**: 2026-07-31
- **Verdict**: ✅ Pass（第一輪 🔴 Critical 已於複驗確認修復；1 項 🟡 P2 記錄不阻塞）

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

### 🟡 Warnings（Should Fix）
- **`src/agent/tools/market_summary_tool.py:18`**：`MARKET_SUMMARY_TOOL` 的 `description`（面向 LLM 的 tool schema 說明）仍寫 "Big 7 tech stock news, AI industry news and major index performance"，未反映追蹤清單已擴大到 12 檔科技股。
  - **為何是問題但非阻塞**：此 description 只用於 agent 的 tool-selection 決策（`agent.py` 的 Groq tool-use loop），不是 `generate_market_summary()` 實際產出摘要用的 system prompt（那個已經在 `prompts.py` 正確更新）。觸發呼叫此 tool 的判斷依據是「overall market / daily market recap」等泛用描述，並未以「僅限 Big 7」設限，因此誤導 agent 決策路徑的風險低——例如使用者問 "AMD 今天新聞"，agent 大概率仍會傾向呼叫 `get_stock_news(AMD)` 而非誤判 `get_market_summary` 不涵蓋 AMD 而略過查詢；即使呼叫了 `get_market_summary`，實際回傳內容仍包含 12 檔的量化資料，不會因為 description 過時而遺漏。
  - **判定**：P2（可另案處理，不阻塞本次合併）。這也是需求文件本身列為「未列入本次範圍」的項目，generator 未動屬合理範圍界定。
  - **建議修法**：下次順手把 "Big 7 tech stock news" 改成 "major tech stock news"（或列出涵蓋範圍改為泛稱），避免長期與程式碼行為脫節、誤導未來維護者。

### 🟢 Suggestions
- **`src/agent/market_summary.py:203-213`**（`select_news()`）：內部區域變數與回傳 dict key 沿用 `big7`（例如 `big7: list[dict] = []`），與外部常數 `TECH_SYMBOLS` 命名不一致。這是需求 5 明確要求保留（避免牽動 DB `category` 值與既有消費端），非缺陷，僅記錄供未來若真的要做全面更名時參考。

### ✅ What looks good
- `fetch_stock_quotes()`（`market_summary.py:249-266`）完整複用 `fetch_index_quotes()` 已驗證過的 skip-on-failure 模式（try/except + `current_price` 檢查），風格一致，未重新發明邏輯。
- Cache hit 路徑正確地在呼叫任何報價抓取（含新的 `fetch_stock_quotes`）之前就 return，符合「cache hit 不打 Finnhub」的既有設計與本次需求 2 的要求。
- `days_back=1` 的 byte-for-byte baseline 測試（`test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_baseline`）與 prompt 側對應測試都同步更新並鎖住新格式，避免生產環境（`push_daily_summary` 固定用 `days_back=1`）的 user_content 意外漂移。
- `TECH_SYMBOLS` 常數重新命名後，全域搜尋確認無殘留的舊 `BIG7` 常數引用；DB `category`/dict key `"big7"` 字串保留範圍精準，未過度更名波及既有資料相容性。
- 測試改動全部維持既有 mock 風格（`monkeypatch`、`MagicMock`），無真實外部呼叫，符合專案測試慣例。

## 結論
- 是否可合併：**是**（round 2 複驗：🔴 Critical 已確實修復，需求 checklist 全數 ✅，555 passed / 13 既有 sandbox error / 0 failed，無 regression）
- 最該先處理的 1–3 件事：
  1. （P2，可另案，不阻塞）`market_summary_tool.py:18` 的 tool description 措辭更新為不限定 Big 7，避免長期與程式碼行為脫節。
