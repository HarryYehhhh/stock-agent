---
name: feature
description: >-
  用於方向已明確的小功能開發、Bug 修復或補充測試。包含 Generator（實作與測試）與 Evaluator（獨立驗收與產出 QA Report、更新 CHANGELOG）兩階段。
---

# Feature Development Workflow

用於已討論清楚範圍的小型功能、修復 Bug 或補充測試。

## 流程規範

### Phase 1: Generator（實作與測試）

1. **明確假設與成功標準**：在開始前清楚陳述假設與成功標準。
2. **Bug 重現測試先行**：若為修復 Bug，必須先寫一個能重現該 Bug 的失敗測試。
3. **最小精準變更（Surgical Changes Only）**：
   - 僅修改與需求直接相關的程式碼。
   - 不「順手改進」無關的程式碼。
4. **本地測試驗證**：
   ```bash
   PYTHONPATH=src uv run pytest tests/ -v
   PYTHONPATH=src uv run pytest tests/test_local_integration.py -v
   ```
   若有修改 `infra/template.yaml`，執行：
   ```bash
   sam validate --region ap-northeast-1 --lint
   ```

### Phase 2: Evaluator（驗收與報告）

1. **獨立驗證**：
   - 評估者須親自執行測試，不可僅引用 Generator 自報的數據。
   - 檢查測試覆蓋邊界與防回歸保護。
2. **產出 QA Report**：
   - 在 `docs/qa-reports/<feature-name>.md` 建立報告。
   - 格式應包含：
     - **需求 checklist**（項目 / Status / Note）
     - **Test results**（指令、通過數、既有 suite regression 確認）
     - **獨立重測**（關鍵函式/檢索/API 的獨立測試輸出）
     - **Findings**（🔴 Critical / 🟡 Warnings / 🟢 Suggestions / ✅ What looks good）
     - **結論**（是否可合併、後續建議）
3. **更新 CHANGELOG**：
   - 在 [docs/CHANGELOG.md](file:///Users/yehhungwei/stock-agent/docs/CHANGELOG.md) 最上方追加條目：
   ```markdown
   ## YYYY-MM-DD — <feature-name> — evaluator — ✅ Pass (或 ⚠️ Pass with issues / 🔴 Fail)
   - 連結：docs/qa-reports/<feature-name>.md
   - 驗證：Unit <result>；Integration <not required / pass / fail>；E2E <not required / pass / fail>；External Smoke <not requested / pass / fail>（blocking: false）
   - 備註：循環次數、未解問題、修改範圍摘要
   ```
4. **交付**：Feature workflow 預設不自動 commit 或開 PR，留待開發者檢視。
