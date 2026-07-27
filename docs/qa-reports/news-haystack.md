# QA Report: news-haystack

- **Contract**: docs/contracts/news-haystack.md
- **Spec**: docs/specs/news-haystack.md
- **ADR**: docs/decisions/0011-news-haystack.md
- **Date**: 2026-07-27
- **Verdict**: ⚠️ Pass with issues（無 🔴 Critical、無 contract 條目 ❌，可合併；2 項 🟡 建議記錄／後續跟進，不阻塞）

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| A. `--table` 加 `news`（`earnings\|conversations\|news\|all`） | ✅ | `dump.py:41-45`；`test_dump_creates_missing_directories` 用 `table="all"` 驗證三檔同時產出 |
| A. `_dump_news`：單次全表 scan → 依 bucket 分流（`news`/`market_summary`，其餘忽略）→ 寫 `snapshots/<date>/news.md` | ✅ | `_scan_all` 只呼叫一次、Python 端 list comprehension 分流；`test_dump_news_unknown_bucket_ignored` 鎖住忽略行為 |
| A. news.md 格式（metadata 段、`## <SYMBOL> 新聞`/`## AI 產業新聞`/`## 市場摘要`、英文原樣） | ✅ | 逐行核對真實產出 `snapshots/2026-07-27/news.md`（321 筆一般新聞、7 個 big7 symbol + AI 產業新聞 section），英文標題/摘要原樣落地 |
| A. 排序（symbol 字母序、section 內 published_at 降序+同日 headline tiebreak、market_summary summary_date 降序） | ✅ | `_sort_news_rows` 兩段 stable sort 正確（先 headline 升冪、再依日期降冪 stable sort，保留同日 headline 升冪次序）；`test_dump_news_format_sections_and_sort` 用刻意亂序插入 + 同日雙筆驗證；真實產出 section 內日期確實降序 |
| A. NewsTable 空/近期無資料 → 仍寫檔 + 註記、不報錯 | ✅ | `test_dump_news_empty_table_no_error` |
| A. 目錄不存在自動建立 | ✅ | 沿用既有 `mkdir(parents=True, exist_ok=True)` 模式 |
| B. judge 只改 `_write_report` caveat（不改檢索/配對/短路/prompt/JSON 解析） | ✅ | `git diff` 確認 `judge.py` 唯一改動在 `_write_report` 的 caveat 文字區塊；`_judge_pair`/`_ask_judge`/`_extract_json`/`run()` 零改動 |
| B. caveat 文字：earnings+news、~14 天/~2 天、時間錯位判讀 | ✅ | 三段文字皆到位，`test_judge_report_caveat_reflects_earnings_and_news_haystack` 鎖住關鍵字；真實 `audit/2026-07-27/report.md` 確認落地 |
| B. judge 前置檢查維持以 earnings.md 為 sentinel、news.md 純加法 | ✅ | `run()` 只檢查 `earnings.md`，未改動 |
| C. `.gitignore` 無需改動 | ✅ | `git diff -- .gitignore` 無差異；`snapshots/` 已忽略 |
| Out of scope 未逾越 | ✅ | `git diff --stat` 只動 `dump.py`/`judge.py`/兩個測試檔/CHANGELOG + 新增 docs 三檔；`retrieval.py`、線上 runtime（`agent.py`/`tools/`/Lambda handlers/IaC）零改動、無新依賴 |
| Tests required（逐項） | ✅ | 見下方 Test results，8 個新測試對應 contract 逐條要求，含混索引正反例 |
| DoD：pytest 全綠 | ✅ | 428 passed（見下） |
| DoD：本機真實 dump NewsTable | ✅ | `snapshots/2026-07-27/news.md`：321 筆一般新聞、0 筆 market_summary（2 天 TTL 下無快取，如實記錄，非 bug） |
| DoD：本機真實 judge 對新聞類 Q/A 跑一輪 | ✅ | `audit/2026-07-27/report.md` #2「最近有什麼AAPL新聞」、#3「google最近有什麼新聞」皆真跑；但見下方 🟡 對「歸因為時間錯位」的訂正 |
| DoD：evaluator QA report 無 P0/P1 | ✅ | 本報告 🔴 0 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（sandbox 內 `test_local_integration.py` 因綁定本機 socket 被沙箱擋下 `PermissionError`，屬既有測試對 sandbox 網路限制的既知現象、與本次改動無關；停用 sandbox 重跑後全綠）
- 結果：**428 passed**，0 failed，0 skipped（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無（`test_audit_dump.py`/`test_audit_judge.py` 既有測試全部維持通過，`judge.py` 檢索/配對/短路邏輯零改動）
- 新增測試：`tests/test_audit_dump.py` +7（格式排序、unknown bucket 忽略、market_summary 不混入一般新聞、空表無錯、可檢索、混索引正反例）+ `tests/test_audit_judge.py` +1（caveat 文字）= 8，與 generator 自報一致；測試斷言直接用production 的 `agent.knowledge.retrieval.parse_sections`/`retrieve_scored`（非自製假 parser），可信度高

