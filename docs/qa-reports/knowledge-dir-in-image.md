# QA Report: knowledge-dir-in-image

- **Contract**: 無 contract（feature skill，直接對照需求敘述驗收）
- **Date**: 2026-07-30
- **Verdict**: ✅ Pass

## 需求檢查表

| 需求條目 | Status | Note |
|---|---|---|
| Dockerfile 加 `COPY knowledge/ ./knowledge/` | ✅ | `Dockerfile:13`，緊接在 `COPY src/agent/ ./agent/` 之後；`infra/template.yaml` 三個 Function 的 `Metadata.DockerContext` 皆為 `../`（repo root），故 `knowledge/` 相對路徑正確對應到 repo root 的 `knowledge/` 目錄，而非 `src/` 底下 |
| `retrieval.py` `_KNOWLEDGE_DIR` 改為往上走訪 parents 找第一個含 `knowledge/` 的目錄，相容本機與 image 扁平 layout | ✅ | `_resolve_knowledge_dir()`（`retrieval.py:25-58`）從 `retrieval.py` 所在目錄（本身即 `knowledge` package）往上走 `parents`，找第一個「有 `knowledge/` 子目錄且不等於自己」的祖先；本機（`src/agent/knowledge/retrieval.py`）與 image 扁平 layout（`agent/knowledge/retrieval.py`，`knowledge/` 為 `agent/` 的 sibling）皆驗證正確解析（見下方抽測） |
| 找不到時 log warning 一次而非無聲空轉 | ✅ | 走訪到 filesystem root 仍找不到時，回傳 sentinel 路徑（`package_dir / "_not_found"`，保證不存在）並 `log.warning(...)`；`_KNOWLEDGE_DIR = _resolve_knowledge_dir(Path(__file__))` 在 module import 時只執行一次，天然滿足「只記一次」，不是 request-path 上的 per-call 邏輯 |
| 補測試：扁平 layout 解析、本機 layout 不 regress、找不到時 warning + fallback | ✅ | `tests/test_knowledge_retrieval.py` 新增 4 case：`test_resolve_knowledge_dir_local_layout`、`test_resolve_knowledge_dir_lambda_image_layout`、`test_resolve_knowledge_dir_not_found_logs_warning_and_returns_fallback`（斷言 `caplog` 有 WARNING 且回傳目錄不存在）、`test_module_level_knowledge_dir_resolves_to_real_repo_knowledge_dir`（regression guard，對真實 repo layout 斷言 `_KNOWLEDGE_DIR.is_dir()` 且 `indices.md` 存在） |

## 邏輯正確性重點覆核

- **本機 layout 不會誤解析成 `src/agent/knowledge/` 自己**：`package_dir`（`retrieval.py` 所在目錄）本身就叫 `knowledge`，走訪第一輪 `ancestor = src/agent`，`candidate = src/agent/knowledge == package_dir`，被 `candidate != package_dir` 擋掉，正確跳過；下一輪才落在 repo root 的 `knowledge/`。已用 `python -c "from agent.knowledge import retrieval; print(retrieval._KNOWLEDGE_DIR)"` 實測確認為 `/Users/yehhungwei/stock-agent/knowledge`（repo root），非 `src/agent/knowledge`。
- **image layout 解析到 `/var/task/knowledge`**：用 `docker build` 出實際 production image 後 `docker run` 驗證（見下方「端到端驗證」），確認 `_KNOWLEDGE_DIR == /var/task/knowledge`，`PLAYBOOKS_DIR`/`EXCEPTIONS_DIR` 皆為其子目錄且非空。
- **「最近祖先」策略合理**：`package_dir.parents` 本身即按由近到遠排序，第一個符合條件的祖先必為最近的，不需要額外排序邏輯；multi-layer 情境（例如某個更上層目錄剛好也叫 `knowledge`）不會發生，因為 loop 一找到就 return，不會跳過離自己更近的候選。
- **warning 只記一次**：`_KNOWLEDGE_DIR` 是 module-level 常數、`_resolve_knowledge_dir` 只在 import 時呼叫一次；不是放在 `retrieve`/`retrieve_scored` 等 per-request 呼叫路徑內，沒有每次請求重複噴 log 的風險。
- **Dockerfile COPY 位置**：`WORKDIR ${LAMBDA_TASK_ROOT}` 在檔案最前面已設定，`COPY knowledge/ ./knowledge/` 相對於 `WORKDIR` 展開為 `${LAMBDA_TASK_ROOT}/knowledge`，與 `agent/`（`COPY src/agent/ ./agent/`）互為 sibling，與程式碼的走訪假設一致。

