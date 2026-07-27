# QA Report: playbook-grounding

- **Contract**: docs/contracts/playbook-grounding.md
- **Date**: 2026-07-27
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| 1. `knowledge/playbooks/` 三份 md，各 section 含 觸發語/工具指引/回覆要求/禁止 四小節 | ✅ | `earnings.md`（2 sections）、`market-trends.md`（1）、`stock-news.md`（1）；四小節齊全（`test_each_playbook_section_has_all_four_subsections` + 手動核對） |
| 1. 內容來源 = S3/S4/S7/S10/S11/S13–S20 拆遷 | ✅ | 逐條核對 `git diff src/agent/prompts.py` 與三份 playbook 內容，S 編號與去向與 spec 表一致，語意完整落地（見下方「S1–S25 拆遷正確性」） |
| 1. 觸發語涵蓋真實問法 | ✅ | earnings 含「本週」「財報」「發布」；market-trends 含「大盤」「市場摘要」；stock-news 含「新聞」「消息」 |
| 2. SYSTEM_INSTRUCTION 拆遷：MOVE 移除、STAY 保留 | ✅ | `git diff` 逐行核對，13 條 MOVE 全數移除、10 條 STAY 逐字保留（無 diff context 變動） |
| 2. system/playbook 無語意重複；`SYSTEM_INSTRUCTION` 非空語句通順 | ✅ | 人工通讀 `src/agent/prompts.py` 現況，語句連貫；`test_prompt_constants_are_nonempty_strings` 續綠 |
| 2. `SUMMARY_SYSTEM_PROMPT_TEMPLATE`/`summary_system_prompt`/`COMPANY_PROMPT_TEMPLATE` 不動 | ✅ | `git diff` 確認這三者所在行域無改動 |
| 3. 索引來源參數化，playbook/KB 各自 `lru_cache`、互不污染 | ✅ | `_load_sections(source_dir)`/`_build_index(source_dir)`/`_get_index(source_dir)` 皆已參數化；`test_kb_index_never_contains_playbook_file_stems` 等 4 條隔離測試綠燈 |
| 3. stdlib-only，不 import agent.agent/market_summary/tools | ✅ | `retrieval.py` 只 import `math/re/collections/dataclasses/functools/pathlib` |
| 3. `retrieve_scored(query, top_k, source_dir=...)` | ✅ | 簽名符合，回傳 `list[(Section, float)]` |
| 3. `search_knowledge_base`／`retrieve(query, top_k)` 行為、預設索引、回傳結構不變 | ✅ | `retrieve()` 內部委派 `retrieve_scored(..., source_dir=_KNOWLEDGE_DIR)` 並丟分數，`test_retrieve_matches_retrieve_scored_default_source` 鎖住；`knowledge_base_tool.py` 呼叫點未動 |
| 4. `run_agent()` 用當前 user message（不含 history）檢索 playbook，`top_k=2` | ✅ | `_retrieve_playbook_hits(user_message)` 只吃 `user_message`，未混入 `history` |
| 4. `MIN_SCORE` 常數 + 實測校準理由 | ✅ | `MIN_SCORE=3.5`，註解列出正例/雜訊/無關三段分數；本次獨立重跑 debug CLI 驗證數字與註解一致（見下方實測證據） |
| 4. 命中 → 注入於基礎 system 之後、history 之前，含 `檔名#標題` | ✅ | 獨立腳本驗證 `messages` 順序為 `[system, system(playbook), user, ...]`／`[system, system(playbook), history..., user]`，內容含 `[來源：earnings#財報行事曆時間查詢]` |
| 4. 無命中/全低於門檻 → 不注入，fallback 現行流程 | ✅ | 獨立腳本驗證「今天天氣如何」→ `messages` 只有 1 個 system message |
| 5. `playbook_injection` structured log | ✅ | 獨立重跑（非套用 generator 測試）觀察到 `playbook_injection query=... hits=[...] injected=True/False` |
| 5. `_run_tool` 成功 log name/args(sanitized)/回傳筆數或型別 | ✅ | 獨立重跑觀察到 `tool get_analyst_estimates succeeded: args={"symbol": "AAPL"} result_type=dict result_size=4`；失敗行為（`log.warning`）未變 |
| 5. debug CLI 可切換 playbook / 術語 KB | ✅ | `python -m agent.knowledge.debug "<query>" --source kb\|playbook --top-k N` 實測正常 |
| DoD：全部 deliverable 打勾 | ✅ | 如上 |
| DoD：`pytest` 全綠 | ✅ | 停用沙箱重跑 368 passed（見下方 Test results） |
| DoD：chat_cli/local_server 實測可見 log | ✅ | 未直接跑 `chat_cli`（需要 `GROQ_API_KEY`/網路，沙箱不可及），改用等價的獨立腳本直接呼叫 `run_agent()`（`chat_cli.py` 亦是同一入口）+ mock Groq client 驗證，日誌與訊息順序皆如預期，證據見下 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：355 passed / 13 errors（`test_local_integration.py` 的 `ThreadingHTTPServer` socket bind `PermissionError: Operation not permitted`，為既有 sandbox 限制，非本次改動造成，過往多份 QA report 亦記錄過同一現象）
- 停用沙箱重跑：**368 passed, 0 failed**（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無回歸（`test_prompts.py` 三則 MOVE 斷言已同步改為斷言 playbook 檔案內容；其餘既有測試維持綠燈）

