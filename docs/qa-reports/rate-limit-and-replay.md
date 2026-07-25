# QA Report: rate-limit-and-replay

- **驗收模式**: feature（無 contract，對照使用者需求 + 程式碼品質）
- **Date**: 2026-07-26
- **Verdict（round 1）**: 🔴 Critical（需回送 generator）
- **Verdict（round 2，最終）**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| Per-chat_id rate limit：每分鐘 3 則、每天 15 則 | ✅ | `rate_limit.py` `MINUTE_LIMIT=3` / `DAY_LIMIT=15`，`> LIMIT` 才擋，剛好第 3/15 則仍放行，符合「每分鐘 3 則、每天 15 則」語意 |
| webhook 在 enqueue 前檢查，與 `claim_update_id` 同層短路 | ✅ | `webhook_handler.py` dedup 之後、`_sqs().send_message` 之前呼叫 `check_rate_limit`，被擋時 `return 200` 不 enqueue，結構與 dedup 一致 |
| DynamoDB fixed-window 計數器 + TTL（仿 dedup 表模式） | 🔴 | 見 Critical finding：`increment_rate_limit` 的 `UpdateExpression` 子句順序（`ADD ... SET ...`）疑似不符合 DynamoDB 官方規定順序（SET, REMOVE, ADD, DELETE），moto 測試無法抓到 |
| 走 StorageBackend Protocol，DynamoDB + LocalSQLite 雙實作 | ✅ | Protocol 新增兩個方法，兩個 backend 都有實作，簽名一致 |
| 限流時每個時間窗只回一次提示，之後同窗靜默丟棄（仍回 200） | ✅ | `mark_rate_limit_notified` 用 conditional update（DynamoDB `attribute_not_exists`）/ conditional UPDATE（SQLite `WHERE notified=0`）保證原子性，`test_rate_limited_message_is_not_enqueued_and_notifies_once` 驗證第二次超限不再呼叫 `send_message` 但仍回 200 |
| infra/template.yaml 新表定義與權限 | ✅ | `RateLimitsTable` 定義齊全（PK/SK/TTL），`WebhookFunction` 補 `DynamoDBCrudPolicy`；`ProcessorFunction`/`SchedulerFunction` 正確地**沒有**加這個表的權限（重放功能只讀 `ConversationsTable`，符合預期） |
| 上一題重放：strip 後完全相同 + 10 分鐘內 → 重發舊回覆 + 前綴提示，不呼叫 Groq | ✅ | `_find_replay` 用 `get_history(limit=2)` 取最後一組，`prev_user["content"].strip() != text` 比對（`text` 在 `_process_update` 已 strip），10 分鐘窗用 `> REPLAY_WINDOW` 判斷（含等於 10 分鐘仍算命中） |
| 只比對最後一組 user/assistant | ✅ | `get_history(chat_id, limit=2)` 限定只拿最後 2 筆；`run_agent` 失敗時不 `save_message`，所以「最後一組」永遠是最後一次成功的問答，設計合理 |
| 重放事件不寫入對話史 | ✅ | replay 分支只 `send_message`，無 `save_message` 呼叫；`test_replay_does_not_write_to_conversation_history` 驗證筆數不變 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：309 passed，12 errors（全部是既有 `tests/test_local_integration.py` 的 `ThreadingHTTPServer.socket.bind` `PermissionError: Operation not permitted`，屬沙箱網路權限限制，與本次改動無關）
- 停用沙箱重跑：**321 passed, 0 failed**（與 generator 自報一致）
- 針對性重跑 `tests/test_rate_limit.py tests/test_replay.py tests/test_storage_backend.py tests/test_webhook.py`：41 passed
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）

- **`src/agent/db/backend.py:911`**：`DynamoDBBackend.increment_rate_limit` 的 `UpdateExpression` 寫成：
  ```python
  UpdateExpression="ADD hit_count :incr SET #ttl = if_not_exists(#ttl, :ttl)",
  ```
  DynamoDB 官方文件規定：若一個 UpdateExpression 混用多種子句，**必須依 `SET, REMOVE, ADD, DELETE` 的固定順序出現**。這裡是 `ADD` 在前、`SET` 在後，屬於反序。真實 AWS DynamoDB 很可能會對此拋出 `ValidationException`（moto 的 parser 是逐 token 嘗試各子句 factory，並未驗證子句出現順序，見 `.venv/.../moto/dynamodb/parsing/expressions.py:_parse_by_a_subfactory`，所以 moto 測試全綠並不能證明真實 AWS 會接受）。
  影響範圍：`webhook_handler.py` 呼叫 `check_rate_limit()` 時**沒有包 try/except**（只有 `send_message` 那段有包），一旦 `increment_rate_limit` 在真實環境對每一則訊息都拋例外，會讓 `webhook_handler.handler` 直接對**所有訊息**丟未處理例外，等同全站中斷（Telegram 會重試但仍會撞同一個 bug）。而且這條路徑完全無法在 CI/moto 下被抓到，只有部署到真實 AWS 才會現形。
  建議修法：把子句順序改成 `SET` 在前、`ADD` 在後：
  ```python
  UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD hit_count :incr",
  ```
  語意不變（`if_not_exists` 只在首次寫入時設定 ttl，之後保留原值），但符合官方要求的子句順序。建議修完後手動用一個真實/LocalStack 等更貼近真實驗證引擎的環境跑一次 `increment_rate_limit`，確認不再是只靠 moto 過關（moto 對此 case 是偽陽性）。