## 端到端驗證

1. **本機 import + 抽測**（`PYTHONPATH=src uv run python -c ...`）：
   - `_KNOWLEDGE_DIR = /Users/yehhungwei/stock-agent/knowledge`，`is_dir() = True`
   - `PLAYBOOKS_DIR`、`EXCEPTIONS_DIR` 皆 `is_dir() = True`
   - `retrieve_scored("今天天氣如何", source_dir=EXCEPTIONS_DIR)` 命中 `off-topic#天氣`（分數 7.696），符合 PR #50 off-topic 攔截門檻（`EXCEPTION_MIN_SCORE = 3.5`）
   - `retrieve("財報 是什麼")` 命中 KB terminology 條目
2. **模擬 image 扁平 layout**（`tmp` 下手動組出 `task/agent/` + `task/knowledge/`，`PYTHONPATH` 指向 `task/`）：三個 index（kb=14、playbooks=5、exceptions=6）皆非空，`EXCEPTIONS_DIR` 命中正確
3. **實際 `docker build -f Dockerfile .` 出 production image 並 `docker run` 驗證**（最強驗證，非模擬）：
   ```
   KNOWLEDGE_DIR = /var/task/knowledge
   is_dir: True
   kb sections: 14
   playbooks sections: 5
   exceptions sections: 6
   exceptions hit: True off-topic#天氣
   ```
   確認修復前的 bug（`parents[3]` 在 image 扁平 layout 下會指到 `/var` 而非 `/var/task/knowledge`，且 image 本來就沒 `COPY knowledge/`）在修復後於真實 image 中已解決：三個 BM25 index（KB / playbooks / exceptions）皆非空，`search_knowledge_base`、playbook 注入、PR #50 off-topic 攔截在 prod 上會恢復生效。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 結果：554 passed / 0 failed / 0 skipped（重跑兩次皆穩定 554 passed，與 generator 回報一致）
- 既有 suite regression：✅ 無
- 附註：驗收過程中第一次跑全套曾在午夜（00:00 台北時間）整數秒跳日當下遇到 10 個 `test_scheduler.py`/`test_watchlist_notify_flow.py` 失敗（`assert 0 == N` 之類，皆為依賴「明天」日期計算的既有測試）；已用 `git stash` 切回乾淨 main 單獨跑同兩檔案（24 passed）、`git stash pop` 復原後再連跑兩次全套（皆 554 passed）確認為當下系統時鐘跨日造成的既有 flaky，與本次 `knowledge/retrieval.py`／`Dockerfile` 改動無關（改動範圍不涉及日期邏輯），非 regression。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- `src/agent/audit/paths.py:18` 有同樣模式的 `_PROJECT_ROOT = Path(__file__).resolve().parents[3]`（`snapshots/`/`audit/` 目錄）。目前 Dockerfile 未 `COPY` 這兩個目錄進 image，且 audit/judge 屬本機/CI 離線稽核工具、不在 Lambda runtime 路徑上，故不在本次需求範圍內，維持原狀即可；僅供未來若該工具也要進 image 時參考。

### ✅ What looks good
- `_resolve_knowledge_dir` 的走訪邏輯用「排除自己」而非魔術 index，避免了本機/image 兩種 layout 深度不同時需要各自硬編碼一個 `parents[N]` 的脆弱性，且對兩種已知 layout 都有測試覆蓋。
- fallback sentinel（`package_dir / "_not_found"`）保留了舊行為（BM25 index 建構時 `source_dir.is_dir()` 為 False → 回傳空 list），沒有改變「真的找不到時」的降級行為，只是加上了可觀測性（warning）。
- 用 `caplog` 斷言 warning 訊息、用 `tmp_path` 建構兩種 layout 的假目錄樹，測試方式乾淨且不依賴真實檔案系統以外的 mock。
- 驗收過程中實際 `docker build` 出 production image 跑過一次，非僅止於本機模擬，對這類「只有在 image 裡才會炸」的 bug 是最直接的驗證方式。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次改動無待辦）
