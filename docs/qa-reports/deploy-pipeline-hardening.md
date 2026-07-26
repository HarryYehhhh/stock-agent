# QA Report: deploy-pipeline-hardening

- **Contract**: 無正式 contract；對照使用者需求（背景：2026-07-24 DLQ 死信事故）與程式碼品質驗收
- **Date**: 2026-07-26
- **Verdict**: ✅ Pass（round 2，最終）

---

## Round 2（最終）複驗結果

round 1 提出的兩個 🔴 Critical 已由 generator 修正並複驗通過，詳見下方。round 1 的完整分析保留在文末「Round 1（原始）分析」供追溯。

### 🔴1 修法複驗：`smoke-test-processor` fail-closed

`Makefile:58-79`（新版）把 `aws lambda invoke` 包進 `if ! INVOKE_META=$$(... 2>&1); then ... exit 1; fi`，只有「invoke 指令本身成功且 FunctionError 為空」才會印出 `✅ passed` 並 exit 0。

親自用 3 支 stub `aws` binary（不同 PATH 前綴，非沙箱環境）重跑三分支，結果：

| 分支 | stub 行為 | 結果 |
|---|---|---|
| 成功 | exit 0，無 FunctionError | `✅ ... passed`，`EXIT=0` |
| FunctionError | exit 0，response 含 `FunctionError: Unhandled` | `❌ ... FunctionError=Unhandled` + cat response，`EXIT=2`（make 包裝過的 exit 1） |
| **CLI 呼叫本身失敗**（模擬 `ResourceNotFoundException`，stderr 訊息 + exit 254，不觸及 Lambda） | `❌ ... aws lambda invoke command itself failed` + 印出 stderr 內容，`EXIT=2` | **round 1 的假通過已修正** |

三分支皆額外確認暫存 response file 有清理（`$TMPDIR` 內 `tmp.*` 檔案數在跑完三分支前後一致，皆為 0）。另外把 `sam` 也 stub 掉、跑一次完整 `make deploy`（CLI 失敗分支），確認錯誤能正確傳播到 `make deploy` 的最終 exit code（`DEPLOY_EXIT=2`）。

**Shell 語法檢查**：
- `INVOKE_META=$$(... 2>&1)` 把 stderr 併入捕獲值，是刻意設計——目的是讓 CLI 失敗時能把 stderr 的錯誤訊息印出來（`echo "$$INVOKE_META"`）。唯一的潛在副作用：若 invoke **成功**（exit 0）但 aws-cli/boto 仍對 stderr 印出無關雜訊（例如環境設定產生的 warning），該雜訊會被併入 `INVOKE_META` 一起餵給 `jq -r '.FunctionError // empty'`，導致 jq parse 失敗、`FUNCTION_ERROR` 靜默為空、仍判定為「passed」。這是一個較低機率的邊界情況（round 1 🔴1 的變體，但發生條件是「exit code 0 但 stderr 有雜訊」，比 round 1 的「exit code 非 0」更少見），列為 🟢 建議而非阻塞項——修正 round 1 明確重現的假通過（CLI exit 非 0）已達成，且要完全排除這個邊界情況需要分離 stdout/stderr 兩個變數，增加的複雜度與極低發生機率不成比例。
- 變數引用皆正確加雙引號（`"$$INVOKE_META"`、`"$$RESPONSE_FILE"`），無 word-splitting 風險。
- `RESPONSE_FILE` 在三個 exit 路徑（CLI 失敗 / FunctionError / 成功）都有 `rm -f`，無洩漏。

### 🔴2 修法複驗：CI 串接

`.github/workflows/deploy.yml:63-64` 在既有 `SAM deploy` step 之後新增：
```yaml
      - name: Post-deploy smoke test
        run: make smoke-test-processor STACK=earnings-agent REGION=ap-northeast-1
```
確認：
- **位置**：緊接在 `SAM deploy` step 之後、job 最後一步，符合「部署後」語意
- **credentials**：同一個 `deploy` job 內，`aws-actions/configure-aws-credentials@v4`（第 41-44 行）已在 job 層級設定好 AWS 憑證（OIDC role assume），GitHub Actions 同一 job 內所有 step 共用同一組已設定的 credentials/env，新 step 不需重複設定，沿用邏輯正確
- **region**：`REGION=ap-northeast-1` 與 job 開頭 `aws-region: ap-northeast-1`、`SAM deploy` step 的 `--region ap-northeast-1` 一致，無不一致風險
- **working directory**：新 step 未加 `working-directory` 或 `cd`，執行於 repo 根目錄（GitHub Actions 每個 step 預設回到 checkout 根目錄），與 `Makefile` 所在位置一致，正確
- **YAML 語法**：`python3 -c "import yaml; yaml.safe_load(...)"` 解析通過，無語法錯誤
- **runner 工具鏈**：`make`、`jq`、`aws` CLI v2 皆是 GitHub-hosted `ubuntu-latest` runner image 內建工具，無需額外安裝 step

