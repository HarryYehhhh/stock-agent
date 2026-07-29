# QA Report: deploy-guard

- **Contract**: 無正式 contract；直接對照使用者需求（`make deploy` pre-flight guard，見任務描述）
- **Date**: 2026-07-29
- **Verdict**: ✅ Pass with issues（🟡 一項建議簡化，不阻擋合併）

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. 工作區必須乾淨（unstaged/staged 皆不可有變更） | ✅ | `git status --porcelain` 非空即擋。已用真實 tmp git repo 手動驗證 dirty workspace 會擋，且移除該檢查後對應測試真的會 FAIL（見下方 regression 驗證） |
| 2. `git fetch origin main` 後 HEAD 必須等於 origin/main | ✅ | 用 `FETCH_HEAD` 而非本地 `origin/main` ref 比對，避開對 remote-tracking ref/refspec 設定的依賴，效果等價且更穩健。已用真實 push-ahead 情境驗證 |
| 3. 不符 → 清楚繁中錯誤訊息 + 非零 exit + 絕不執行 sam deploy | ✅ | 兩種情境訊息皆說明「哪裡不符」+「怎麼修」，且都提及 FORCE=1 逃生口；測試斷言 `sam` stub 完全未被呼叫 |
| 4. 本機 `sam deploy` 加 `--confirm-changeset` | ✅ | 已加在 `deploy` target；CI 的 `.github/workflows/deploy.yml` 走獨立的 `sam deploy` 呼叫，不經過 `make deploy`，不受影響 |
| 5. 保留 FORCE=1 逃生口，印醒目警告，guard 失敗訊息要提到逃生口 | ✅ | `⚠️` 開頭警告，且兩個失敗分支的訊息都有提到 `FORCE=1` |
| 6. 不改 `.github/workflows/deploy.yml`；不破壞 `make smoke-test-processor` | ✅ | `git diff` 確認 workflow 檔零改動；`smoke-test-processor` target 內容逐行未動 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（CLAUDE.md 指定）
- 結果：537 passed（含新增 `tests/test_deploy_guard.py` 4 cases）
- 失敗測試：無
- 既有 suite regression：✅ 無（沙盒環境下 `test_local_integration.py` 因 socket bind 權限出現 13 個 ERROR，屬既有沙盒現象與本次改動無關——停用 sandbox 重跑後 537/537 全過，與 generator 回報一致）

### 額外驗證（本次重點）

1. **測試非空轉驗證**：暫時把 `deploy-guard.sh` 的 dirty-check 分支改成不 `exit 1`（僅印訊息），重跑 `test_deploy_guard.py` → `test_dirty_workspace_blocks_deploy_and_never_calls_sam` 確實 FAIL（訊息斷言與 `sam` 未被呼叫的斷言都會抓到）。驗證後已還原檔案，diff 與還原前一致。
2. **Fail-closed 情境**（核心價值）：
   - `scripts/deploy-guard.sh` 不存在 → `make deploy` 因 shell `No such file or directory`（exit 127）直接中止，`sam deploy` 不會被叫到。已用真實 repo 手動重現。
   - 非 git 目錄執行 `deploy-guard.sh` → `set -euo pipefail` 下 `git status`/`git fetch` 失敗即整支腳本以非零 exit code（128）中止，不會誤判為「乾淨」而放行。已手動重現。
   - 離線 / `git fetch` 失敗（模擬指向不存在的 remote）→ 同樣因 `set -e` 立即以非零 exit code 中止，不會誤放行。已手動重現。
   - 結論：guard 在各種「腳本本身失效」的邊界情境下都是 fail-closed，符合本 feature 的核心訴求。
3. **`--confirm-changeset` 是否真的覆寫 `samconfig.toml` 的 `confirm_changeset = false`**：SAM CLI 官方文件的參數優先順序為「命令列參數 > 環境變數 > `samconfig.toml` > 範本預設值」，CLI flag 優先權最高，會覆寫 config 檔設定，行為符合預期。未實際跑一次 `sam deploy` 打真的 AWS 驗證互動提示（會有副作用/需要憑證），依文件佐證合理採信；若要 100% 實測需在有真實 stack 權限的環境下人工跑一次 `make deploy`（不帶 FORCE）確認出現互動式 confirm 提示。
4. **CI 是否受影響**：`.github/workflows/deploy.yml` 的 deploy job 直接呼叫 `sam deploy`（不經 `make deploy`），且已有 `--no-fail-on-empty-changeset`，未使用 `--confirm-changeset`，因此本次改動對 CI 完全無影響；`smoke-test-processor` target 內容 diff 為零。
5. **`.gitignore` 涵蓋 `.aws-sam/`**：確認 `sam build` 產出的建置目錄已被忽略，不會讓「跑過 `make build` 後」的工作區被 guard 誤判為 dirty。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`Makefile:6`**：新增的 `SAM ?= sam` 變數化並非達成測試可注入性的必要條件。已實測：把 `deploy` target 內的 `$(SAM) deploy` 改回硬編碼 `sam deploy` 後，`tests/test_deploy_guard.py` 4 個測試依然全數通過——因為測試本來就是透過把 stub 塞進 `PATH` 最前面來攔截 `sam`，Makefile 用哪個字面值呼叫 `sam` 不影響這個機制。目前這個變數沒有其他用途（專案內沒有任何地方需要覆寫 `SAM=`），屬於「非必要但也無害」的多餘可配置性，與 CLAUDE.md「No 'flexibility' or 'configurability' that wasn't requested」的原則有點出入。
  建議修法：把 `deploy` target 的 `$(SAM) deploy` 改回硬編碼 `sam deploy`，移除 `SAM ?= sam` 這一行，測試不需要跟著改（stub 攔截機制不變）。若之後真的有多套 sam CLI 版本切換的需求再加回來也不遲。

### 🟢 Suggestions
- **`scripts/deploy-guard.sh:27`**：用 `FETCH_HEAD` 而非 `git rev-parse origin/main` 比對是刻意的設計取捨（避開對 remote-tracking ref 更新行為的依賴），已驗證行為正確，記錄下來供之後維護者理解，不需改動。

### ✅ What looks good
- Fail-closed 設計紮實：腳本缺失、無執行權限、非 git repo、fetch 失敗等「guard 自身失效」的邊界情境全部會讓 `make deploy` 中止，而不是意外放行進到 `sam deploy`。
- 錯誤訊息具體可行動：清楚指出哪一項不符（dirty workspace / HEAD 落後 origin/main）、如何修正（`git commit`/`git stash`、`git pull`），並統一提及 `FORCE=1` 逃生口；`FORCE=1` 本身有醒目的 `⚠️` 警告。
- 測試設計扎實：用真實 bare git repo + clone 驅動 `make deploy` 整條路徑（而非只單獨測 shell script），涵蓋 dirty / behind-origin / clean-synced / FORCE=1 四種情境，且用 stub `sam`/`aws` 避免打真網路；已驗證測試不是空轉（拿掉 guard 邏輯後測試真的會抓到）。
- Surgical：`git diff` 上 `Makefile` 只動了 2 處（新增變數宣告 + `deploy` target 3 行），未動其他 target；`.github/workflows/deploy.yml` 與 `smoke-test-processor` 完全未受影響。

## 結論
- 是否可合併：是
- 最該先處理的 1 件事：`Makefile:6` 的 `SAM ?= sam` 屬非必要抽象，建議簡化為硬編碼 `sam`（🟡，非阻擋項，順手改掉即可）
