# 本地每日自動排程（launchd）

本地有兩個獨立的 `launchd` 排程，分工如下（ADR-0005：DynamoDB 為 watchlist /
subscribers 的 source of truth）：

- **00:00（台北午夜）下行**：把 DynamoDB `watchlist` + `subscribers` 完整鏡射回本地
  sqlite，讓本地反映隔夜雲端 Telegram `/watch` `/unwatch` `/subscribe`
  `/unsubscribe` 的最新狀態。
- **10:30（台北）每日 pipeline**：用剛下行更新的本地 watchlist 抓財報、推播提醒、
  產生每日摘要，最後把本地產生的快取（不含 watchlist/subscribers）上傳回
  DynamoDB。

兩者相隔 10.5 小時、互不衝突；00:00～10:30 之間雲端的改動要到隔天 00:00 才會
反映到本地（每日粒度，非即時同步）。

---

## 00:00 下行排程（DynamoDB → 本地 watchlist / subscribers）

```bash
make sync-from-dynamodb
```

完整覆蓋本地 `watchlist`、`subscribers` 兩張表：先把 DynamoDB 兩表全量 scan 進
記憶體，全部成功後才在單一 sqlite transaction 內覆蓋本地兩表並 commit；雲端刪掉
的本地也會跟著刪除（嚴格鏡射）。任何一步失敗都不會留下半空表，見
`docs/decisions/0005-dynamodb-source-of-truth-downsync.md`。

範本檔案：`scripts/com.earnings-agent.downsync.plist`（呼叫
`scripts/run-downsync.sh`，先 `source .env` 載入 `LOCAL_STORE_PATH` 等變數，再跑
`make sync-from-dynamodb`；下行主要靠 `~/.aws` credentials）。

### 安裝

> **`launchctl load` 需使用者本人執行**，以下步驟不會由 agent 自動跑。

1. 建立 log 目錄（首次安裝需要）：

   ```bash
   mkdir -p /Users/yehhungwei/stock-agent/.local/logs
   ```

2. 把 plist 複製到 `~/Library/LaunchAgents/`：

   ```bash
   cp /Users/yehhungwei/stock-agent/scripts/com.earnings-agent.downsync.plist ~/Library/LaunchAgents/
   ```

3. 載入排程：

   ```bash
   launchctl load ~/Library/LaunchAgents/com.earnings-agent.downsync.plist
   ```

4. 確認已載入：

   ```bash
   launchctl list | grep com.earnings-agent.downsync
   ```

### 解除 / 重新載入

```bash
launchctl unload ~/Library/LaunchAgents/com.earnings-agent.downsync.plist
# 改動 plist 後要重新 load 才會生效
launchctl load ~/Library/LaunchAgents/com.earnings-agent.downsync.plist
```

### 看 log

```bash
tail -f /Users/yehhungwei/stock-agent/.local/logs/downsync.log
```

### 手動觸發測試

`RunAtLoad` 設為 `false`，`load` 當下不會自動跑一次，需要手動觸發：

```bash
launchctl start com.earnings-agent.downsync
```

跑完後檢查 `.local/logs/downsync.log` 確認 `sync-from-dynamodb` 有正常輸出、沒有
例外。

---

## 10:30 每日排程（財報 sync / 推播 / 上行快取）

用 macOS `launchd` 每天自動跑一次：

```bash
make sync-earnings && make notify-earnings && make daily-summary && make sync-dynamodb
```

依序完成：同步 watchlist 財報快取 → 推播明日財報提醒 → 產生並推播每日科技股摘要 →
把本地每日 pipeline 產生的快取（earnings_calendar / company_summaries / news）
同步上傳到 DynamoDB。`watchlist` / `subscribers` 是 Telegram 輸入、DynamoDB 為
source of truth，不會被這一步上傳（見上方 00:00 下行排程）。

launchd 不會載入使用者的 shell profile（`.zshrc`/`.zprofile`），所以 `.env` 裡的
`FINNHUB_API_KEY`、`GROQ_API_KEY` 等變數不會自動出現在排程的執行環境中。因此
plist 實際執行的是 wrapper script `scripts/run-daily.sh`，它會先 `source .env`
把變數載入，再依序執行上述四個 make target。

範本檔案：`scripts/com.earnings-agent.daily.plist`（呼叫 `scripts/run-daily.sh`）。