## 重點審查證據

### S1–S25 拆遷正確性
逐行核對 `git diff src/agent/prompts.py`：
- MOVE 全數移除：S3/S4（新聞翻譯與歸納整合、忽略行銷導流）、S7（分析師評級買入/持有/賣出分佈）、S10（明確代碼→雙工具+symbol 參數）、S11（本週/近期/接下來/最近→未來視角日期區間）、S13–S17（市場摘要 `get_market_summary`/`days_back` 規則）、S18–S20（未公布提醒、勿因查無斷言找不到資料、每筆標日期+時段）——13 條全數在 diff 中被刪除，且未在檔案其他位置殘留。
- STAY 全數保留：S1/S2/S5/S6/S8/S9/S12/S21/S22–S25，diff 顯示這些行完全未變動（無新增/刪除的 context 行），語意逐字未動。
- 語意落地檢查：三份 playbook 內容與 spec 表逐條比對，S3/S4→`stock-news.md`、S7/S10/S11/S18/S19/S20→`earnings.md` 兩個 section、S13–S17→`market-trends.md`，內容完整（部分加了反引號包裹工具名/參數名，屬無害排版強化，不影響語意）。
- 未發現 system 與 playbook 語意重複；S8（術語代碼對照）確如 spec 設計留在 system，`earnings.md` 的 S20 回覆要求只引用「盤前/盤後/盤中」而未重述 bmo/amc/dmh 定義，符合「S8 不需要在 playbook 重述」的拆遷原則。

### 檢索隔離
- `retrieval.py` 的 `_load_sections`/`_build_index`/`_get_index` 已參數化 `source_dir`；`retrieve()`（`search_knowledge_base` 用）內部固定呼叫 `retrieve_scored(..., source_dir=_KNOWLEDGE_DIR)`，行為與回傳結構未變。
- `PLAYBOOKS_DIR = _KNOWLEDGE_DIR / "playbooks"`，`_load_sections` 用非遞迴 `glob("*.md")`，天然互斥（`knowledge/*.md` 不會遞迴進 `knowledge/playbooks/`）。
- 新增 4 條隔離測試（`test_kb_index_never_contains_playbook_file_stems`、`test_playbook_index_only_contains_playbook_file_stems`、`test_playbook_retrieval_never_returns_kb_headings`、`test_kb_retrieval_never_returns_playbook_headings`）全綠。

### 核心驗收案（獨立重跑 `python -m agent.knowledge.debug`，非引用 generator 自報）
```
$ PYTHONPATH=src uv run python -m agent.knowledge.debug "本週有什麼公司發布財報" --source playbook --top-k 5
10.323  earnings#財報行事曆時間查詢
2.679   stock-news#個股新聞查詢與摘要整理
2.665   earnings#特定個股財報與分析師預期查詢
0.947   market-trends#大盤與市場摘要查詢

$ PYTHONPATH=src uv run python -m agent.knowledge.debug "今天大盤怎樣" --source playbook --top-k 5
6.922   market-trends#大盤與市場摘要查詢
0.879   earnings#財報行事曆時間查詢

$ PYTHONPATH=src uv run python -m agent.knowledge.debug "AAPL 有什麼新聞" --source playbook --top-k 5
7.292   stock-news#個股新聞查詢與摘要整理
1.177   earnings#財報行事曆時間查詢
0.749   earnings#特定個股財報與分析師預期查詢

$ PYTHONPATH=src uv run python -m agent.knowledge.debug "今天天氣如何" --source playbook --top-k 5
0.947   market-trends#大盤與市場摘要查詢
0.879   earnings#財報行事曆時間查詢

$ PYTHONPATH=src uv run python -m agent.knowledge.debug "AAPL 最近新聞" --source playbook --top-k 5
3.595   stock-news#個股新聞查詢與摘要整理
0.879   earnings#財報行事曆時間查詢
0.749   earnings#特定個股財報與分析師預期查詢
0.651   market-trends#大盤與市場摘要查詢
```
上述數字與 `agent.py` 中 `MIN_SCORE=3.5` 註解所載校準數據（正例最低 ~6.9、跨 playbook 雜訊上限 ~2.7、無關查詢上限 ~0.9）一致，非事後合理化。經 `top_k=2` + `MIN_SCORE=3.5` 過濾後：
- 「本週有什麼公司發布財報」→ 僅 `earnings#財報行事曆時間查詢`（10.323）注入，`stock-news`（2.679，top_k=2 內但低於門檻）被過濾——**符合契約「不含 stock-news 任何 section」**。
- 負例「AAPL 最近新聞」→ 僅 `stock-news`（3.595）注入，`earnings#財報行事曆時間查詢`（0.879）遠低於門檻——**跨類問題未誤命中 earnings 行事曆 section**。

