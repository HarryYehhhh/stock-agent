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

## 2026-07-21 23:28 — groq-migration-and-market-summary — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定後實作）；分支 `feat/groq-migration-and-market-summary`
- 驗證：Unit pass（86/86，`PYTHONPATH=src uv run pytest tests/ -q`）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：兩部分——(1) LLM 由 Google Gemini（`gemini-2.5-flash` / `google-genai`）遷移到 Groq（`llama-3.3-70b-versatile` / `groq` SDK）；同步更新 `agent.py` tool-use loop、`config.py`、`.env.example`（`GEMINI_API_KEY`→`GROQ_API_KEY`）、`Makefile ssm-put`、`pyproject.toml`/`uv.lock` 與 README/CLAUDE/AGENTS 文件。(2) 新增每日盤勢摘要 `market_summary.py`：抓 Big 7 + AI 產業英文新聞存進本地 SQLite `news` 表，再由 Groq 綜整成繁中每日 briefing 並存進 `market_summaries`（附來源 news ids 供稽核）；`backend.py` 新增 `news`/`market_summaries` 表與存取方法，`local_server.py` 新增 `/market-summary`、`/news` endpoint，新增 `tests/test_market_summary.py`。

## 2026-07-21 15:00 — earnings-notifications — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定計劃後實作）
- 驗證：Unit pass（79/79）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：local backend 通知改為「sync 未來 30 天財報到 SQLite → watchlist ⋈ earnings_calendar join → 推播」；新增 `earnings_notifications` 冪等表、`earnings_calendar.updated_at` 與日期 index（含舊 DB migration）；Finnhub 失敗時推播帶警告並用快取資料；dynamodb（Lambda）路徑維持原 live-fetch 行為；新增 `make notify-earnings`。

## 2026-07-20 15:18 — local-first-storage-sprint — evaluator — ⚠️ Pass with issues
- 連結：`docs/specs/local-first-storage.md`, `docs/contracts/local-first-storage-sprint.md`, `docs/decisions/0001-local-first-storage-backend.md`, `docs/qa-reports/local-first-storage-sprint.md`
- 驗證：Unit pass；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環 2 次；repo `.venv` 完整測試 55/55 通過；`make test` 因當前 PATH 缺少 `uv` 而未啟動 pytest；未解 P0/P1：0/0。
