# QA Report: summary-timewindow

- **驗收模式**: feature（無 contract，對照使用者需求 + 程式碼品質）
- **Date**: 2026-07-25
- **Verdict（round 1）**: ⚠️ Pass with notes → 主流程判定回修（需求 5 字面未滿足）
- **Verdict（最終，round 2 複驗後）**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. `get_market_summary` schema `days_back` description 補時間詞對應 | ✅ | `today/今天/本日 -> 1`、`this week/本週/近一週/過去七天 -> 7`、預設 1，字面完整 |
| 2. `SYSTEM_INSTRUCTION` 補「依時間詞傳 days_back」+「回覆明說資料涵蓋區間」 | ✅ | `prompts.py` 兩句都補齊 |
| 3. `generate_market_summary` prompt 措辭隨 days_back 切換 + 回傳 `date_range` + 區間資訊進 user_content | ⚠️ | 功能到位，但「須註明日期區間」句對兩個分支都生效，見下方判定 |
| 4. 兩 backend published_at 區間篩選測試 | ✅ | 新增 sqlite 版 `test_local_list_news_since_filters_and_orders_by_published_at`，與既有 DynamoDB 測試逐字對稱（邊界相等、排除更舊、newest-first） |
| 5. 「今日摘要」（days_back=1）行為維持不變 | 🟡 | **非零變化**——見下方詳細判定，round 2 回修處理 |

## days_back=1 行為判定（round 1 主要 finding）

實測比對 `summary_system_prompt(1)` 與 `git show HEAD:src/agent/prompts.py` 的舊版 `SUMMARY_SYSTEM_PROMPT`：字串**不相等**。差異為新增一句「摘要中須註明這份資料實際涵蓋的日期區間。」，該句寫死在 template、不受 days_back 條件化，days_back=1 也拿到；`user_content` 對 days_back=1 也附加了日期區間文字。

影響：`push_daily_summary()`（每日 EventBridge 排程固定 days_back=1）把 `generate_market_summary()` 的文字原封不動推給訂閱者、完全不經 `SYSTEM_INSTRUCTION`——此改動使每日排程推播的措辭與改動前不同，違反需求 5 字面「維持不變」。`test_summary_system_prompt_daily_wording` 僅做關鍵字存在性檢查，抓不到此差異。

處置：主流程採 evaluator 建議修法 (a) 回送 generator——「須註明日期區間」與 user_content 的區間附註**只在 days_back>1 生效**，days_back=1 的 system prompt / user_content 逐字恢復；`date_range` 資料欄位（純資料）兩分支照舊回傳；並補「days_back=1 prompt 逐字比對」回歸測試。

## Test results（round 1）

- `PYTHONPATH=src uv run pytest tests/ -v` → **295 passed, 0 failed**（非沙盒；沙盒內 12 個 `test_local_integration.py` socket bind errors 屬既有環境限制，非回歸）

## Findings（round 1）

### 🔴 Critical
無。

### 🟡 Warnings
- `prompts.py` `SUMMARY_SYSTEM_PROMPT_TEMPLATE` 新增句 + `market_summary.py` user_content 區間附註對 days_back=1 也生效，改變每日排程推播措辭（需求 5 違反）→ round 2 回修
- `test_summary_system_prompt_daily_wording` 關鍵字斷言抓不到「多一句指示」型變化 → round 2 補逐字回歸測試

### 🟢 Suggestions
- `market_summary_tool.py` 工具層 description 仍寫 "Generate today's US stock market summary"，多天窗口下措辭略舊（不在需求範圍，不強制）
- `prompts.py` 函式與常數間空行數風格 nit（repo 無 lint target，不處理）

### ✅ What looks good
- `_compute_date_range()` 的 `published_at[:10]` 切片假設在兩 backend 寫入格式（`datetime.isoformat()` 原樣儲存）下皆成立；無新聞/單日/多日三情境皆有測試
- 全 repo grep 無殘留舊名 `SUMMARY_SYSTEM_PROMPT` 引用；`prompts.py` 維持零外部依賴
- 兩個 return 分支（含無新聞早退）都正確帶 `date_range`
- schema description 斷言測試確實驗到 `today`、`本日`、`-> 1`、`本週`、`-> 7`

## Round 2（回修後）

### 回修內容
- `SUMMARY_SYSTEM_PROMPT_TEMPLATE` 加 `{coverage_note}` 佔位，days_back<=1 時為空字串、>1 才帶「摘要中須註明這份資料實際涵蓋的日期區間。」
- `generate_market_summary()` 的 `user_content` 同樣條件化（`coverage_clause`），days_back<=1 回到舊版字面
- `date_range` 資料欄位（`_compute_date_range()`）兩分支照舊回傳，不受本次回修影響
- 新增 2 條 byte-for-byte 回歸測試：`test_prompts.py::test_summary_system_prompt_days_back_1_is_byte_for_byte_identical_to_pre_sprint_baseline`（凍結 `_OLD_SUMMARY_SYSTEM_PROMPT`）、`test_market_summary.py::test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_pre_sprint_baseline`（mock Groq 攔截 `messages[1]` 比對），既有 `test_generate_summary_reads_store_and_persists` 補上兩條負向斷言（`"實際涵蓋日期" not in`、`"註明這份資料涵蓋的日期區間" not in`）