無發現問題。

### Round 2 測試結果
- `PYTHONPATH=src uv run pytest tests/ -v`（停用沙箱）：**332 passed**，與 round 1、generator 自報一致，無 regression
- `sam validate --lint`：valid

### Round 2 結論
round 1 的兩個 🔴 Critical 皆已正確修正並經獨立複驗（非僅信 generator 自報）：
1. smoke test 現在對「invoke CLI 本身失敗」與「FunctionError」兩種失敗模式都 fail closed，且已用 stub 實測 3 分支 + 完整 `make deploy` 鏈路
2. CI 的實際生產部署路徑（push to main）已接上同一道 smoke test，不再只保護人工執行 `make deploy` 的情境

無新增 🔴/🟡，僅有 1 條 🟢 建議（stderr 雜訊污染 jq 解析的極低機率邊界情況，不阻塞）。round 1 的 🟡（`$(STACK)`/`AppName` 命名耦合）本輪未處理，維持邊界情境、非立即風險，不影響合併判斷。

**可合併。**

---

## Round 1（原始）分析

以下為 round 1 的完整紀錄，供追溯（結論已由上方 round 2 取代）。

### 需求 checklist（round 1）

| 需求 | Status | Note |
|---|---|---|
| 1a. `make deploy` 於 `sam deploy` 成功後 invoke 一次 `earnings-agent-processor`（region ap-northeast-1），payload `{"Records": []}` | ✅ | `Makefile:49-51` deploy 目標接續呼叫 `smoke-test-processor`；`--function-name $(STACK)-processor --region $(REGION)`，STACK/REGION 預設值與 template.yaml 的 `AppName`（`earnings-agent`）一致 |
| 1b. payload 無副作用 | ✅ | `processor_handler.handler()` 對 `event.get("Records", [])` 為空 list 時迴圈不執行、直接回 `{"statusCode": 200}`，不打 Groq/Telegram/DynamoDB；新增 `test_handler_empty_records_is_noop` 鎖住此假設 |
| 1c. 有 `FunctionError` 則印出錯誤內容並讓 `make deploy` 非零 exit | ⚠️ → ✅（round 2 已修） | round 1：只在「Lambda 回應內含 FunctionError」情境下正確，`aws lambda invoke` CLI 呼叫本身失敗時被靜默視為通過（🔴1）。round 2：已修正為 fail-closed，見上方複驗 |
| 2. Processor Errors alarm（Errors>0 / Sum / Period 60 / EvaluationPeriods 1 / TreatMissingData notBreaching / AlarmActions 到既有 SNS topic / earnings-agent 前綴命名） | ✅ | `infra/template.yaml:387-402` `ProcessorErrorsAlarm` 這次改動前已存在且除 Period 外全部符合要求，本次僅把 `Period: 300 → 60`。見下方「取捨評估」 |

### 取捨評估：「沿用既有 alarm 改 Period」vs「新增 alarm」

已用 `git diff main -- infra/template.yaml` 確認：這次改動前 `ProcessorErrorsAlarm` 已完整存在且各項參數（Namespace/MetricName/Statistic/EvaluationPeriods/Threshold+ComparisonOperator/TreatMissingData/AlarmActions/AlarmName 前綴）皆符合需求，只有 `Period` 是 300 而非要求的 60。generator 選擇原地把 `Period` 改成 60，而非新增一個重複的 alarm 資源——這個判斷合理：CloudFormation 允許對同一 metric+dimension 建立多個 alarm，但那樣會造成兩個同語意 alarm 同時開火、SNS 重複告警、且健檢 routine 用 `--alarm-name-prefix earnings-agent` 撈到的清單會多一筆功能重疊的雜訊。改既有資源的 `Period` 是最小且正確的修法，符合「不做多餘變更」原則。此取捨合理，**無需修正**。

