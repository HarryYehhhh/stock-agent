# QA Report: dockerfile-groq-dep

- **Contract**: 無正式 contract（feature skill，對照需求 + 程式碼品質驗收）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| 1. Dockerfile 依賴對齊 pyproject.toml（修 `groq` import 炸掉） | ✅ | `Dockerfile:7` `google-genai>=1.0.0` → `groq>=0.11.0`，其餘三個依賴（httpx/boto3/aws-lambda-powertools）與版本號皆與 `pyproject.toml` 完全一致 |
| 2. Makefile ssm-put/set-webhook/set-webhook-local/delete-webhook 補 `@` | ✅ | 全檔案 grep 含 `API_KEY`/`TOKEN`/`SECRET` 的 recipe 行逐一核對，四個 target 的所有會印出金鑰或呼叫外部 API 的行皆已加 `@`；`@test -n` 檢查行本就已有 `@`，未動；未發現其他 target 有洩密風險 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- Sandbox 內：179 passed, 12 errors（既有 `tests/test_local_integration.py` `ThreadingHTTPServer` socket bind 沙盒限制，與本次改動無關，歷史 QA report 已多次記錄同一問題）
- 非 sandbox 重跑：**191 passed, 0 failed**（含新增的 `tests/test_dockerfile_deps.py::test_dockerfile_pip_packages_match_pyproject_dependencies`）
- 既有 suite regression：✅ 無

### 獨立重現與驗證（evaluator 自行執行，非僅採信 generator 摘要）
1. `git stash push -- Dockerfile` 後單獨跑新測試，確認修復前會 fail，且錯誤訊息清楚：
   ```
   AssertionError: Dockerfile pip install list drifted from pyproject.toml dependencies.
   Only in Dockerfile: {'google-genai>=1.0.0'}
   Only in pyproject.toml: {'groq>=0.11.0'}
   ```
2. 獨立執行 `docker build -f Dockerfile .`：build 成功。
3. `docker run --rm --entrypoint python <image> -c "import groq; import agent.agent; import agent.processor_handler; import agent.scheduler_handler"` 全數成功匯入（`groq==1.5.0`），確認雲端會炸掉的三個 handler（agent/processor_handler/scheduler_handler）修復後皆可正常 import。驗證完畢已 `docker rmi` 清除，無殘留 image。
4. `make delete-webhook TELEGRAM_BOT_TOKEN=FAKE_TOKEN_XYZ` 實跑：輸出僅有 Telegram API 的 404 JSON 回應，無任何 recipe 行或金鑰值被 echo。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **tests/test_dockerfile_deps.py:29**：`_parse_dockerfile_pip_packages()` 的 regex 要求 `pip install` 區塊後緊接一個空白行（`\n\n`）才能匹配到區塊結尾。若未來有人在 `RUN pip install ...` 與下一個 Dockerfile instruction（如 `COPY`）之間拿掉空行、或緊接著加一行 comment，regex 會匹配失敗。不過失敗模式是安全的：`assert match` 會直接丟出清楚的 `could not find pip install --no-cache-dir block in Dockerfile` 訊息，不會靜默通過，因此風險可接受，僅供未來維護參考，不要求本次修改。

## What looks good
- 改動完全手術式：`git diff` 只有 Dockerfile 一行套件名稱替換、Makefile 7 個地方加 `@`、新增一個測試檔；未觸及 base image、`COPY`/`CMD`、`pyproject.toml`、`src/`、`infra/template.yaml`。
- 新測試以 `tomllib` 解析 pyproject 與 regex 解析 Dockerfile 兩邊比對「集合相等」，而非寫死套件清單，未來新增/刪除依賴時能持續有效防呆，且測試無網路/檔案 I/O 依賴之外的副作用。
- 測試的 docstring 清楚交代 root cause（`agent.py` 有 `from groq import Groq` 但 Dockerfile 沒裝）與失敗訊息（drift 的具體差異集合），對未來排查很有幫助。
- Makefile 修復精準覆蓋所有含金鑰/token 的 recipe 行（含 multi-line continuation 只需首行加 `@` 即可，此認知正確），全檔案掃描確認沒有遺漏其他 target。
- 獨立以真實 `docker build` + `docker run` 驗證確認修復對雲端部署問題（`Runtime.ImportModuleError: No module named 'groq'`）确實解決。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無需先處理任何事項，可直接合併。
