# QA Report: dev-env-file

- **Contract**: 無正式 contract，對照需求（修復 local dev 不載入 `.env` 導致 `config.py:37` `KeyError` → 500）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## Contract checklist（對照需求）

| Deliverable | Status | Note |
|---|---|---|
| `make dev` 的 uvicorn 加 `--env-file .env` | ✅ | `Makefile:20`，保留原有 `tee` 落檔與 `PYTHONUNBUFFERED=1` |
| `--env-file` 所需的 `python-dotenv` 依賴補上 | ✅ | 加在 `pyproject.toml` dev group，`uv.lock` 同步更新；確認修復前 lockfile 完全沒有 `python-dotenv`（`git show HEAD:uv.lock \| grep python-dotenv` 無輸出），確實是原本缺的必要依賴 |
| 先有 reproducing test 再修 | ✅ | `tests/test_dev_env_file.py::test_load_secrets_raises_keyerror_without_env_file` 重現 bug：清空 4 個 secret env vars 後 `config.finnhub_api_key()` 拋 `KeyError` |
| 驗證修法本身 | ✅ | `test_uvicorn_env_file_flag_loads_dotenv_before_secrets_are_read` 用 `uvicorn.Config(env_file=...)` 證明修法有效 |
| 測試不打真實外部 API | ✅ | 兩個新測試皆為本地 in-process 呼叫，無網路請求 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：148 passed / 12 errors（既有 `test_local_integration.py` 的 `ThreadingHTTPServer` socket bind 因 sandbox 網路限制 `PermissionError: Operation not permitted`，與本次改動無關，非本次引入的 regression）
- 非沙盒重跑結果：**160 passed, 0 failed**（與 generator 回報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無

新測試獨立驗證：
- `tests/test_dev_env_file.py` 單獨執行：2 passed
- 兩個測試互換執行順序（`test_uvicorn_...` 先跑、`test_load_secrets_...` 後跑）：2 passed，無污染
- 全 suite 中穿插執行：160 passed，未見其他測試因 `FINNHUB_API_KEY` 等 env var 或 `_load_secrets` lru_cache 被污染而失敗

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`tests/test_dev_env_file.py:58`**：`uvicorn.Config(app="agent.local_server:app", env_file=str(env_file))` 中的 `app=` 參數在建構時不會被 import/載入（已用 `inspect.getsource` 確認 `uvicorn.Config.__init__` 對 `env_file` 的處理與 `app` 載入邏輯無關，`load_dotenv` 是獨立的一段 if 分支），所以此處寫成任意字串效果相同；目前寫成真實模組路徑 `agent.local_server:app` 更貼近實際 `make dev` 用法，屬於好的可讀性選擇，非問題。
- fixture `clean_secrets` 的 setup/teardown 都呼叫 `config._load_secrets.cache_clear()`，且 teardown 順序（yield 後、monkeypatch 自動 revert 前）確保下個測試拿到的是乾淨、未快取的狀態——這個 pytest fixture teardown 順序的正確性值得在其他新增涉及 `lru_cache` 的測試中沿用同樣模式。

### ✅ What looks good
- diff 完全手術式：只動 `Makefile`（1 行）、`pyproject.toml`（1 行）、`uv.lock`（對應鎖檔更新）、新增 `tests/test_dev_env_file.py`，沒有動任何無關檔案或程式碼
- `python-dotenv` 正確放在 `[dependency-groups].dev`，不進 production `dependencies`——production Lambda 走 SSM Parameter Store（`config.py` 的 `_is_lambda()` 分支），不需要 `.env` / `python-dotenv`，符合 CLAUDE.md 對 Lambda config 的描述
- 測試命名與 docstring 清楚說明 root cause 與修法對應關係（`test_dev_env_file.py:1-13` 的模組 docstring），可讀性高
- 重現測試與驗證測試共用 `SECRET_ENV_VARS` 常數與 `clean_secrets` fixture，無重複程式碼
- 手動驗證（乾淨環境下 `/webhook` 無 `--env-file` 回 500、加上後回 401 webhook secret 驗證正常）與自動化測試方向一致，且未讓手動驗證取代自動化測試

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次無 Critical/Warning，可直接合併）