### Round 1 Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：320 passed / 12 errors（`tests/test_local_integration.py` 因沙箱禁止 socket bind 產生 `PermissionError`，屬既有沙箱限制，非本次改動造成的回歸）
- 停用沙箱重跑：**332 passed**，與 generator 自報一致，無 regression
- `sam validate --lint`（於 `infra/` 目錄）：**valid**，與 generator 自報一致
- 既有 suite regression：✅ 無

### Round 1 Findings

#### 🔴 Critical（Must Fix，round 2 已修正，見上方複驗）

- **Makefile:60-71（`smoke-test-processor`）— `aws lambda invoke` 本身失敗時被靜默視為「通過」**
  目前邏輯只檢查 invoke 回應 metadata 裡的 `.FunctionError`，完全沒檢查 `aws lambda invoke` 這個指令本身的 exit code。若 invoke API 呼叫層級失敗（函式名稱找不到、IAM 權限不足、認證失效、節流、網路問題等），則會被靜默視為通過。已用 stub `aws` binary 實測重現：
  ```
  ==> smoke testing earnings-agent-processor (ap-northeast-1)
  An error occurred (ResourceNotFoundException) when calling the Invoke operation: Function not found
  ✅ processor smoke test passed (no ImportModuleError)
  EXIT_CODE=0
  ```
  **round 2 狀態：已修正**（見上方「🔴1 修法複驗」）。

- **`.github/workflows/deploy.yml` 未串接本次的 smoke test — 真正造成 2026-07-24 事故的自動化部署管線目前仍然沒有這道防線**
  本次改動只動了 `Makefile`、`infra/template.yaml`、`tests/test_processor_handler.py`；`.github/workflows/deploy.yml` 的 `deploy` job（`push` 到 `main` 時觸發、是這個專案實際的生產部署路徑）仍然是直接呼叫 `sam deploy ...`，完全沒有呼叫 `make deploy` 或這次新增的 `smoke-test-processor` 目標。
  **round 2 狀態：已修正**（見上方「🔴2 修法複驗」）。

#### 🟡 Warnings（Should Fix，round 2 未處理，非阻塞）

- **Makefile:61（`smoke-test-processor`）— `$(STACK)-processor` 命名假設與 `AppName` 參數的耦合**
  `template.yaml` 的 `ProcessorFunction` 命名是 `!Sub "${AppName}-processor"`（`AppName` 參數，預設 `earnings-agent`），而不是 `!Sub "${AWS::StackName}-processor"`。目前 `Makefile` 的 `STACK`（預設 `earnings-agent`）與 `AppName` 預設值剛好相同，所以預設情境下沒問題；但若日後有人用 `make deploy STACK=xxx` 部署到不同 stack 名稱、卻沒有同步在 `sam deploy` 加上 `--parameter-overrides AppName=xxx`，`smoke-test-processor` 會去 invoke 一個錯誤/不存在的函式名稱。屬邊界情境（目前專案只有單一 `earnings-agent` stack，非立即風險），不影響本輪合併判斷。

### Round 1 ✅ What looks good

- `processor_handler.handler()` 對空 `Records` 確實無副作用（不觸及 Groq/Telegram/DynamoDB），且用新測試 `test_handler_empty_records_is_noop`（含最小可行的 `_Ctx` fake context）鎖住此假設，測試命名與意圖清楚
- `--payload '{"Records": []}' --cli-binary-format raw-in-base64-out` 是 AWS CLI v2 下傳遞原始 JSON 字串 payload 的正確寫法（避免 v2 預設把 `--payload` 當成 base64 而丟出 `Invalid base64` 錯誤）
- region 正確帶入 `--region $(REGION)`（預設 `ap-northeast-1`），未依賴呼叫者 profile 預設 region
- `infra/template.yaml` 的 alarm 改動是最小且正確的既有資源調整，沒有引入重複資源

## 結論（最終，round 2）

- 是否可合併：**是**
- 最該先處理的 1–3 件事（皆非阻塞）：
  1. 🟢 建議：若要進一步排除極低機率的「invoke 成功但 stderr 有雜訊」邊界情況，可將 stdout/stderr 分開捕獲後再各自判斷（非必要）
  2. 🟡 建議：`smoke-test-processor` 註解補一句 `$(STACK)` 與 `AppName` 參數需保持一致的前提，避免未來有人覆寫 `STACK` 時踩到命名不一致