> **已安裝過舊版 plist 的人請注意**：plist 的 `ProgramArguments` 已變更為呼叫
> `scripts/run-daily.sh`，需要重新載入排程才會生效：
>
> ```bash
> launchctl unload ~/Library/LaunchAgents/com.earnings-agent.daily.plist
> cp /Users/yehhungwei/stock-agent/scripts/com.earnings-agent.daily.plist ~/Library/LaunchAgents/
> launchctl load ~/Library/LaunchAgents/com.earnings-agent.daily.plist
> ```

### 安裝

> **`launchctl load` 需使用者本人執行**，以下步驟不會由 agent 自動跑。

1. 建立 log 目錄（首次安裝需要，`launchd` 不會自動建立 `StandardOutPath`／`StandardErrorPath` 的父目錄）：

   ```bash
   mkdir -p /Users/yehhungwei/stock-agent/.local/logs
   ```

2. 把 plist 複製到 `~/Library/LaunchAgents/`：

   ```bash
   cp /Users/yehhungwei/stock-agent/scripts/com.earnings-agent.daily.plist ~/Library/LaunchAgents/
   ```

3. 載入排程：

   ```bash
   launchctl load ~/Library/LaunchAgents/com.earnings-agent.daily.plist
   ```

4. 確認已載入：

   ```bash
   launchctl list | grep com.earnings-agent.daily
   ```

### 解除排程

```bash
launchctl unload ~/Library/LaunchAgents/com.earnings-agent.daily.plist
```

如果只是暫停但不移除，`unload` 之後 plist 檔案可以留在 `~/Library/LaunchAgents/`，之後要恢復再重新 `load` 一次即可。若要完全移除，`unload` 後再刪除該檔案。

### 改時間

預設觸發時間是台北時區早上 10:30（plist 內建假設本機時區為 Asia/Taipei，`StartCalendarInterval` 用的是系統本地時間，不是 UTC）。要改時間：

1. 編輯 `~/Library/LaunchAgents/com.earnings-agent.daily.plist`（或先改 repo 內的範本 `scripts/com.earnings-agent.daily.plist` 再重新複製一份過去）
2. 調整 `StartCalendarInterval` 底下的 `Hour` / `Minute`（24 小時制），例如改成 7:30：

   ```xml
   <key>StartCalendarInterval</key>
   <dict>
       <key>Hour</key>
       <integer>7</integer>
       <key>Minute</key>
       <integer>30</integer>
   </dict>
   ```

3. 套用變更需要先 `unload` 再 `load`：

   ```bash
   launchctl unload ~/Library/LaunchAgents/com.earnings-agent.daily.plist
   launchctl load ~/Library/LaunchAgents/com.earnings-agent.daily.plist
   ```

### 看 log

stdout／stderr 都導到同一個檔案：

```bash
tail -f /Users/yehhungwei/stock-agent/.local/logs/daily.log
```

### 手動觸發測試

不用等到排定時間，可以立即觸發一次跑跑看（`RunAtLoad` 設為 `false`，所以 `load` 當下不會自動跑一次，需要手動觸發）：

```bash
launchctl start com.earnings-agent.daily
```

跑完後檢查 `.local/logs/daily.log` 確認四個步驟（sync-earnings、notify-earnings、daily-summary、sync-dynamodb）都有正常輸出、沒有例外。

---

## 已知限制（兩個排程共通）

- plist 內的路徑（`WorkingDirectory`、log 路徑、`PATH` 裡的 `uv` 路徑、wrapper script 的絕對路徑）是寫死的絕對路徑，對應這台機器的使用者帳號與 `uv` 安裝位置；換機器或換帳號要同步修改（含 `scripts/run-daily.sh` / `scripts/run-downsync.sh` 內的 `cd` 路徑）。
- `scripts/run-daily.sh`、`scripts/run-downsync.sh` 皆依賴專案根目錄下有 `.env` 檔案；若 `.env` 不存在，script 會直接以非 0 狀態退出並在 log 留下錯誤訊息，不會執行任何 make target。
- `sync-dynamodb`（10:30 上行）與 `sync-from-dynamodb`（00:00 下行）都需要有效的 AWS credentials（沿用 `~/.aws` 設定，不是 `.env` 裡的 Finnhub/Groq/Telegram 變數）；沒有設定好的話該步驟會失敗。
- 沒有 retry：某天失敗就等下一次排程時間再試；本階段不做告警，只留 log，需要自己定期看 log 確認排程有正常跑。
- 00:00 下行採嚴格鏡射：雲端刪除（`/unwatch`、`/unsubscribe`）會直接傳播到本地，無保護網；10:30 上行則完全不會把本地 watchlist/subscribers 寫回雲端。
- 兩排程為每日粒度、非即時同步：00:00～10:30 之間雲端的改動要等到隔天 00:00 才會反映到本地。
