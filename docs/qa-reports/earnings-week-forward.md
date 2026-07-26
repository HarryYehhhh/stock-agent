# QA Report: earnings-week-forward

- **Contract**: 無 contract（feature skill，直接對照使用者需求）
- **Date**: 2026-07-26
- **Verdict**: ✅ Pass

## 需求對照 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. 「本週/近期/接下來/最近」等未明確指定過去的時間詞詢問財報行事曆時，date_from=今天、date_to=今天+7 | ✅ | `src/agent/prompts.py:23-25` 新增明確指令；`agent.py:77` 既有的 `f"今天日期是 {date.today().isoformat()}。\n{SYSTEM_INSTRUCTION}"` 已把「今天日期」注入同一則 system message，指令中的「今天日期」可直接對應，不會落空 |
| 1b. 明確查過去財報（上週/已公布）才用過去區間 | ✅ | `prompts.py:26` 明確排除句 |
| 2. 每筆財報回覆標示日期與盤前/盤後/盤中，hour 空值標「時段未定」 | ✅ | `prompts.py:35-37` |
| 實作範圍限定在 `prompts.py` 的 SYSTEM_INSTRUCTION + 對應測試 | ✅ | `git diff main --stat` 僅 `src/agent/prompts.py`（+6）與 `tests/test_prompts.py`（+17），無其他檔案改動 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：329 passed，12 個 `tests/test_local_integration.py` 因 sandbox 禁止 socket bind（`PermissionError: Operation not permitted`）報 ERROR——這是既有沙箱限制，非本次改動造成的 regression（該檔案本身未被改動）
- 停用沙箱重跑：**341 passed, 0 failed**（與 generator 自報一致）
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/prompts.py:23` / `:28`**：「本週」這個時間詞同時出現在兩條不同語意的規則裡——查財報行事曆時「本週」代表未來視角（date_from=今天, date_to=今天+7），查市場摘要時「本週」代表過去視角（`days_back=7`，回顧過去 7 天新聞/指數）。兩條規則都有明確的工具情境限定句（「詢問財報行事曆」vs「詢問市場摘要、大盤整體狀況」）作為區隔，理論上對 instruction-following 較強的模型不構成問題，但對 llama-3.3-70b 這類較小模型，同一詞彙在同一份 system prompt 裡對應相反的時間方向仍有被混淆呼叫參數的風險（例如使用者問「本週有什麼財報，順便看一下大盤」的複合問題）。由於本次任務範圍限定在 prompt 文字 + 字串斷言測試，無法用單元測試驗證實際模型行為，僅能建議後續觀察真實對話 log；若之後發現模型混淆，可考慮把兩處的「本週」改用更不會重疊的措辭（例如財報行事曆規則專用「未來/即將」而非沿用「本週」）。不阻塞合併。

### 🟢 Suggestions
- **`src/agent/prompts.py:35-37`**：新增的「hour 欄位 bmo/amc/dmh」說明與既有 persona 段落（`prompts.py:17`「Finnhub earnings hour 欄位代碼：bmo=盤前、amc=盤後、dmh=盤中」）語意重複。重複本身是刻意為之（在動作指令處再次強調要求，提高小模型遵循機率），不是缺陷，僅記錄供未來若要精簡 prompt 長度時參考。

### ✅ What looks good
- Diff 完全 surgical：只動了 `prompts.py` 兩處工具選擇策略段落（各 3 行），以及對應的 2 個新測試，符合「實作範圍限定在 prompts.py + 對應測試」的要求，未觸碰 `agent.py`、`tools/earnings.py` 或其他檔案。
- 指令具體到可執行的參數層級（「date_from 傳今天日期，date_to 傳今天加 7 天」），比單純說「用未來視角」更不容易被模型曲解，且與既有 `agent.py:77` 注入「今天日期是 X」的機制銜接良好。
- 新增指令未改動、未削弱既有規則（`days_back` 上限 7、市場摘要當日漲跌標註等），也未修改 `SUMMARY_SYSTEM_PROMPT_TEMPLATE`／`summary_system_prompt()` 的既有 byte-for-byte baseline 測試。
- 兩個新測試都是對新增子句做精確字串斷言（含否定情境「過去或已公布的財報」排除句），符合本次「純 prompt 文字驗收」的可測試範圍；沿用既有 `test_prompts.py` 的測試風格（無過度設計）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：
  1. （非阻塞）觀察正式環境中「本週」在財報行事曆 vs 市場摘要兩種情境下是否真的被 llama-3.3-70b 正確區分，如發現混淆再調整措辭。
