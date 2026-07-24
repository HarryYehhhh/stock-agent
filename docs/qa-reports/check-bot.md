# QA Report: check-bot

- **Contract**: 無正式 contract，對照使用者需求驗收（見 harness `feature` 流程訊息）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | 狀態 | 備註 |
|---|---|---|
| 1. 本地 server（連線失敗 FAIL，任何 HTTP 狀態碼含 404/405 算 PASS） | ✅ | `classify_server_probe` + `probe_local_server`：`httpx.RequestError` → FAIL；其餘任何 status code → PASS。單元測試涵蓋 200/404/405/connect-error 四種情境 |
| 2. ngrok tunnel（抓 :4040 對外 https public_url，指向 :8080 那條；抓不到 FAIL） | ✅ | `find_ngrok_public_url` 以 `target_addr="localhost:8080"` 篩選且要求 `https://` 開頭；找不到、連線失敗、API 回應異常皆回 FAIL。單元測試涵蓋多 tunnel 選對、忽略其他 port、無 tunnel、缺 `config` 不 crash 四種情境 |
| 3. Telegram webhook（url 不符 WARN；last_error WARN；pending_update_count>0 WARN） | ✅ | `webhook_url_matches` 正規化尾斜線後比對 `tunnel+"/webhook"`；`webhook_info_warnings` 分別檢查 `pending_update_count` 與 `last_error_date/message`；三種狀況皆標為 WARN 而非 FAIL，符合需求描述。缺 token 或 API 呼叫失敗才標 FAIL。單元測試涵蓋 clean/pending/last_error/both 四種組合 |
| 4. 金鑰存在且非空非 placeholder，輸出遮蔽實際值，缺失 FAIL | ✅ | `is_placeholder`（None/空字串/`your_`/`xxx`/`changeme`）+ `mask_key_status` 只回傳 `"set (len=N)"` 或 `"EMPTY/PLACEHOLDER"`，從不回傳原值。已用 `dangerouslyDisableSandbox` 實跑 `make check-bot`，四把金鑰輸出均為 `len=N` 格式，未見原值 |
| 5. Groq 用 `GROQ_MODEL`（預設 `llama-3.3-70b-versatile`）發最小 chat 請求，失敗 FAIL 顯示錯誤 | ✅ | `probe_groq` 用 `max_tokens=1` 的最小請求；例外訊息截斷至 200 字元。已用假的 `GROQ_API_KEY` 實測 401 情境，錯誤訊息 `AuthenticationError: Error code: 401 - {...'Invalid API Key'...}` 未包含金鑰原值 |

## 實作約束 checklist
| 約束 | 狀態 | 備註 |
|---|---|---|
| target 名 `check-bot` 且進 `.PHONY` | ✅ | `Makefile` diff 確認新增於 `.PHONY` 清單與獨立 target |
| 純判斷邏輯與 I/O 探測分離 | ✅ | `check_bot.py` 明確以註解區隔「Pure decision logic」與「Network probes」兩區塊，純函式無 import httpx/groq 呼叫 |
| 所有網路探測設 timeout | ✅ | `HTTP_TIMEOUT = 10.0` 套用於 `probe_local_server`、`probe_ngrok`、`probe_telegram_webhook`（httpx）與 `probe_groq`（`Groq(..., timeout=HTTP_TIMEOUT)`） |
| 每項失敗不中斷後續（不可 `set -e` 提早結束） | ✅ | Python 端 `run_checks()` 對五個檢查各自包一層 `try/except`（各 probe 內部也已有自己的 try/except，雙層防護）；bash wrapper `scripts/check-bot.sh` 無 `set -e`（只有 `set -a ... set +a` 用於載入 env） |
| exit code：有 FAIL 非 0、全 PASS(WARN 可接受) 為 0 | ✅ | `exit_code()` 邏輯正確；實測（見下）全 PASS + 1 WARN 情境 exit 0 |
| 絕不印出秘密原值 | ✅ | 見下方安全審查 |
| 絕不寫 DB 或發訊息、唯讀 | ✅ | 通篇僅 4 個 GET/POST 讀取型呼叫（localhost、ngrok API、Telegram `getWebhookInfo`、Groq `chat.completions.create`），無任何 DB backend import、無 `send_message` 呼叫 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：**188 passed**（沙盒內因 socket bind 權限限制對 `test_local_integration.py` 有 12 個 ERROR，與本次改動無關；用 `dangerouslyDisableSandbox` 重跑後 188 passed, 0 failed, 0 error）
- 失敗測試：無
- 既有 suite regression：✅（非本次改動的測試全數維持通過）
- 新增測試：`tests/test_check_bot.py` 共 30 個，全部針對純函式（`classify_server_probe`／`find_ngrok_public_url`／`webhook_url_matches`／`is_placeholder`／`mask_key_status`／`webhook_info_warnings`／`format_epoch`／`summarize`／`exit_code`），未觸網路，符合檔案開頭註解宣稱的測試範圍

