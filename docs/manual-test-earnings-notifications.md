# 手動測試計劃：財報通知 local-first 改造（feat/earnings-notifications）

驗證範圍：**地端 SQLite 的 sync / join / 失敗回報**。
本計劃**不會**發送 Telegram 訊息、**不會**呼叫 Gemini，只驗證 Finnhub API 呼叫成功寫入 SQLite、失敗時回傳失敗資訊。

## 被測變更摘要

- `earnings_calendar` 新增 `updated_at` 欄位與 `idx_earnings_date` index（含舊 DB 自動 migration）
- 新表 `earnings_notifications`（推播冪等紀錄，本計劃只驗證表存在）
- `scheduler_handler._sync_earnings_calendar()`：逐檔同步 watchlist 未來 30 天財報進 SQLite，回傳失敗的 ticker 清單
- `backend.get_watchlist_earnings_for_date(date)`：watchlist ⋈ earnings_calendar join 查詢

## 前置準備

```bash
git checkout feat/earnings-notifications
make install
set -a; source .env; set +a
```

- `.env` 中 `FINNHUB_API_KEY` 必須是有效值；`GEMINI_API_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET` 只需有值（不會實際使用）。
- 測試中「明天」的日期請動態取得：

```bash
TOMORROW=$(date -v+1d +%F 2>/dev/null || date -d '+1 day' +%F)
echo "$TOMORROW"
```

- 若 `.local/earnings-agent.sqlite3` 不存在，情境 1 的「遷移前」檢查會顯示空結果，屬正常（全新 DB 直接建立新 schema），仍需驗證「遷移後」的斷言。

## 情境 1：舊 DB schema migration

```bash
# 遷移前（舊 DB 應該沒有 updated_at 欄位）
sqlite3 .local/earnings-agent.sqlite3 "PRAGMA table_info(earnings_calendar);"

# 任何開啟 backend 的動作都會觸發 migration
PYTHONPATH=src uv run python -c "from agent.db.backend import get_backend; get_backend()"

# 遷移後
sqlite3 .local/earnings-agent.sqlite3 "PRAGMA table_info(earnings_calendar);"
sqlite3 .local/earnings-agent.sqlite3 ".tables"
sqlite3 .local/earnings-agent.sqlite3 "SELECT name FROM sqlite_master WHERE type='index';"
```

**預期結果**：

- [ ] `earnings_calendar` 有 `updated_at` 欄位
- [ ] 存在 `earnings_notifications` 表
- [ ] 存在 `idx_earnings_date` index
- [ ] 既有資料未遺失（舊 row 的 `updated_at` 為 NULL）

## 情境 2：Call API 成功 → 寫入 SQLite 並回傳結果

```bash
PYTHONPATH=src uv run python <<'EOF'
from agent.scheduler_handler import _sync_earnings_calendar
failed = _sync_earnings_calendar()
print("failed tickers:", failed)
EOF
```

執行約 30 秒（watchlist 29 檔逐檔查詢）。接著確認寫入：

```bash
sqlite3 .local/earnings-agent.sqlite3 \
  "SELECT symbol, earning_date, extended_hour, updated_at FROM earnings_calendar
   WHERE updated_at IS NOT NULL ORDER BY earning_date LIMIT 20;"
```

**預期結果**：

- [ ] 印出 `failed tickers: []`
- [ ] SQLite 有未來 30 天內的財報資料，`updated_at` 為剛才執行的 UTC 時間（財報淡季可能只有少數幾檔，屬正常）

## 情境 3：Join 查詢回傳正確結果

先塞一筆明天的假資料保證有結果：

```bash
sqlite3 .local/earnings-agent.sqlite3 \
  "INSERT OR REPLACE INTO earnings_calendar VALUES ('AAPL', '$TOMORROW', 'amc', 1.42, 82000000000, NULL);"
sqlite3 .local/earnings-agent.sqlite3 \
  "INSERT OR REPLACE INTO earnings_calendar VALUES ('ZZZZ', '$TOMORROW', 'bmo', 1.0, 1000000, NULL);"

PYTHONPATH=src uv run python <<EOF
from agent.db.backend import get_backend
rows = get_backend().get_watchlist_earnings_for_date("$TOMORROW")
for r in rows:
    print(r)
EOF
```

**預期結果**：

- [ ] 印出 AAPL 那筆，且 `exchange` 為 `NASDAQGS`（來自 watchlist join）
- [ ] `ZZZZ` **不出現**（不在 watchlist，join 排除）
- [ ] 若明天真有其他 watchlist 公司財報，也會一併列出，依 watchlist position 排序

## 情境 4：Call API 失敗 → 回傳失敗資訊、不污染快取

```bash
sqlite3 .local/earnings-agent.sqlite3 "SELECT COUNT(*) FROM earnings_calendar;"   # 記下筆數

FINNHUB_API_KEY=invalid PYTHONPATH=src uv run python <<'EOF'
from agent.scheduler_handler import _sync_earnings_calendar
failed = _sync_earnings_calendar()
print(f"failed tickers ({len(failed)}):", failed)
EOF

sqlite3 .local/earnings-agent.sqlite3 "SELECT COUNT(*) FROM earnings_calendar;"   # 應與上面相同
```

**預期結果**：

- [ ] 每檔印出 `earnings sync failed for XXX` 的 exception log（HTTP 401）
- [ ] 最後印出 `failed tickers (29): ['AAOI', 'AAPL', ...]`，共 29 檔全部列出
- [ ] `earnings_calendar` 筆數不變（失敗不寫入、既有快取保留）

## 情境 5（選測）：REST endpoint 驗證 API 成功/失敗回應

```bash
make dev   # 另開 terminal 執行
curl "http://localhost:8080/earnings?date_from=$(date +%F)&date_to=$TOMORROW&symbol=AAPL"
```

**預期結果**：

- [ ] 回傳 `{"earningsCalendar": [...]}`（範圍內沒有 AAPL 財報時為空陣列，屬正常）
- [ ] 將 `.env` 的 `FINNHUB_API_KEY` 改為無效值並重啟 server 後，同一請求回 `502 {"detail": "Finnhub upstream error"}`（測完改回）

## 範圍外（本計劃不測）

Telegram 推播、冪等（重跑不重發）、Finnhub 失敗時的 ⚠️ 警告訊息組裝、Gemini 摘要——這些留待完整 E2E 推播測試。

## 測試結果紀錄

| 情境 | 結果（pass / fail） | 備註 |
|------|--------------------|------|
| 1. Schema migration | | |
| 2. Sync 成功寫入 | | |
| 3. Join 查詢 | | |
| 4. Sync 失敗回報 | | |
| 5. REST endpoint（選測） | | |

## 測試通過後

功能已隨 PR #9 併入 `main`，請直接在 `main` 上執行本計劃。
情境 1–4 全部 pass（情境 5 為選測）即完成驗收；
有任何 fail，將結果表回填後回報處理。
