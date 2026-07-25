# QA Report: prompts-module

- **Contract**: 無正式 contract（feature，純重構）；本次以主 Claude 傳入的使用者需求為驗收依據。
- **Date**: 2026-07-25
- **Branch**: `feat/prompts-module`
- **Verdict**: ✅ Pass

## Contract checklist（對照使用者需求）

| Deliverable | Status | Note |
|---|---|---|
| 建 `src/agent/prompts.py` 統一管理三個 prompt 常數 | ✅ | `SYSTEM_INSTRUCTION` / `SUMMARY_SYSTEM_PROMPT` / `COMPANY_PROMPT_TEMPLATE` 三者皆在 `src/agent/prompts.py`。 |
| 純搬移、內容一字不改 | ✅ | 獨立用 `git show HEAD:<原檔>` 抽出三個常數原始字串（未信任 generator 自查），以 `ast.literal_eval` 還原後與 `prompts.py` 現值逐一 `==` 比對，三者皆 `True`（完全一致，含空白、換行、標點）。 |
| 原檔改 import，行為零變化 | ✅ | `agent.py`／`market_summary.py`／`scheduler_handler.py` 的 diff 僅「刪定義區塊 + 加一行 `from agent.prompts import ...`」，無其他改動；三個常數的實際使用點（`agent.py:77` f-string 組裝 `user_content`、`market_summary.py`、`scheduler_handler.py` 的 `.format(...)` 呼叫）逐一確認皆未被觸碰。 |
| 排除：processor_handler 訊息模板 / user_content inline f-string / 分段組裝 / 外部檔案化 | ✅ | `git diff HEAD -- src/agent/processor_handler.py` 為空（0 行變動）；`agent.py:77` 的 `f"今天日期是 {date.today().isoformat()}。\n{SYSTEM_INSTRUCTION}"` 組裝邏輯原封不動；`prompts.py` 是純 Python 常數模組，非外部檔案化（如 `.txt`/`.yaml`）。 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：274 passed / 12 errors（`tests/test_local_integration.py` 因 sandbox 禁止 socket bind 造成 `PermissionError`，為此 repo 既有已知限制，CHANGELOG 歷史條目皆同此模式，非本次改動造成的回歸）
- 非沙盒重跑結果：**286 passed, 0 failed**（與 generator 自報「基線 285 → 搬移後 286」一致，新增的 1 個測試為 `tests/test_prompts.py::test_prompt_constants_are_nonempty_strings`）
- 失敗測試：無
- 既有 suite regression：✅ 無（`test_agent.py` 對 `agent_module.SYSTEM_INSTRUCTION` 的既有斷言透過 `from agent.prompts import SYSTEM_INSTRUCTION` re-export 機制沿用，未修改，測試仍全綠）

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
無明顯可挑剔之處。`prompts.py` 的 docstring 說明「零依賴、避免循環引入」，寫得比範圍要求更清楚，屬合理的自我文件化，非過度工程。

### ✅ What looks good
- 三個 prompt 常數逐字元比對（`git show HEAD:` 獨立抽取 vs. 現值）完全一致，證明是真正的「純搬移」而非重寫。
- `prompts.py` 除 `from __future__ import annotations` 外零 import，符合「不 import agent 層任何東西」要求，未來被其他模組 import 也不會有循環引入風險。
- 三個原檔的 diff 極度手術式：`agent.py` -26/+1（含 import）、`market_summary.py` -16/+1、`scheduler_handler.py` -9/+1，刪除區塊恰好對應原常數定義且無其他行被動到；blank line 間距（PEP8 頂層定義間兩行空白）在刪除後仍正確維持。
- import 排序符合專案慣例：三檔皆將 `from agent.prompts import ...` 依字母序插入既有 `from agent.*` import 區塊中正確位置（`agent.prompts` 排在 `agent.config`/`agent.db.*` 之後、`agent.telegram_client`/`agent.tools*` 之前）。
- 排除範圍精準遵守：`processor_handler.py` 完全未觸碰，`agent.py:77` 的 user_content f-string 組裝邏輯保留原樣，未被誤搬入 `prompts.py`。
- `tests/test_prompts.py` 精簡，只斷言三常數為非空字串，對應「純搬移、無新行為」的驗收性質，未過度測試字串內容細節（重複於獨立比對步驟）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（此次為零 finding 的乾淨重構，無需回修）