## 本機實測驗證（DoD，獨立複驗）

- `snapshots/2026-07-27/news.md`：真實跑通，321 筆一般新聞（AAPL/AMZN/GOOGL/META/MSFT/NVDA/TSLA + AI 產業新聞），0 筆 market_summary（2 天 TTL 特性，如實記錄，未報錯）。symbol section 依字母序排列（AAPL→AMZN→GOOGL→META→MSFT→NVDA→TSLA→AI 產業新聞），每 section 內日期確實降冪。
- `audit/2026-07-27/report.md`：`--limit` 真跑 6 筆，統計 grounded=1 ungrounded=4 no-data=1。獨立以 `retrieve_scored` 對真實快照重測（非只信 generator 分數）確認：
  - 新聞 Q/A（`最近有什麼AAPL新聞` + 完整回答）首命中 `news#AAPL 新聞`（20.268）優於 `earnings#AAPL`（15.761）；`google最近有什麼新聞` 首命中 `news#GOOGL 新聞`（21.441）優於 `earnings#NUE`（10.825）。
  - 財報 Q/A（`META的財報是什麼時候` + 回答）首命中 `earnings#META`（16.768）優於 `news#META 新聞`（11.128）。
  - 在 judge 實際使用的 query 型態（question+answer 全文拼接）下，混索引不稀釋在這次真實資料上成立。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）

- **`src/agent/audit/dump.py`（`_dump_news` 的 per-symbol 單一大 section 設計）+ `src/agent/audit/judge.py:40`（既有 `MAX_SECTION_CHARS = 1000`，本次未改動）**：DoD 對真實 judge 跑的 `ungrounded` verdict（`audit/2026-07-27/report.md` #2「最近有什麼AAPL新聞」）歸因為「時間錯位（14 天滾動窗口洗掉）」，但**經獨立複驗，這個歸因不準確**。bot 回答引用的 5 則英文標題（如 *Apple's Lawsuit Against OpenAI...*、*I'm Buying Apple's AI Advantage...*）**全部**存在於 `snapshots/2026-07-27/news.md` 的 `## AAPL 新聞` section 內（日期 2026-07-20，在 14 天保留窗口內，未被 TTL 淘汰）。真正原因是：`## AAPL 新聞` section 因熱門 symbol 新聞量大（本次真實資料達 16135 字元），而既有 `_ask_judge` 對送進 Groq prompt 的每個命中片段套用 `MAX_SECTION_CHARS = 1000` 截斷——實測該 section 截斷後只剩 2026-07-26 附近最新約 10 則，2026-07-20 的 5 則舊標題**根本沒送進 judge prompt**，judge 因此看不到證據而誤判 `ungrounded`。
  這代表：(1) DoD 附上的「時間錯位 caveat 實例」佐證本身不成立，是誤歸因；(2) 現有 caveat 文字（TTL 淘汰→no-data 屬缺料）未涵蓋這個新出現的失效模式（section 過大被截斷→ungrounded 誤判，非 TTL、非幻覺）；(3) 此為既有 `MAX_SECTION_CHARS` 常數與**本次新增**的「每 symbol 單一大 section」設計交互出現的問題——v1 只有 earnings.md（每 symbol 僅 2-3 行，遠低於 1000 字元）從未觸發過這個截斷路徑，本次新增熱門 symbol 的大量新聞後才首次暴露。
  contract 明確禁止本次改動 judge 的 prompt/檢索核心，所以**不要求本 sprint 修正**，但建議：(a) 後續 sprint 評估提高 `MAX_SECTION_CHARS`（news section 專屬更高上限，或依 section 筆數動態抓最新 N 則而非字元截斷）；(b) 若近期不處理，至少在 caveat 或 ADR-0011 Consequences 補一句「熱門 symbol 新聞量大時，既有 prompt 截斷可能使 judge 看不到片段內較舊（但仍在保留窗口內）的證據，導致 ungrounded 誤判，非必然代表 bot 真的幻覺」，避免未來讀報告的人誤判幻覺率。不阻塞合併。