### 注入位置與觀測 log（獨立腳本驗證，非執行 generator 寫的測試檔）
用 `unittest.mock.MagicMock` 直接呼叫 `run_agent()`（與 `chat_cli.py` 同一入口，繞過需要網路/`GROQ_API_KEY`的 sandbox 限制）：
```
=== Case 1: 「本週有什麼公司發布財報」（有 history）===
playbook_injection query='本週有什麼公司發布財報' hits=[{'source': 'earnings#財報行事曆時間查詢', 'score': 10.323}] injected=True
MESSAGES ROLES: ['system', 'system', 'user', 'assistant', 'tool']  # 無 history 案例
SYSTEM[1] 內容開頭：以下是根據使用者當前問題比對到的操作指引（playbook），請依此指引選擇工具與組織回覆…
                    [來源：earnings#財報行事曆時間查詢]
                    ## 財報行事曆時間查詢...

=== Case 2: 「今天天氣如何」===
playbook_injection query='今天天氣如何' hits=[] injected=False
MESSAGES ROLES: ['system', 'user']  # 無 playbook system message，fallback 正確
```
`_run_tool` 成功 log（mock `get_analyst_estimates` 回傳 dict）：
```
tool get_analyst_estimates succeeded: args={"symbol": "AAPL"} result_type=dict result_size=4
```
失敗路徑維持 `log.warning`（未動，另用真實 `get_earnings_calendar` 因缺 `FINNHUB_API_KEY` 觸發驗證：`tool get_earnings_calendar failed: 'FINNHUB_API_KEY'`）。

`args` 未見任何 token/secret 洩漏——工具函式簽章（`symbol`/`date_from`/`date_to`/`days_back`）本就不含憑證參數，`_sanitize_error` 的 `token=` 遮蔽為既有防禦性機制沿用，非本次新增風險。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- `knowledge/playbooks/earnings.md` 第一個 section 的「禁止」小節（"不要因為查無結果就直接回覆找不到資料而不嘗試其他工具"）與「工具指引」小節末句語意有些微重疊（同一規則在同一 section 內被複述兩次）。不違反契約（契約只禁止 system/playbook 兩地重複，未禁止 section 內部小節間的呼應強化），且此重複本身是刻意的規則加強，不影響行為，僅供未來精簡時參考。
- `tests/test_playbook_injection.py::test_run_tool_success_logs_name_args_and_result_info` 用手動 `try/finally` 暫換 `TOOL_FUNCTIONS` 字典項，未用 `monkeypatch` fixture；功能正確，僅風格上可再與其他測試對齊（non-blocking）。

### ✅ What looks good
- S1–S25 拆遷表對照精確，MOVE 全刪、STAY 逐字保留，`git diff` 呈現外科手術式變更（只動必要 hunk），無夾帶重構。
- `MIN_SCORE=3.5` 校準有實測數據佐證且註解誠實記錄置信區間，本次獨立重跑驗證數字未經美化。
- 兩套索引隔離設計簡潔（子目錄 + 非遞迴 glob 天然互斥），無需額外 flag 或條件判斷，測試覆蓋到位。
- Pre-injection 只吃 `user_message` 不含 `history`，結構性阻斷歷史污染，呼應本 sprint 的根因分析。
- 新增程式碼量精簡（`agent.py` diff 僅 60 行左右），無投機性抽象或未要求的彈性設計。
- 測試分層清楚：`test_knowledge_retrieval.py`（純檢索層）、`test_playbook_injection.py`（agent 整合層）、`test_knowledge_debug.py`（CLI 層）、`test_prompts.py`（拆遷同步），互不重疊又能各自定位問題。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；🟢 兩則為未來可選的小整理，不影響本次合併。
