# QA Report: reduce-agent-token

- **Contract**: 無 contract（feature skill，直接對照需求驗收）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| `HISTORY_LIMIT` 20→10 | ✅ | `src/agent/db/conversations.py:11` |
| 字元預算制（總量 6,000 字元上限，超過從最舊整則丟棄，不截斷單則） | ✅ | `_apply_char_budget()`，邏輯與四個測試皆驗證通過 |
| 邊界語意：最新一則本身超過 6,000 字元仍保留 | ✅ | `test_history_single_oversized_message_is_kept` 覆蓋，且手動推導迴圈邏輯確認正確 |
| `run_agent()` 歷史同時受「則數 ≤10」與「總字元 ≤6,000」雙重上限 | ✅ | `build_messages()` 先 `get_history()`（則數上限）再套 `_apply_char_budget()`（字元上限） |
| 不得影響 `processor_handler.py` 的 `get_history(chat_id, limit=2)` fast path | ✅ | 該呼叫點（`processor_handler.py:54`）直接呼叫 `get_history`，未經過 `build_messages`/`_apply_char_budget`，未受影響 |
| SYSTEM_INSTRUCTION 加一句「同一輪一次呼叫所有需要的工具」 | ✅ | `src/agent/prompts.py:16`，放在「需要即時市場資料時主動呼叫工具」之後、`get_quote` 那句之前，僅新增一句，無其他改動 |
| 字元預算制測試覆蓋（丟最舊/10 則上限/空歷史/單則超長保留） | ✅ | `tests/test_db.py` 新增 4 個測試，全部針對性覆蓋這四種情境 |
| 現有測試無 regression | ✅ | 434 passed, 0 failed |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：434 passed / 0 failed / 0 skipped
- 失敗測試：無
- 既有 suite regression：✅ 無（首次跑因沙盒限制 socket bind 導致 `test_local_integration.py` 13 個測試出現 `PermissionError`，與本次改動無關；停用沙盒後重跑，434 passed）

## Diff 範圍確認
`git diff` 對 main 顯示大量無關檔案異動（`docs/CHANGELOG.md`、`news-headline-relevance.md`、`market_summary.py`、`tools/news.py`、`test_market_summary.py`、`test_tools.py`），經查是因為此 worktree 分支（`feat/reduce-agent-token-2`）落後 main 一個 commit（`1d87d43` news headline 過濾），與本次改動無關。改對 merge-base（`b33d17e`）diff 後確認實際改動僅限三個檔案，共 50 insertions / 3 deletions，完全對應需求，無多餘改動：
- `src/agent/db/conversations.py`（+16/-2）
- `src/agent/prompts.py`（+1）
- `tests/test_db.py`（+33/-1，實際新增 4 個測試 + import 調整）

## `_apply_char_budget` 邏輯驗證

```python
def _apply_char_budget(items, budget=HISTORY_CHAR_BUDGET):
    total = sum(len(item["content"]) for item in items)
    start = 0
    while total > budget and start < len(items) - 1:
        total -= len(items[start]["content"])
        start += 1
    return items[start:]
```

手動推導以下邊界：
- **總量剛好等於 6,000**：迴圈條件 `total > budget` 為 False，全部保留。正確。
- **budget 內全保留**：同上，`start` 停在 0。
- **單則本身即超過 6,000**（items 只有一則）：`start < len(items)-1` 為 `0 < 0 = False`，迴圈不執行，直接回傳該則。正確，不會被清空。
- **多則超長，最新一則自己也超過 budget**：迴圈會一路丟到只剩最後一則為止（`start < len(items)-1` 卡住迴圈邊界），保證至少保留最新一則。與需求「最新一則本身超過 6,000 字元時仍保留該則」語意一致。
- **`get_history` 回傳順序**：確認 DynamoDB backend（`items.reverse()`）與 SQLite backend（`ORDER BY id ASC` 於內層 DESC+LIMIT 之後）皆回傳「舊→新」順序，與 `_apply_char_budget` 假設（`items[0]` 為最舊）一致。

測試 `test_history_char_budget_drops_oldest_messages` 手動驗算：4 則訊息，每則內容長度 2501 字元（`msgN-` 5 字元 + 2496 個 `x`），總量 10004 > 6000。丟 `items[0]`（2501）後總量 7503 仍 > 6000；再丟 `items[1]`（2501）後總量 5002 ≤ 6000，停止，保留 `msg2`、`msg3`。與測試斷言一致。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- `src/agent/db/conversations.py:1-4` 的模組 docstring（"History is fetched newest-first then reversed..."）在本次改動前就已存在，且描述的是 backend 層行為，與新增的字元預算邏輯無衝突，不需更動；僅供未來維護者留意 `build_messages` 現在多了一層字元預算過濾，docstring 未提及但不影響正確性。
- `chat_cli.py`（本地互動 CLI）沿用 `HISTORY_LIMIT`（現為 10）做記憶體內 slice，但未套用字元預算制；需求未涵蓋此檔案（僅針對 `run_agent()`/`build_messages` 路徑），且該檔案是本地開發工具非生產路徑，維持現狀合理，僅記錄供參考。

### ✅ What looks good
- 改動完全 surgical，三個檔案、50 行內，每一行都可對應到需求描述。
- `_apply_char_budget` 對「整則丟棄、不截斷單則」與「最新一則超長仍保留」的邊界處理正確，並有對應測試覆蓋。
- 新增的 prompt 句子精準放置在需求指定的位置（「需要即時市場資料時主動呼叫工具」附近），只加一句，未動其他 prompt 內容。
- 對 `processor_handler.py` fast path（`get_history(chat_id, limit=2)`）的獨立呼叫路徑做了明確確認，未被字元預算邏輯波及。
- 測試命名清楚、每個測試對應單一情境，符合需求列出的四個測試點。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次無待辦事項）