- **`docs/decisions/0011-news-haystack.md` §Decision 4（heading 帶類型詞降稀釋設計）**：ADR 聲稱「news query 含 `symbol`+`新聞` 兩 token 2x 命中 news heading，強勝只含 symbol 的 earnings heading」，並以此作為「混合索引不互相稀釋」的核心論據，該論據已在 moto 玩具語料測試中驗證通過。但**用真實生產規模快照獨立複驗**（`retrieve_scored(f"{symbol} 最近有什麼新聞", ...)`，即 contract/spec 逐字列出的驗收查詢）發現：7 個 big7 symbol 中有 **6 個**（AAPL/AMZN/GOOGL/MSFT/NVDA/TSLA）首命中仍是 `earnings#<SYMBOL>` 而非 `news#<SYMBOL> 新聞`，只有 META 符合設計預期。原因是 news.md 的 per-symbol section 在真實資料下篇幅很大（數千至上萬字元、數十則新聞），BM25 的文件長度正規化會壓低長 section 的相對分數，抵銷了 heading 2x 權重的優勢；而 earnings.md 對應 section 極短（2-3 行），heading 命中在正規化後反而分數更高。這與 moto 測試（每個 section 僅 1-3 筆假資料）的結果不一致，代表**現有測試對「混索引不稀釋」的驗證只在玩具規模成立，未反映生產規模行為**。
  ADR-0011 Consequences 已將「混合索引下 BM25 為未正規化分數，理論上仍可能有邊角稀釋」列為已知 trade-off，且 contract 明確排除本 sprint 修改 `retrieval.py` BM25 核心，故**不阻塞合併**。實務上，judge 真正使用的 query 是「問題+完整回答」而非 contract 驗收條件的短查詢（回答內容通常會包含具體新聞標題文字，能有效消歧，本次真實 judge 跑的兩則新聞 Q/A 確實正確命中 `news#...`），所以線上稽核行為目前未受影響。但建議：(a) 補充這個真實資料下的量化結果到 ADR Consequences（把「理論上」的 trade-off 換成有實測數據佐證的已知限制）；(b) 後續若要更嚴謹驗證，contract 的「檢索命中」驗收測試應額外覆蓋一次真實規模（或用更貼近真實 section 長度的合成資料）而非只用 1-3 筆的玩具語料，避免測試通過但生產行為不同調的落差。

### 🟢 Suggestions
- **`src/agent/audit/dump.py:136-140`**：`symbol` 判斷用 `if symbol:`（truthy），若未來资料出現 `symbol=""`（空字串而非 `None`）會被歸入 `ai_items` 而非任何 symbol section。目前生產資料與測試都沒有這個邊角案例，純理論風險，不需改動。
- **`src/agent/audit/dump.py:121`**：`days_back` 若缺欄位時 fallback 字串 `"?"`，與其他數值 fallback（如 earnings 的 `EPS 預估`）風格一致，純觀察，無需修。

### ✅ What looks good
- **caveat 文字準確反映 TTL 實況**：獨立核對 `src/agent/db/backend.py` 的 `NEWS_TTL_SECONDS = 14 * 24 * 3600`、`MARKET_SUMMARY_TTL_SECONDS = 2 * 24 * 3600`，與 judge caveat「一般新聞 ~14 天、市場摘要 ~2 天」完全一致。
- **surgical diff**：`git diff --stat` 只動 `dump.py`（+97）、`judge.py`（+11 caveat 文字）、兩個對應測試檔、`docs/CHANGELOG.md`，加上 planner 產出的 spec/contract/ADR 三個新檔；未觸及任何線上 runtime（`agent.py`/`tools/`/Lambda handlers/IaC 零改動）、`retrieval.py` BM25 核心零改動，完全對齊 contract 邊界。
- **無密鑰/隱私外洩風險**：`git status`/`git diff` 未發現任何 secret 字樣；真實產物 `snapshots/`、`audit/` 正確落在既有 `.gitignore` 規則下、未進暫存區。
- **測試品質高**：新增的 dump 測試直接呼叫 production `retrieval.parse_sections`/`retrieve_scored`（而非自製假 parser），對格式相容性與檢索命中的斷言可信；混索引正反例測試同時 dump 真實 earnings + news 兩檔驗證雙向不稀釋（雖然如上 🟡 所述，這個玩具規模的正反例在生產規模下不完全成立，但測試本身寫得對）。
- **empty table / unknown bucket 邊角處理正確**：`_dump_news` 對空表寫「近期無資料」註記不報錯、未知 bucket 正確忽略、`market_summary` 與一般新聞不互相混入，逐一有獨立測試鎖住且讀碼確認邏輯正確。
- **排序穩定性設計正確**：兩段 stable sort（先按 headline 排、再按日期 stable sort）是解決「published_at 降序 + 同日 headline tiebreak」的簡潔正確作法，讀碼 + 真實產出（`## AAPL 新聞` 內日期降序排列）雙重確認無誤。

## 結論
- 是否可合併：**是**
- 最該先處理的 1-2 件事：
  1. 修正 DoD/交接文件中「AAPL 新聞 ungrounded 屬時間錯位」的歸因（實際是既有 `MAX_SECTION_CHARS=1000` 截斷造成 judge 看不到仍在保留窗口內的舊標題），避免後續維護者誤讀報告、誤判幻覺率；後續評估是否需要提高新聞 section 的截斷上限或改用更細粒度的片段。
  2. 在 ADR-0011 Consequences 補充「混索引不稀釋」在真實生產規模資料下的量化結果（7 個 big7 symbol 中 6 個短查詢會被 earnings section 壓過），把現有「理論上」的 trade-off 描述換成有實測佐證的已知限制，並記錄 judge 實際查詢型態（question+answer）目前仍能正確消歧，此限制暫不影響線上稽核行為。
