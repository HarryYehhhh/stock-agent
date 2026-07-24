# QA Report: ecr-lifecycle-policy

- **Contract**: 無（feature flow，直接對照使用者需求）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. `infra/` 下新增 lifecycle policy JSON：保留最近 3 個 image，其餘 expire（tagStatus any） | ✅ | `infra/ecr-lifecycle-policy.json`：`tagStatus: any` / `countType: imageCountMoreThan` / `countNumber: 3` / `action: expire`，語意正確 |
| 2. Makefile `ecr-lifecycle` target：`describe-repositories` 過濾 `earningsagente` 開頭 repo，逐一 `put-lifecycle-policy`，不 hardcode 完整 repo 名 | ✅ | `for repo in $$(aws ecr describe-repositories ... \| jq -r 'select(startswith("earningsagente"))')`，`.PHONY` 有加 `ecr-lifecycle`，風格與既有 `ddb-tables`/`ddb-scan` 一致 |
| 3. `docs/runbook.md` 補充：為何不在 template.yaml、費用背景、重套方式、24 小時非同步生效 | ✅ | 新增「ECR lifecycle policy」段落，四點都涵蓋，文字與既有段落風格一致 |
| 4. 已實際套用，`aws ecr get-lifecycle-policy` 可驗證三個 repo | ✅ | 已用 `aws` CLI 實測三個 repo（見下），皆回傳與 `infra/ecr-lifecycle-policy.json` 一致的 policy，非僅信 generator 回報 |

## 驗證結果（實際執行，非轉述 generator）

對三個 repo 逐一執行 `aws ecr get-lifecycle-policy --region ap-northeast-1 --repository-name <repo>`，皆回傳：

```json
{"rules":[{"rulePriority":1,"description":"Keep only the 3 most recent images, expire the rest","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}
```

- `earningsagente26e66f8/processorfunction78019991repo` ✅
- `earningsagente26e66f8/schedulerfunction07e3fe86repo` ✅
- `earningsagente26e66f8/webhookfunction2cdba7a2repo` ✅

現役 image 驗證：`aws lambda get-function --function-name earnings-agent-processor` 目前指向 tag `processorfunction-c94c7ac5f273-processor`，用 `aws ecr describe-images` 依 `imagePushedAt` 排序後，該 tag 正是最新一次 push（processor repo 共 26 個 image，此 tag 排第一），落在 policy 保留的「最近 3 個」範圍內，不會被 expire 規則刪除。`imageCountMoreThan` 是依 push 時間保留最新 N 個，語意與需求（現役 image 不受影響）相符。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（沙盒環境）
- 結果：212 passed / 12 errors（sandbox 限制）
- 失敗測試：`tests/test_local_integration.py` 12 條，皆因 `ThreadingHTTPServer.bind()` 在沙盒下 `PermissionError: Operation not permitted`（本機 socket bind 限制），與本次改動（Makefile/runbook/JSON）無關，非回歸
- 既有 suite regression：✅ 無回歸（212 passed 與 generator 回報之關沙盒 224 passed 一致，差值 12 正是 sandbox socket bind 限制條數）

## 改動範圍核對

`git status --porcelain` 顯示：
```
 M Makefile
 M docs/runbook.md
?? infra/ecr-lifecycle-policy.json
```
`infra/template.yaml`、`samconfig.toml` 及其他檔案均未被觸碰，符合「不動循環依賴相關 IaC 檔案」的要求。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- **Makefile:51-56**：`ecr-lifecycle` target 假設本機已裝 `jq`（repo 內 `ddb-tables`/`ddb-scan` 已有此假設，非本次新增問題，維持一致即可，不需改動）。

### ✅ What looks good
- Lifecycle policy JSON 語意正確（`tagStatus: any` 涵蓋 tagged + untagged image，避免只清 untagged 導致舊 tagged image 持續累積計費）
- Makefile target 沒有 hardcode 完整 repo 名，未來 stack 重建、repo hash 改變時仍可直接重跑
- runbook 說明完整交代「為何不能進 IaC」的根因（循環依賴：先推 image 再建主 stack），對未來維運者很清楚
- 改動範圍精準，只碰到需要碰的三個檔案，沒有動 template.yaml/samconfig.toml
- 已實際在正式帳號套用並可用 CLI 驗證，不是紙上談兵

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次無阻塞項目）
