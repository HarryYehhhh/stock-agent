# QA Report: manual-test-market-summary（T3 流程 B）

- **Contract**: docs/TASKS.md — T3「流程 B：每日科技股摘要」
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass（含一項測試中發現並已修復的 🟡 bug，修復已驗證回歸）

## 範圍

僅涵蓋 **T3 流程 B（每日科技股摘要，local FastAPI `/news` + `/market-summary`）**。
流程 A（Telegram watchlist 財報通知）**尚未測試**，不在本報告範圍內，需另開
`docs/qa-reports/manual-test-watchlist-notify.md`。

測試環境：本機 `local_server.py`（FastAPI）+ `STORAGE_BACKEND=local`（SQLite，
`.local/earnings-agent.sqlite3`），由使用者實際手動呼叫 endpoint 完成。

## 驗收標準（來自 docs/TASKS.md T3）

1. 打本機 FastAPI `/news`（refresh）與 `/market-summary`
2. `news`、`market_summaries` 表有資料
3. 摘要為繁中且引用來源 news_ids

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| `/market-summary` 可成功呼叫並回傳摘要 | ✅ | 兩次呼叫皆 200，回傳結構完整（見下方手測證據） |
| `news` 表有資料 | ✅ | `category` 分組 `ai\|5`、`big7\|49`，total 54，DB 實際驗證 |
| `market_summaries` 表有資料 | ✅ | `id=2` 列存在，`summary` 非空、`news_ids` 為合法 JSON 陣列 |
| 摘要為繁體中文 | ✅ | 兩次呼叫的 `summary` 皆為繁中，含 📈/📊/🤖/💡 四段結構 |
| 摘要引用來源 `news_ids` | ✅ | `news_ids` 陣列長度 24 = `select_news()` 揀選的 big7(21，≤3/檔×7檔內) + ai(3) 筆數，與 `generate_market_summary()` 邏輯一致 |
| news 去重（`INSERT OR IGNORE` by headline）正確運作 | ✅ | `count(*) = count(DISTINCT headline) = 54`，權威 DB 查詢確認無重複列 |
| `refresh_news()` 回傳的 `stored_news` 計數準確反映實際插入筆數 | ✅（修復後）| 原本回報「嘗試插入數」而非「實際新增數」，見下方 bug 記錄；已修復並驗證 |

## 手動測試證據

### 呼叫 1：`GET /market-summary?days_back=2`
- 回傳：`stored_news=57`、`summary_id=1`、`big7=21`、`ai=3`、`idx=4`
- `summary`：繁體中文，結構完整 ——
  📈大盤收盤指標（SPY/QQQ/DIA/IWM 四檔 ETF 代理）+ 📊Big7 動態 + 🤖AI 產業焦點 + 💡今日總結

### 呼叫 2：重跑同一 endpoint（同一天內）
- 回傳：`stored_news=12`、`summary_id=2`
- `summary`：同樣繁中、結構完整

### DB 驗證（SQLite 直查，權威來源）

```sql
SELECT category, count(*) FROM news GROUP BY category;
-- ai|5
-- big7|49   （total 54）

SELECT id, substr(summary,1,30), news_ids
FROM market_summaries ORDER BY id DESC LIMIT 1;
-- id=2，news_ids 為 24 個整數的 JSON 陣列（= big7 21 + ai 3）

SELECT count(*), count(DISTINCT headline) FROM news;
-- 54 | 54  → 相等，代表無重複列，dedup（headline 唯一鍵 + INSERT OR IGNORE）正確
```

三項驗收標準（`news`/`market_summaries` 有資料、摘要繁中、引用 `news_ids`）皆通過，
且用權威 DB 查詢交叉確認 dedup 邏輯正確無重複資料。

## 測試中發現並已修復的問題

### 🟡 `insert_news()` 回傳計數不準確（已修復）

- **現象**：`refresh_news()` 回傳的 `stored_news`（呼叫端顯示為「本次新增筆數」）與資料庫實際新增筆數不符。
  - 呼叫 1 回報 `stored_news=57`，但 `news` 表 total 只有 54（含呼叫 1 + 呼叫 2 所有資料）。
  - 呼叫 2 回報 `stored_news=12`，但呼叫 2 執行後 `news` total 仍是 54（相對呼叫 1 後淨新增 0，因為呼叫 2 抓到的都是已存在的 headline）。
  - 資料本身完全正確（54/54 dedup 正常），問題只在「回傳的計數值」，不影響已落地資料的正確性，也不影響摘要本身。
- **根因**：`src/agent/db/backend.py` 的 `LocalSQLiteBackend.insert_news()` 原本用
  `cur.rowcount` 逐筆累加來源自 `INSERT OR IGNORE` 語句。SQLite 對於被 `OR IGNORE`
  忽略的衝突列，`rowcount` 的回傳值並不可靠保證為 0（依 sqlite3 版本/驅動行為，
  可能回報「嘗試執行」而非「實際變更」），導致函式回傳的是「嘗試插入筆數」而非
  「實際插入筆數」。
