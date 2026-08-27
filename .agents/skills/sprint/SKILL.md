---
name: sprint
description: >-
  用於需要完整規格 (Spec)、契約 (Contract) 或架構決策 (ADR) 的大型功能或重構任務。包含 Planner → Generator → Evaluator 完整三階段與自動 PR 流程。
---

# Sprint Workflow

用於較大型的功能開發、跨模組重構或需要事前設計規格的變更。

## 流程規範

### Phase 1: Planner（規格與架構設計）

1. **撰寫 Spec**：
   - 建立 `docs/specs/<sprint-name>.md`，描述背景、目標、使用者情境與介面設計。
2. **撰寫 Contract**：
   - 建立 `docs/contracts/<sprint-name>.md`，定義精確的驗收條件、Deliverables（如 P1 核心功能 / P2 進階項目）、DoD（Definition of Done）與測試策略。
3. **架構決策記錄（若有需要）**：
   - 若涉及資料庫 schema、新架構模組或外部依賴調整，建立 `docs/decisions/XXXX-<decision-name>.md` 並更新 `docs/decisions/README.md`。
4. **追加 CHANGELOG**：
   - 在 [docs/CHANGELOG.md](file:///Users/yehhungwei/stock-agent/docs/CHANGELOG.md) 最上方追加 Planner 階段記錄（標註 spec/contract/ADR 連結與決策摘要）。

### Phase 2: Generator（實作與單元/整合測試）

1. **依據 Contract 實作**：
   - 嚴格依照契約中定義的 P1/P2 交付項目開發。
   - 保持最小代碼更動，避免過度設計。
2. **撰寫與執行測試**：
   - 編寫對應的單元測試與整合測試。
   - 驗證測試全部通過：
     ```bash
     PYTHONPATH=src uv run pytest tests/ -v
     PYTHONPATH=src uv run pytest tests/test_local_integration.py -v
     ```
   - 若動到 AWS SAM template，進行驗證：
     ```bash
     sam validate --region ap-northeast-1 --lint
     ```

### Phase 3: Evaluator（驗收、QA 報告與開 PR）

1. **對照 Contract 驗收**：
   - 逐條核對 Contract 中的 checklist 與可交付項目。
   - 執行獨立重測，記錄 P50/P95 數據或邊界條件。
2. **產出 QA Report**：
   - 在 `docs/qa-reports/<sprint-name>.md` 寫入完整 QA 報告。
3. **追加 CHANGELOG**：
   - 在 [docs/CHANGELOG.md](file:///Users/yehhungwei/stock-agent/docs/CHANGELOG.md) 最上方追加 Evaluator 階段記錄。
4. **Git Commit & Open PR**：
   - 提交 commit，push 至遠端分支。
   - 建立 PR 並加上 `auto-merge` label（CI checks 全綠後自動 squash merge 並 dispatch deploy）。