### 實機 smoke test（`dangerouslyDisableSandbox`，真實 `.env`）
```
[PASS] 本地 server (localhost:8080)
[PASS] ngrok tunnel
[WARN] Telegram webhook   -- webhook URL 一致，但有近期 last_error（503）
[PASS] 金鑰                -- 四把金鑰皆 "set (len=N)"，無原值
[PASS] Groq API
總結：0 項 FAIL、1 項 WARN。
EXIT=0
```
行為與需求描述一致：webhook 有 `last_error` 時標 WARN 不中斷、整體 exit 0（因無 FAIL）。

## 安全審查（金鑰／token 洩漏，硬性要求）
逐一檢查所有輸出路徑：
- `mask_key_status`：只回傳長度與布林狀態文字，不回傳原值 → 已用單元測試 `test_real_value_is_pass_and_length_only` 明確斷言原值字串不在輸出中。
- `check_secrets`：逐把 key 只印 `mask_key_status` 的回傳字串，不直接印 `os.environ.get(key)`。
- `probe_telegram_webhook`：印出的是 `registered_url`（我們自己註冊的 webhook URL，即 `tunnel_url + "/webhook"`），**不包含** bot token（token 只用於組出 `TELEGRAM_API}/bot{token}/getWebhookInfo` 的請求 URL，該字串從未被印出或存進 `CheckResult.lines`）。已核對程式碼確認 `token` 變數只在 `httpx.get(...)` 呼叫中出現一次，其餘皆用 `registered_url`／`tunnel_url`。
- `probe_groq`：例外訊息截斷至 200 字元後印出。已實測餵入假金鑰觸發真實 401 錯誤，Groq SDK 回傳的 `AuthenticationError` 內容為伺服端錯誤訊息（`Invalid API Key`），**不包含**送出的 API key 值。
- 全域 grep 確認 `check_bot.py` 中沒有任何 `print`／`CheckResult` 建構直接引用 `TELEGRAM_BOT_TOKEN`、`GROQ_API_KEY`、`FINNHUB_API_KEY`、`TELEGRAM_WEBHOOK_SECRET` 原始環境變數值。

**結論：未發現金鑰／token 洩漏路徑。**

## `.env` 版控確認
generator 為 smoke test 在 worktree 根目錄放了一份含真實金鑰的 `.env`。已確認：
- `git status --porcelain` 未列出 `.env`（不在 tracked 也不在 untracked 顯示中）
- `git check-ignore -v .env` → 命中 `.gitignore:1:.env`，確認被忽略規則覆蓋
未進版控，清理交由主 Claude 處理，此處不做任何檔案異動。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- `src/agent/check_bot.py:227`：`probe_groq` 例外訊息用 `str(e)[:200]` 截斷後直接印出，目前實測 Groq SDK 錯誤不含金鑰值，風險低；但若未來 SDK 版本的例外訊息格式改變（例如某些 HTTP client 例外會回顯 request headers），截斷字串仍可能意外帶出敏感內容。可考慮之後視需要用 regex 過濾請求中出現的金鑰字串再印出，但目前不構成阻塞問題，僅供留意。
- `tests/test_check_bot.py`：測試檔頭註解已明確說明 I/O probes（`probe_local_server`／`probe_ngrok`／`probe_telegram_webhook`／`probe_groq`）不在單元測試範圍內、改用 `make check-bot` 手動驗證，此設計與「純判斷邏輯與 I/O 探測分離」的約束一致，本次已用實跑方式補足對這四個函式的行為驗證（見上方 smoke test 與安全審查），不需額外動作。

### ✅ What looks good
- 五項檢查逐一對應需求描述的分類規則（PASS/FAIL/WARN 條件）幾乎逐字對應，无過度延伸或縮水。
- 純函式與 I/O 探測的分離做得乾淨，30 個新測試全部針對可預測、無副作用的純函式，執行快（不碰網路）。
- `run_checks()` 的雙層 try/except（各 probe 內部 + 呼叫端）確保任何未預期例外都不會讓後續檢查跳過，滿足「不中斷」的硬性要求。
- `scripts/check-bot.sh` 沿用既有 `scripts/run-daily.sh` 的 `.env` 載入慣例（`set -a; source .env; set +a`），風格與專案一致，且未使用 `set -e`。
- Makefile 改動範圍精簡（只新增 1 個 target + `.PHONY` 更新），未動到任何既有 target。
- 依賴（`httpx`、`groq`）皆已在 `pyproject.toml` 中存在，未新增依賴。
- 命名與既有 codebase 風格一致（中文訊息、`CheckResult` dataclass 簡潔、`PASS`/`FAIL`/`WARN` 常數清楚）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：
  1. （無阻塞項）可選：留意 🟢 建議中提到的 Groq 例外訊息截斷的長期風險，非本次必須處理。
  2. 主 Claude 後續清理 worktree 根目錄的 smoke-test `.env`（已確認未進版控，不影響本次合併判定）。