### 複驗結果

**1. 獨立驗證 days_back=1 逐字恢復（不信任 generator 的測試，自己比對）**

用 `git show HEAD:src/agent/prompts.py` 動態 exec 出舊版 `SUMMARY_SYSTEM_PROMPT`，與 `summary_system_prompt(1)`／`summary_system_prompt(0)`／`summary_system_prompt(-5)` 逐一比對：

```
days_back=1 EQUAL: True
days_back=0 EQUAL: True
days_back=-5 EQUAL: True
```

✅ **確認 days_back<=1 的 system prompt 與 pre-sprint 舊版字串完全一致（byte-for-byte）**。

**2. 凍結字串測試品質**

- `_OLD_SUMMARY_SYSTEM_PROMPT`（`test_prompts.py`）：獨立比對其內容與 `git show HEAD:src/agent/prompts.py` 的實際 `SUMMARY_SYSTEM_PROMPT` 常數 —— **完全相同**，不是憑印象手抄、也沒有字元誤差。
- mock Groq 攔截測試（`test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_pre_sprint_baseline`）：確認它真的讀取 `mock_groq.chat.completions.create.call_args.kwargs["messages"][1]["content"]` 並與重建的 expected user_content 做 `==` 比對（不是只查關鍵字存在），且硬編碼的連接句「以下是大盤收盤數據與過去 1 天的新聞資料，請產出每日市場摘要：」與生產程式碼路徑（`index_lines`／`select_news`／`_format_news_block`，皆非本次改動範圍）組合，屬合理且不脆弱的測試設計。
- **Mutation 驗證**（evaluator 主動執行，非 generator 自報）：暫時把 round-1 的 bug 重新引入（`coverage_note` 對 days_back<=1 也給非空字串），跑 `tests/test_prompts.py tests/test_market_summary.py` → `test_summary_system_prompt_days_back_1_is_byte_for_byte_identical_to_pre_sprint_baseline` **正確 FAILED**（其餘 26 個 PASSED），證明這條回歸測試真的有偵測力，不是空殼斷言。還原程式碼後重跑全數回綠。

✅ 凍結字串測試品質可信賴。

**3. days_back>1 行為未被回修弄壞**

獨立執行 `summary_system_prompt(7)` 與 `summary_system_prompt(2)`：
- coverage note（「摘要中須註明這份資料實際涵蓋的日期區間。」）存在 ✅
- 措辭切換為「過去 N 天市場摘要」「過去 N 天發布」「本期間總結」✅，且不含「每日」字樣 ✅

✅ 未被回修破壞。

**4. 完整測試**

- `PYTHONPATH=src uv run pytest tests/ -v`（停用沙箱）→ **297 passed, 0 failed**，與 generator 自報一致，無 regression。
- （沙箱模式下同 round 1，`test_local_integration.py` 12 個因 `ThreadingHTTPServer` socket bind 被沙箱擋下，屬既有環境限制，非本次改動造成。）

### Round 2 Findings

**🔴 Critical**：無。

**🟡 Warnings**：無（round 1 的 🟡 已由回修解決並經獨立複驗確認）。

**🟢 Suggestions（維持 round 1，未強制處理）**：
- `market_summary_tool.py` 工具層 description 仍寫 "Generate today's US stock market summary"，多天窗口下措辭略舊，不在需求範圍。
- `prompts.py` 函式與常數間空行數風格 nit，repo 無 lint target，不處理。

### Round 2 結論

需求 5「今日摘要（days_back=1）行為維持不變」**已由回修修正並經 evaluator 獨立驗證（非僅信賴 generator 自報）確認為真**：system prompt 與 user_content 皆恢復 byte-for-byte 一致，`date_range` 資料欄位不受影響，days_back>1 的期間措辭與 coverage note 均正常運作，全測試綠燈，且用 mutation 測試證明新增的回歸測試具備真實偵測力。

## 最終 Verdict

**✅ Pass**

- 需求 1-5 全數 ✅ 達成（含 round 1 已通過項目）
- 🔴 0、🟡 0（round 1 的 1 條已解決）、🟢 2（不影響合併）
- 循環次數：2（round 1 → 回修 → round 2 複驗通過）
- 未解 P0/P1：0
- 測試：`PYTHONPATH=src uv run pytest tests/ -v` → 297 passed, 0 failed，無 regression

---
註：本報告由主流程代 evaluator 落地（round 1 執行時 evaluator 的 Write 新建報告檔被 harness 攔截）；round 1 內容為 evaluator round 1 回覆原文整理，round 2 段落由 evaluator 以 Edit 直接補上。