## 未列入 Critical 但需注意

### 🟡 Warnings（Should Fix）

- **`src/agent/webhook_handler.py:76`**：被限流時會在 webhook handler 內同步呼叫 `send_message()`（`telegram_client.py` 的 httpx client `timeout=10.0`，且 Markdown 失敗時會整段重試一次，最壞情況接近 20s）。但 `infra/template.yaml` 裡 `WebhookFunction` 的 `Timeout: 10`，且檔案開頭 docstring 明確寫著「Stays under 1 second by offloading agent work to SQS」。這條新路徑讓 webhook Lambda 第一次真正做同步外部 HTTP call，若 Telegram API 慢，有機會被 Lambda 超時砍掉（SIGKILL，try/except 攔不到），使用者收不到限流提示、且該次呼叫可能沒有優雅結束。機率低（只有真的觸發限流時才會走到），但建議至少三選一：(a) 把這次呼叫使用較短的 client timeout（例如 3-5s），(b) 接受風險但在 docstring/註解說明這個已知取捨，(c) 把限流通知也丟一個很輕量的 fire-and-forget（例如另開一個短 timeout 的 client）。目前程式碼沒有任何註解說明這個取捨，建議至少補充說明。

### 🟢 Suggestions

- **`src/agent/db/rate_limit.py:45-51`**：分鐘窗超限時直接 `return`，不會遞增日窗計數（generator 已自陳這是設計決定）。這代表「因為狂發訊息被分鐘窗擋下的訊息」不會消耗使用者的每日 15 則額度，行為上對使用者較友善，也是合理解讀，但需求原文沒有明確定義這個邊界情況，建議之後和需求方（PO/使用者）過一次這條假設，避免日後被誤認為 bug。
- **`tests/test_replay.py`**：目前只測了「9 分鐘內會 replay」「11 分鐘外不會 replay」，沒有測剛好等於 10 分鐘的邊界（code 用 `>` 判斷，10 分鐘整仍算命中），以及沒有測「使用者訊息前後多打空白但 strip 後相同」的情境（`_find_replay` 邏輯已正確處理，只是沒有專門測試覆蓋）。非阻塞，建議之後補上讓覆蓋率更完整。

### ✅ What looks good

- `mark_rate_limit_notified` 的原子性設計正確：DynamoDB 用 `ConditionExpression="attribute_not_exists(notified)"`，SQLite 用 `WHERE notified = 0`，兩者都能在併發下保證「只有一個呼叫者拿到 True」，且 `tests/test_storage_backend.py` 對兩個 backend 都有獨立測試覆蓋這個 conditional 行為（不是只測 happy path）。
- `test_rate_limited_message_is_not_enqueued_and_notifies_once`（`tests/test_webhook.py`）是少見的「真的驗證了行為」的測試：用 `MagicMock` 斷言 `send_message` 只被呼叫一次、直接檢查 SQS 佇列裡的訊息數量（而非只信任回傳值），對第二次超限額外驗證 `fake_send.assert_called_once()`（次數未增加）。
- TTL 計算正確且有註解說明理由：分鐘窗 `+120s`（覆蓋 60s 窗 + 時脈誤差）、日窗 `+26*3600s`（覆蓋整個 UTC 日 + buffer），邊界推演（窗頭/窗尾插入）都能保證 ttl 落在窗結束之後，不會提早被清掉。
- infra 權限最小化做得好：只有 `WebhookFunction` 拿到 `RateLimitsTable` 的 `DynamoDBCrudPolicy`，`ProcessorFunction`／`SchedulerFunction` 都沒有動，符合「重放功能只需要 ConversationsTable」的預期，沒有過度授權。
- 重放邏輯對「上一輪 agent 失敗」的情境處理正確：`run_agent` 例外時不會 `save_message`，因此 `_find_replay` 抓到的「最後一組」永遠是上一次成功的問答，不會誤把錯誤訊息當作可重放的內容。
- 改動範圍 surgical：`CLAUDE.md` 只新增一行表格、`config.py`/`conftest.py` 的新增都嚴格對齊既有同類函式的風格與命名，沒有夾帶無關重構。

## Round 2 複驗（2026-07-26）

generator 回報已修正 round 1 的 🔴 Critical 與 🟡 Warning，複驗如下：

