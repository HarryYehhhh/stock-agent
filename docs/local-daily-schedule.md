# 本地每日自動排程（launchd）

本地 DB-first 開發期間，用 macOS `launchd` 每天自動跑一次：

```bash
make sync-earnings && make notify-earnings && make daily-summary && make sync-dynamodb
```

依序完成：同步 watchlist 財報快取 → 推播明日財報提醒 → 產生並推播每日科技股摘要 →
把本地 sqlite（watchlist / earnings_calendar / company_summaries / news）同步上傳到 DynamoDB。

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

---

## 安裝

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

## 解除排程

```bash
launchctl unload ~/Library/LaunchAgents/com.earnings-agent.daily.plist
```

如果只是暫停但不移除，`unload` 之後 plist 檔案可以留在 `~/Library/LaunchAgents/`，之後要恢復再重新 `load` 一次即可。若要完全移除，`unload` 後再刪除該檔案。

## 改時間

預設觸發時間是台北時區早上 8:00（plist 內建假設本機時區為 Asia/Taipei，`StartCalendarInterval` 用的是系統本地時間，不是 UTC）。要改時間：

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

## 看 log

stdout／stderr 都導到同一個檔案：

```bash
tail -f /Users/yehhungwei/stock-agent/.local/logs/daily.log
```

## 手動觸發測試

不用等到排定時間，可以立即觸發一次跑跑看（`RunAtLoad` 設為 `false`，所以 `load` 當下不會自動跑一次，需要手動觸發）：

```bash
launchctl start com.earnings-agent.daily
```

跑完後檢查 `.local/logs/daily.log` 確認四個步驟（sync-earnings、notify-earnings、daily-summary、sync-dynamodb）都有正常輸出、沒有例外。

## 已知限制

- plist 內的路徑（`WorkingDirectory`、log 路徑、`PATH` 裡的 `uv` 路徑、`scripts/run-daily.sh` 的絕對路徑）是寫死的絕對路徑，對應這台機器的使用者帳號與 `uv` 安裝位置；換機器或換帳號要同步修改（含 `scripts/run-daily.sh` 內的 `cd` 路徑）。
- `scripts/run-daily.sh` 依賴專案根目錄下有 `.env` 檔案；若 `.env` 不存在，script 會直接以非 0 狀態退出並在 log 留下錯誤訊息，不會執行任何 make target。
- 四個 make target 用 `&&` 串接，任一步驟失敗會讓後面的步驟不執行；本階段不做告警，只留 log，需要自己定期看 log 確認排程有正常跑。
- `sync-dynamodb` 需要有效的 AWS credentials（沿用 `~/.aws` 設定，不是 `.env` 裡的 Finnhub/Groq/Telegram 變數）；沒有設定好的話這一步會失敗，但不影響前三步已完成的結果。
- 沒有 retry：某天失敗就等下一次排程時間再試。
