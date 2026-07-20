# CHANGELOG

Harness 執行記錄。**最新的在上面**（reverse chronological）。

每條由 planner / evaluator 在自己跑完後追加，main Codex 不直接寫。
格式：
```
## YYYY-MM-DD hh:mm — <sprint 或 feature> — <agent> — <一句話結果>
- 連結：spec / contract / qa-report / ADR
- 驗證：Unit <result>；Integration <not required / pass / fail>；E2E <not required / pass / fail>；External Smoke <not requested / pass / fail>（blocking: <true/false>）
- 備註：循環次數、未解問題、後續工作（若有）
```

---

<!-- 新條目從這裡開始 -->

## 2026-07-20 15:18 — local-first-storage-sprint — evaluator — ⚠️ Pass with issues
- 連結：`docs/specs/local-first-storage.md`, `docs/contracts/local-first-storage-sprint.md`, `docs/decisions/0001-local-first-storage-backend.md`, `docs/qa-reports/local-first-storage-sprint.md`
- 驗證：Unit pass；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環 2 次；repo `.venv` 完整測試 55/55 通過；`make test` 因當前 PATH 缺少 `uv` 而未啟動 pytest；未解 P0/P1：0/0。
