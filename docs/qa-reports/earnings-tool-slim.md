# QA Report: earnings-tool-slim

- **Contract**: 無（feature skill，直接對照需求 + 程式碼品質）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| `EARNINGS_ROW_LIMIT` 50 → 25，`_select_rows` 日期覆蓋邏輯不動 | ✅ | diff 只改常數，`_select_rows` 函式本體零改動（逐行比對 `git diff bec27c7 -- src/agent/tools/earnings.py` 確認）；`test_select_rows_covers_every_date_and_ranks_by_revenue_desc_with_none_last` 沿用舊測試（傳入 limit=50 直接測函式，不受生產常數影響）驗證日期覆蓋 + revenue 排序 + None 排最後行為未變 |
| `EARNINGS_FIELDS` 移除 `quarter`/`year`，欄位 whitelist 註解同步更新 | ✅ | `grep -rn "quarter" src/` 只剩 earnings.py 內的註解說明，無其他 production 消費者；註解已更新為「quarter/year are omitted since they're derivable from date and have no production consumer」，準確反映改動 |
| 每列剔除 null 欄位，非 null 保留 | ✅ | `{k: it[k] for k in EARNINGS_FIELDS if it.get(k) is not None}`；`it.get(k) is not None` 語意正確——只濾掉真正的 `None`，不會誤殺合法 falsy 值（`epsEstimate=0.0`、`hour=""` 皆會保留，因為 `0.0 is not None` / `"" is not None` 均為 True）。新測試 `test_earnings_calendar_drops_null_fields_but_keeps_non_null` 直接驗證此邊界（`epsActual=1.55` 保留、`revenueActual=None` 剔除） |
| DB upsert 在缺 key 時不炸 | ✅ | SQLite backend（`db/backend.py:516-546`）與 DynamoDB backend（`db/backend.py:1039-1058`）皆用 `.get()` / `if item.get(...) is not None` 存取欄位，且 SQLite 版本先用 `if item.get("symbol") and item.get("date")` 過濾掉缺必要 key 的列，本就是 `.get()` 安全語意，不需改動；新增的端到端測試 `test_earnings_calendar_upsert_survives_null_fields_end_to_end`（moto 真實 DynamoDB）餵入全 null 的 `hour/epsEstimate/epsActual/revenueEstimate/revenueActual`，確認 `upsert_earnings_calendar` 不炸且回傳只剩 `{"symbol": "AAPL", "date": "2026-07-23"}` |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：439 passed, 13 errors（全數為 `test_local_integration.py` 的 `PermissionError: [Errno 1] Operation not permitted`，`ThreadingHTTPServer.bind` 被沙盒擋 socket bind，與本次改動無關，為已知問題）
- 停用沙盒重跑結果：**452 passed, 0 failed**（與 generator 自報一致；main 基準 450 + 新增 2 條測試 = 452）
- 失敗測試：無
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- `src/agent/tools/earnings.py:99` 的過濾邏輯 `it.get(k) is not None` 隱含假設「未來欄位若新增數值型別的合法 0/false 也會被保留」——目前所有 EARNINGS_FIELDS 都是數值/字串型別，行為正確；純記錄供未來擴充欄位時留意，不需現在處理。

### ✅ What looks good
- diff 完全 surgical：只動 `src/agent/tools/earnings.py`（3 處：註解、`EARNINGS_FIELDS` 移除兩項、`EARNINGS_ROW_LIMIT` 常數、欄位過濾條件式）+ `tests/test_tools.py`，未觸碰 `_select_rows`、`db/backend.py` 或其他無關程式碼
- 測試覆蓋精準對應需求四項驗收標準，且新增的端到端測試使用真實 moto DynamoDB backend（非 mock），確實驗證了 upsert 路徑不會因缺欄位丟例外
- 獨立 grep 確認 `quarter`/`year` 在 production code（含 prompts/schedulers/audit）無任何消費者，移除安全
- 註解更新誠實反映改動理由，沒有留下過時或誤導性描述

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次改動無待辦事項）