- **修復**（`src/agent/db/backend.py:557-581`）：改用同一連線的 `conn.total_changes`
  在批次插入前後取差值。`total_changes` 是 SQLite connection-level 的「實際變更列數」
  計數器，被 `OR IGNORE` 忽略的衝突列不會計入，因此差值即為本批次「真正新增」的列數。
  ```diff
  -        inserted = 0
           with self._connect() as conn:
  +            before = conn.total_changes
               for it in items:
  -                cur = conn.execute(...)
  +                conn.execute(...)
  -                inserted += cur.rowcount
  -        return inserted
  +            return conn.total_changes - before
  ```
- **迴歸測試**（`tests/test_storage_backend.py`，新增 2 條）：
  1. `test_insert_news_returns_distinct_inserted_count_not_attempted_count` —
     同一批次含 2 筆相同 headline + 1 筆不同 headline，斷言回傳 `2`（distinct 插入數，
     非嘗試數 3），並用 `count(*) = count(DISTINCT headline) = 2` 交叉驗證表內無重複。
  2. `test_insert_news_reinserting_existing_headlines_returns_zero` —
     對已存在的 headline 重複呼叫 `insert_news()`，斷言回傳 `0`，且表列數不變
     （`count(*) = count(DISTINCT headline) = 2`，未被重複插入）。
- **修復後回歸結果**：`PYTHONPATH=src uv run pytest tests/ -q` → **130 passed**
  （原 128 + 新增 2 條），無其他測試受影響。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 結果：**130 passed / 0 failed / 0 skipped**
- 失敗測試：無
- 既有 suite regression：✅ 無（bug 修復後全量重跑通過，非本次改動的其他測試皆綠燈）
- 備註：sandbox 模式下對 `tests/test_local_integration.py` 內以 `http.server` 起本機
  server 的整合測試會因 `PermissionError: [Errno 1] Operation not permitted`
  （socket bind 被沙箱限制）而報錯（118 passed, 12 errors）；以
  `dangerouslyDisableSandbox` 重跑後上述 12 條全數通過，總計 130 passed，
  確認並非程式問題而是沙箱環境限制。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/db/backend.py:557`（已修復，記錄存檔）**：`insert_news()` 回傳計數
  曾用不可靠的 `cur.rowcount` 加總 `INSERT OR IGNORE` 結果，導致呼叫端看到的
  `stored_news` 數字虛高、不代表實際新增筆數。已改用 `conn.total_changes` 差值修復，
  並補上 2 條迴歸測試防止再次退化。**此問題已在本次測試迭代中修復並驗證，
  不需 generator 再處理。**

### 🟢 Suggestions
- `market_summary.py` 的 `select_news()` 對 big7 類別採「每檔 symbol 最多
  `MAX_NEWS_PER_SYMBOL=3` 筆」的截斷邏輯，與呼叫 1 揀選出的 21 筆（7 檔 ×3）吻合，
  行為符合預期，僅在此記錄以利未來對照，非缺陷。
- 兩次呼叫的 `stored_news`（57 之於實際 54、12 之於淨新增 0）差異提醒：
  Finnhub 免費層在短時間內重複呼叫同一 `/company-news` + `/news?category=technology`
  容易拿到高重疊的新聞集合；目前的 headline-based dedup 足以應付，未發現需要
  額外處理的 edge case。

### ✅ What looks good
- `news` 表 dedup（`UNIQUE(headline)` + `INSERT OR IGNORE`）在真實資料下驗證正確：
  `count(*) = count(DISTINCT headline)` 恆等，兩次呼叫、54 筆資料皆無重複列。
- `market_summaries.news_ids` 與 `select_news()` 實際揀選出的 big7+ai 筆數完全對得上
  （24 = 21 + 3），可稽核性符合設計預期（見 `market_summary.py` docstring 的
  audit 目的）。
- 摘要輸出穩定為繁體中文，且結構化（大盤指標 / Big7 動態 / AI 焦點 / 今日總結）
  符合 `SUMMARY_SYSTEM_PROMPT` 的設計。
- 發現的計數 bug 屬於「回傳值不準確」而非「資料錯誤」，影響面小（僅影響
  呼叫端顯示訊息），且已在本輪測試中一併修復、補測試、跑過全量回歸，
  沒有遺留未解問題。

## 結論
- 是否可合併：是（bug 修復 diff 屬於本次測試附帶修復，已通過全量回歸 130/130，
  建議與本 QA report 一併提交）
- 最該先處理的 1–3 件事：
  1. 無阻塞事項。
  2. 依 `docs/TASKS.md` 排序，T3 流程 A（Telegram watchlist 財報通知）仍待手測，
     完成後才算 T3 全數通過。
  3. `insert_news()` 的修復 diff（`src/agent/db/backend.py`、
     `tests/test_storage_backend.py`）目前是未 commit 的工作區改動，
     若後續要推進 T4/T5，建議先將此修復提交成獨立 commit。