### 🔴 Critical 修復確認

- **`src/agent/db/backend.py:911`**：`UpdateExpression` 已改為
  ```python
  UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD hit_count :incr",
  ```
  子句順序改為 `SET` 在前、`ADD` 在後，符合 DynamoDB 官方要求的 `SET, REMOVE, ADD, DELETE` 固定順序。`ExpressionAttributeNames`/`ExpressionAttributeValues` 未變、語意（`if_not_exists` 只在首次寫入設定 ttl）未變。**已修復。**（註：本輪仍只能用 moto 驗證語法可解析且行為正確，無法連到真實 AWS 驗證會被接受；但子句順序已對齊官方文件範例慣用寫法，殘餘風險已降到最低。）

### 🟡 Warning 修復確認

- **`src/agent/webhook_handler.py`**：限流通知改用新增的 `send_plain_message()`（`telegram_client.py`），單次送出、不帶 `parse_mode`、不做 Markdown-fail 重試、`timeout=3.0`（透過 httpx per-request timeout 覆蓋 client 預設的 10.0s）。最壞情況從原本 `send_message()` 的 ~20s（Markdown 失敗會整段重試一次）降到 3s，落在 `WebhookFunction` 的 10s Lambda timeout 預算內，且 docstring 明確寫出這個取捨（"For callers on a tight timeout budget ... that must not risk send_message()'s worst case"）。**已修復。**
- 額外加分：`check_rate_limit()` 呼叫本身也包了 `try/except Exception`，fail-open（`allowed, notify_text = True, None`）並用 `logger.exception` 記錄含 `chat_id` 的錯誤內容，註解說明理由（"Rate limiting is a protective guard, not core functionality"）。這是本輪需求「4. fail-open 實作合理性」的驗收重點，例外範圍（bare `Exception`）與既有 `processor_handler.py` 對 `run_agent()` 的 fail-safe 慣例一致，log 內容包含 chat_id 足以追查，合理。

### 新增測試檢查

- `test_rate_limit_check_failure_fails_open_and_still_enqueues`：用 `MagicMock(side_effect=RuntimeError(...))` 讓 `check_rate_limit` 直接拋例外，斷言 `resp["statusCode"] == 200` **且**實際檢查 SQS 佇列收到 1 則訊息——不是只信任回傳值，是真的驗證了「fail-open 後仍照常 enqueue」這個行為。
- `test_rate_limited_message_is_not_enqueued_and_notifies_once` 同步改 mock `send_plain_message`（原本 mock `send_message`），與 production code 的呼叫對象一致，斷言邏輯未變。

### 是否引入新問題

- 檢查 `send_plain_message` 實作：沒有 try/except（設計上刻意讓例外往外拋，由呼叫端決定要不要吞），呼叫端 `webhook_handler.py` 已用 try/except 包住並 log，行為正確、沒有洩漏未處理例外的風險。
- 檢查 webhook 回 200 路徑：fail-open 分支與原本「被限流」分支共用同一個 `if not allowed` 區塊之外的邏輯，fail-open 直接讓 `allowed=True` 走正常 enqueue 路徑，未新增額外分支或重複程式碼，surgical。
- `git diff main --stat` 確認本輪異動僅集中在 `backend.py`（1 行 `UpdateExpression` 字串）、`webhook_handler.py`（try/except + import 改名）、`telegram_client.py`（新增 1 個函式）、`test_webhook.py`（改 mock 對象 + 新增 1 條測試），無不相關改動。
- 未發現新的 Critical / Warning。

### Test results（round 2）

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：310 passed，12 errors（同上，`test_local_integration.py` 沙箱 socket bind 限制，與本次改動無關）
- 停用沙箱重跑：**322 passed, 0 failed**（與 generator 自報一致，較 round 1 多 1 條 fail-open 測試）
- 既有 suite regression：✅ 無

## 結論

- **Round 1**：否（1 個 🔴 Critical + 1 個 🟡 Warning，回送 generator）
- **Round 2（最終）**：**是，可合併**。🔴 Critical（`UpdateExpression` 子句順序）與 🟡 Warning（webhook 同步呼叫 Telegram 的 timeout 風險）皆已正確修復，且新增測試有效覆蓋 fail-open 行為，未發現新問題。全測試 322/322 passed，無 regression。
- 未解事項（非阻塞，供後續參考）：
  1. round 1 遺留的 🟢 建議未處理，非阻塞：分鐘窗超限不計入日窗計數的設計決定建議找機會與需求方（PO/使用者）確認一次。
  2.（選配）補 replay 10 分鐘邊界與空白差異的測試案例。
  3. 建議部署後第一時間觀察 CloudWatch，確認 `increment_rate_limit` 在真實 DynamoDB 上沒有拋出 `ValidationException`（本地測試環境用 moto，無法 100% 保證真實 AWS 行為與本地一致）。
