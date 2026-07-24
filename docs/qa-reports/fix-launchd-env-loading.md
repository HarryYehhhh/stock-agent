# QA Report: fix-launchd-env-loading

- **Contract**: 無正式 contract（bug fix，直接對照原始需求驗收）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 新增 `scripts/run-daily.sh`：cd 專案目錄 → 檢查並 source `.env` → 依序三個 make target | ✅ | `scripts/run-daily.sh:10-21`：`cd /Users/yehhungwei/stock-agent` → `if [ ! -f .env ]` 檢查 → `set -a; source .env; set +a` → `make sync-earnings && make notify-earnings && make daily-summary` |
| 任一步失敗以非 0 退出 | ✅ | 頂部 `set -e`（cd/檔案檢查/source 任一失敗即中止）；`&&` 串接的三個 make target 為腳本最後一行，其非 0 exit code 會直接成為腳本的 exit code。實測：模擬 `.env` 缺失情境 exit 1（見下方驗證） |
| 腳本可執行權限 | ✅ | `ls -l` 確認 `-rwxr-xr-x`（551 bytes） |
| plist `ProgramArguments` 改為執行該 script，其餘設定不動 | ✅ | `scripts/com.earnings-agent.daily.plist:20-24` 改為 `/bin/bash` + 腳本絕對路徑；`WorkingDirectory`（`/Users/yehhungwei/stock-agent`）、`EnvironmentVariables/PATH`（含 `~/.local/bin`）、`StartCalendarInterval`（Hour 8/Minute 0，台北時區）、`StandardOutPath`/`StandardErrorPath`（`.local/logs/daily.log`）、`RunAtLoad`（`false`）皆與既有版本（`docs/qa-reports/phase1-local-daily-schedule.md` 記載值）逐項比對一致，未被觸動 |
| `docs/local-daily-schedule.md` 更新，說明改動並提醒已安裝者需 unload/load | ✅ | 第 11-14 行說明 root cause（launchd 不載入 shell profile → `.env` 變數不會出現在排程環境）；第 18-25 行有明顯的 `> **已安裝過舊版 plist 的人請注意**` 提示框，含完整 `unload → cp → load` 三步指令 |
| 已知限制章節更新 | ✅ | 第 107-112 行新增「`.env` 不存在則腳本以非 0 退出、不執行任何 make target」、維持既有「`&&` 串接失敗擋後續步驟」「無 retry」等既有揭露 |
| 手術式修改 | ✅ | 僅涉及 `scripts/run-daily.sh`（新增）、`scripts/com.earnings-agent.daily.plist`（`ProgramArguments` 區塊）、`docs/local-daily-schedule.md`。working tree 內另有 `Makefile`/`src/agent/market_summary.py`/`src/agent/local_server.py`/相關測試的未提交修改，經核對均為先前 phase（`phase0-webhook-switch`、`phase1-local-daily-schedule`、`fix-local-webhook-500`、`fix-local-webhook-sqs-500`，各自已有獨立 QA report）遺留、與本次修復無關，本次 diff 未再觸碰 |

## 驗證結果（自行重跑）

- `bash -n scripts/run-daily.sh` → 語法正確
- `plutil -lint scripts/com.earnings-agent.daily.plist` → OK
- `ls -l scripts/run-daily.sh` → `-rwxr-xr-x`
- `.env` 第 1-15 行檢查 → 皆為乾淨的 `KEY=VALUE` 行，無雜字，`source` 不會噴錯
- `env -i HOME="$HOME" PATH="/usr/bin:/bin" <script 副本，末行 make 改成 echo 三把 key + make -n>` 模擬 launchd 最小環境：
  - `FINNHUB_API_KEY set: yes` / `GROQ_API_KEY set: yes` / `TELEGRAM_BOT_TOKEN set: yes`
  - `make -n` 依序印出 `... agent.scheduler_handler sync` / `... agent.scheduler_handler`（notify-earnings 無參數）/ `... agent.market_summary`，三步組成正確
  - exit code 0
- 同一 wrapper 但把 `cd` 指向不含 `.env` 的暫存目錄 → 印出 `.env not found ... — aborting` 並以 exit code 1 中止，未執行任何 make target
- `PYTHONPATH=src uv run pytest tests/ -q`：沙盒內 130 passed / 12 errors（既有 `tests/test_local_integration.py` socket bind 沙盒限制，與本次改動無關，錯誤清單與既有 QA report 一致）；非沙盒重跑 **142 passed, 0 failed**
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`scripts/run-daily.sh:8`**：只用 `set -e`，未加 `set -u`/`set -o pipefail`。經檢視腳本內容（無管線、無變數展開依賴未設值防呆），這個健壯性程度剛好對應腳本的實際複雜度——`set -e` 已足以讓 `cd` 失敗、`.env` 缺失（明確 `exit 1`）、`source` 語法錯誤等狀況正確以非 0 退出，未過度設計也未不足，符合 Simplicity First。
- **`scripts/run-daily.sh:13`**：`.env not found in /Users/yehhungwei/stock-agent — aborting` 訊息中的路徑是字面字串而非 `$(pwd)`，若未來要換機器需同步改這裡與 `cd` 那行的路徑（`docs/local-daily-schedule.md:109` 已在「已知限制」揭露此類路徑寫死問題，涵蓋此點），不要求修改。
- plist 內路徑、`PATH` 裡的 `uv`/`.local/bin` 位置維持既有的寫死本機絕對路徑（Phase 1 已定案的階段性設計），本次修復未擴大也未縮小此既有已知限制範圍。

### ✅ What looks good
- root cause 分析（launchd 不 source shell profile）與修法（wrapper script 顯式 `set -a; source .env; set +a`）完全對應，且在 script 註解、plist XML 註解、`docs/local-daily-schedule.md` 三處一致重述，不會出現「文件與程式碼各說各話」的情況。
- `.env` 缺失情境用明確的 `if [ ! -f .env ]` 檢查搭配清楚的錯誤訊息與 `exit 1`，比單純依賴 `source .env` 失敗後 `set -e` 隱式中止更利於 log 排查（訊息會直接說明是 `.env` 缺失，而非籠統的 shell 錯誤）。
- `docs/local-daily-schedule.md` 的「已安裝過舊版 plist 的人請注意」提示框位置醒目（緊接在說明段落後、安裝步驟前），三行指令（unload → cp → load）與「安裝」章節的既有步驟用詞、路徑完全一致，不會讓已安裝使用者疑惑該用哪個指令。
- plist `ProgramArguments` 改動精準：只換成 `/bin/bash` + 腳本路徑兩個 array 元素，`EnvironmentVariables`/`StartCalendarInterval`/log 路徑/`RunAtLoad` 均維持不動，符合「其餘設定不動」的要求，`plutil -lint` 也確認 XML 結構未破壞。
- 改動範圍精準對應 bug 本身：只新增一個 wrapper script、改 plist 兩行、更新一份文件，未觸碰任何 Python 程式碼或既有測試，非本次相關的既有未提交改動（Makefile、market_summary.py 等）皆屬其他 phase 的既有工作，未被本次修復污染。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項。
