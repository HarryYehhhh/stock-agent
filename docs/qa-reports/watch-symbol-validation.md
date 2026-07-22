# QA Report: watch-symbol-validation

- **依據**：本任務無 contract，對照需求描述（修正 `docs/qa-reports/per-subscriber-watchlist-sprint.md` 的 🟡 issue：`/watch`、`/unwatch` 缺 symbol 格式驗證）+ 程式碼品質
- **Branch**: feat/per-subscriber-watchlist
- **Date**: 2026-07-22
- **Verdict**: ✅ Pass

## 需求檢查清單

| 項目 | 狀態 | 備註 |
|---|---|---|
| 加 regex 驗證 symbol 格式 | ✅ | `src/agent/processor_handler.py:28` `SYMBOL_RE = re.compile(r"[A-Z]{1,10}([.\-][A-Z]{1,3})?")` |
| 拒絕特殊字元 | ✅ | 手動驗證 `$$$WEIRD!!` → `SYMBOL_RE.fullmatch()` 回 `None`；測試 `test_watch_invalid_symbol_special_chars_gives_usage` |
| 拒絕過長 | ✅ | 11 字元（超過 10 字元上限）手動驗證回 `None`；測試 `test_watch_symbol_too_long_gives_usage` |
| 拒絕多字參數 | ✅ | `"AAPL MSFT"` 手動驗證回 `None`（regex 無空白字元）；測試 `test_watch_multi_word_argument_gives_usage`。原本靠 `" " in arg` 額外判斷，現已移除並確認 regex 天然涵蓋（見下方 findings） |
| 接受 `BRK.B` / `BF-B` 型態 | ✅ | 測試 `test_watch_accepts_symbol_with_dot_and_dash`（含小寫輸入 `brk.b`/`bf-b` 正規化後通過） |
| 不合法回覆用法說明 | ✅ | `/watch`、`/unwatch` 皆用同一組 `not arg or not SYMBOL_RE.fullmatch(symbol)` 判斷式，失敗回 `WATCH_USAGE` / `UNWATCH_USAGE` |
| 補測試（含多字參數 case） | ✅ | 新增 6 條測試，涵蓋 `/watch` 特殊字元、過長、多字、合法 `.`/`-`，以及 `/unwatch` 無效 symbol |

## 測試結果
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：118 passed, 0 failed, 0 skipped
- 失敗測試：無
- 既有 suite regression：✅（含前次 sprint 的 watchlist/scheduler/processor 測試全數維持綠燈）
- 備註：sandbox 對 `~/.cache/uv` 有寫入限制（`Operation not permitted`），與 generator 說明一致；本次以非 sandbox 模式重跑並確認 118/118 通過。

## 手動驗證（regex 邊界）

任務提醒特別檢查是否用 `fullmatch`（或有 `^...$` anchor），因為只用 `match()` 會讓 `AAPL$$$` 或過長字串因 prefix-match 而漏擋。實際確認 `src/agent/processor_handler.py:90` 與 `:107` 皆使用 `SYMBOL_RE.fullmatch(symbol)`。用 Python 手動比對 `fullmatch` 與 `match` 差異：

```
'AAPL$$$'    -> fullmatch: False | match: True（若誤用 match 會漏擋）
'A'*11       -> fullmatch: False | match: True（若誤用 match 會漏擋）
''           -> fullmatch: False | match: False
'AAPL MSFT'  -> fullmatch: False | match: True（若誤用 match 會漏擋）
```

`re.fullmatch()` 本身語意即為「整個字串必須匹配」，等同於隱含 `^...$` anchor，不需要在 pattern 字串裡額外寫 `^$`。確認程式碼正確使用 `fullmatch()`，因此本次沒有重現任務描述中擔心的 anchor 漏洞。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`src/agent/processor_handler.py:90`、`:107`**：`not arg or not SYMBOL_RE.fullmatch(symbol)` 中的 `not arg` 現在是多餘判斷——空字串本來就無法通過 `SYMBOL_RE.fullmatch("")`（regex 至少需要 1 個字母），已用 Python 手動驗證 `''` → `fullmatch` 回 `None`。目前寫法無害（純粹保留舊有短路邏輯），不需要立即修，但若之後有人維護這段程式碼可以順手簡化成只留 `not SYMBOL_RE.fullmatch(symbol)`。
- 前次 QA report 的 🟢 建議 2（`/watch`／`/unwatch` 結構重複，可考慮未來抽 helper）與建議 3（`_init_schema()` 註解提醒建表順序）本次未處理，但均與本次「symbol 格式驗證」需求無關，維持原本「規模下可接受」的結論，非本次任務範圍。

### ✅ What looks good
- Regex 設計覆蓋需求列出的所有情境：特殊字元、過長（>10 字母）、多字參數皆正確拒絕；`BRK.B`、`BF-B`（含小寫輸入）正確接受並正規化為大寫。
- 正確使用 `re.fullmatch()`，避免了只用 `match()` 而缺 anchor 導致 prefix-match 誤放行的常見陷阱；已手動用具體 case 驗證。
- `/watch` 與 `/unwatch` 兩處驗證邏輯一致（同一個 `SYMBOL_RE`、同一種判斷式），沒有邏輯漂移風險。
- 新增的 6 條測試命名清楚、各自只驗證單一行為（特殊字元、過長、多字、合法格式、`/unwatch` 無效輸入），非同義反覆測試，能鎖住這次修正的行為。
- 移除舊的 `" " in arg` 檢查前已確認 regex 天然涵蓋多字參數情境（regex 字元類不含空白），沒有留下功能缺口。
- Diff 範圍精準，只動了 `/watch`、`/unwatch` 相關的驗證程式碼與對應測試，沒有動到其他不相關邏輯（surgical change）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項目；若要精益求精，可選擇性簡化 `not arg or not SYMBOL_RE.fullmatch(symbol)` 為單一條件（見 🟢 suggestion）。
