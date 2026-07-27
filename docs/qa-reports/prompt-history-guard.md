# QA Report: prompt-history-guard

- **Contract**: 無正式 contract，依需求描述驗收（見下）
- **Date**: 2026-07-27
- **Verdict**: ✅ Pass

## 需求（取代 contract）
在 `src/agent/prompts.py` 的 `SYSTEM_INSTRUCTION` 加一條防「對話歷史污染」的指示，涵蓋三個語意：
1. 只答當前問題
2. 回覆依據本次工具查詢結果而非歷史數據
3. 用戶未提及的個股不主動查詢或附上

驗收標準：只動 `prompts.py` 的 `SYSTEM_INSTRUCTION` 一處、全測試過、surgical change only。

## Checklist
| 項目 | Status | Note |
|---|---|---|
| 只動 `prompts.py` 一處 | ✅ | `git diff` 確認全部變更僅 `src/agent/prompts.py`，單一 hunk、新增 3 行，無其他檔案異動 |
| 語意1：只答當前問題 | ✅ | 「只回答用戶當前提出的問題，回覆內容一律依據本次工具查詢的結果；」 |
| 語意2：依本次工具查詢結果而非歷史數據 | ✅ | 「不要引用、模仿或重複對話歷史中的舊回答與數據；」 |
| 語意3：未提及個股不主動查詢/附上 | ✅ | 「用戶當前問題未提及的個股，不要主動查詢或附上其資訊。」 |
| 插入位置合理 | ✅ | 位於財報行事曆時段標示規則之後、知識庫段落（`\n\n遇到財報術語...`）之前，不打斷任一段落既有語意 |
| 措辭風格一致 | ✅ | 句式（「不要 X，」「一律 Y」）、標點（全形頓號/分號、句尾句號）與既有段落一致，無重複既有文字（`grep "當前\|歷史"` 確認新增前檔案無同義句） |
| Surgical change only | ✅ | 未動任何測試、既有指示、註解或其他段落 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：
  - 沙箱內：329 passed / 13 errors（`tests/test_local_integration.py` 的 `ThreadingHTTPServer` 因沙箱禁止 bind local socket 而 `PermissionError`，屬環境限制非回歸——同一組 12~13 個測試在近期多份 QA report 中都有相同現象）
  - 停用沙箱重跑：**342 passed**，與 generator 自報一致
- 失敗測試：無（排除沙箱環境限制後）
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/prompts.py:40`**：「用戶當前問題未提及的個股，不要主動查詢或附上其資訊」在字面上可能被模型解讀為「當前這句話沒出現的 ticker 一律不可查」，若使用者用代名詞追問同一支股票（例如上一輪問 AAPL 財報，下一輪追問「那它的分析師預期呢？」，未重複打出 AAPL），理論上有被模型過度保守解讀而拒絕/不呼叫工具的風險。
  - 影響評估：風險可控。該句的訴求對象是「與當前主題無關的『其他』個股」（對應 production 事件：問 TSLA 卻夾帶歷史 AAPL 預期），而非「透過代名詞延續同一主題」；後者的實體仍在對話歷史與當前語境中，一般 LLM 會視為語境已提及。且既有 `SYSTEM_INSTRUCTION`／`tests/test_prompts.py` 對此類語意皆只做 substring 斷言，沒有針對真實 LLM 回覆的語意迴歸測試框架，這是 prompt 純文字驗收的固有限制（同類保留意見過去也曾出現在 2026-07-26 `earnings-week-forward` QA report，未阻塞合併）。
  - 建議修法（non-blocking，可留待下次微調）：若之後想收斂這個風險，可在句尾補一個排除語，例如「（同一支股票在對話中被追問時不受此限）」，但目前用詞尚屬合理範圍，不建議為此臨時加句造成過度工程化。
- **無對應迴歸測試**：檔案中其他多數 `SYSTEM_INSTRUCTION` 條目在近期 sprint 都有 `tests/test_prompts.py` 內的 substring 斷言保護（例如 `test_system_instruction_requires_earnings_hour_label_in_reply`），本次新增的 3 句沒有對應測試，之後若有人不慎在重構/精簡 prompt 時誤刪，不會被測試攔截。
  - 建議修法：補一條 `test_system_instruction_guards_against_conversation_history_pollution`，斷言 3 句關鍵字（例如「只回答用戶當前提出的問題」「不要引用、模仿或重複對話歷史」「未提及的個股」）都在 `SYSTEM_INSTRUCTION` 內。

### 🟢 Suggestions
- **段落歸屬**：這 3 句是通用於所有工具呼叫的防護指示，目前插在「財報行事曆時段標示規則」（相當戰術性、僅限 earnings calendar 情境）與「知識庫查詢策略」兩個具體段落之間。閱讀上可行，但若未來要再擴充同類「通用守則」，可以考慮獨立成一個小標題段落（如 `\n\n對話與回覆原則：\n`），會比夾在兩個具體工具策略段落中間更易維護。目前不影響功能，不要求本次處理。

### ✅ What looks good
- 三個語意全部到位，且分別對應 production 事件的三個面向（範圍/資料來源/未提及個股），涵蓋完整。
- Diff 精準，只有一個 hunk、3 行新增，沒有動到任何既有文字、測試或其他檔案，符合 surgical change 要求。
- 用詞與既有 `SYSTEM_INSTRUCTION` 風格（「不要...」「一律...」的祈使句、全形標點）高度一致，讀起來像是原本就存在的指示，不會有拼接感。
- 全測試綠燈（342/342），無回歸。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事（非阻塞，可留待下次 sprint 順手做）：
  1. 補一條 `tests/test_prompts.py` 斷言鎖住這 3 句關鍵字，避免未來被誤刪。
  2. 若之後觀察到多輪代名詞追問（如「那它的...」）被模型過度保守解讀而不呼叫工具，再回來微調語意加排除句；目前不需預先處理。
