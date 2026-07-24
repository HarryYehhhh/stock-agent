# QA Report: chat-cli

- **需求依據**：使用者需求（本次為 feature，無 contract）— 見 evaluator 派工訊息
- **Date**: 2026-07-25
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| `make chat` 互動式 CLI，本地終端機多輪對話 | ✅ | `src/agent/chat_cli.py` REPL，`Makefile` 新增 `chat` target |
| 呼叫 `run_agent(user_message, history)`，session 內保留 history 多輪傳入 | ✅ | 每輪呼叫後以新 list 追加 user+assistant，下一輪傳入；`test_second_turn_passes_first_turn_as_history` 驗到完整 `{"role": "user"/"assistant", "content": ...}` pair |
| 不經過 Telegram / webhook / SQS | ✅ | `chat_cli.py` 只 import `agent.agent.run_agent`，無 Telegram/SQS 相關 import |
| 用 `.env` 的金鑰，`STORAGE_BACKEND=local` | ✅ | `make chat` 沿用 `dev` target 的 `cp -n .env.example .env \|\| true` + `uv run --env-file .env` 模式；`.env.example` 中 `STORAGE_BACKEND=local` 已是預設值 |
| Makefile 加 `chat` target | ✅ | `.PHONY` 已補 `chat`；target 內容與 `dev` 風格一致 |
| 附測試（mock run_agent，驗證多輪 history 傳遞與離開行為） | ✅ | `tests/test_chat_cli.py` 7 個 case：exit/quit/EOF/空白行/單輪/雙輪 history/history 上限 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：219 passed / 12 errors（既有 `tests/test_local_integration.py` 因沙盒禁止 `ThreadingHTTPServer` bind localhost 埠而 error，與本次改動無關）
- 沙盒外重跑（`dangerouslyDisableSandbox`）：**231 passed, 0 failed**，含 12 條原本 error 的 socket-bind 測試全綠
- `tests/test_chat_cli.py` 單獨執行：**7 passed**
- 失敗測試：無
- 既有 suite regression：✅ 無回歸
- 額外手動驗證：`printf 'exit\n' | make chat` 實跑，輸出「再見！」，exit code 0

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- **`src/agent/chat_cli.py:36`**：`run_agent()` 若拋例外（例如 Groq API 網路錯誤）會直接讓整個 REPL crash，使用者需重跑 `make chat` 且已輸入的多輪 history 全部遺失。目前 scope 是「最小實作、本地互動用途」，且使用者需求未提及錯誤處理，維持現狀不強制修；若之後常在不穩定網路環境使用，可考慮包一層 try/except 印出錯誤後繼續迴圈。

### ✅ What looks good
- `history` 用「產生新 list 再切片」而非原地 `append`/`mutate`，避免呼叫端持有舊參照被污染，並直接複用 `agent.db.conversations.HISTORY_LIMIT`（20）與既有多輪對話上限語意一致，沒有重複定義 magic number。
- 離開條件覆蓋齊全（`exit`/`quit` 大小寫不拘、Ctrl-D 觸發 `EOFError`、Ctrl-C 觸發 `KeyboardInterrupt`），且用同一個 `except (EOFError, KeyboardInterrupt)` 讓兩者行為一致（印出「再見！」後 return），簡潔不過度設計。
- `Makefile` 的 `chat` target 完全沿用 `dev` target 既有的 `.env` bootstrap 模式（`cp -n .env.example .env || true`），沒有另外發明新慣例，維持專案一致性。
- 測試用 `_feed_input` 以 iterator + `EOFError` 模擬 `input()` 序列，是常見且乾淨的 REPL 測試手法；`test_second_turn_passes_first_turn_as_history` 用 `call_args_list[1].args` 精確斷言第二輪傳入的 history 內容（角色、順序、content 皆對），不是只驗「有東西傳進去」而已，測試品質足夠。
- diff 精簡（`Makefile` 僅 6 行改動、新增兩個獨立檔案），沒有動到任何既有檔案或既有邏輯，符合 surgical changes 原則。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋項；若要精進可考慮上述 🟢 建議（run_agent 例外處理），非必要。
