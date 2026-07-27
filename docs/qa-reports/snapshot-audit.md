# QA Report: snapshot-audit

- **Contract**: docs/contracts/snapshot-audit.md
- **Spec**: docs/specs/snapshot-audit.md
- **ADR**: docs/decisions/0010-snapshot-audit-offline-llm-judge.md
- **Date**: 2026-07-27
- **Verdict**: ⚠️ Pass with issues（無 🔴 Critical、無 contract 條目 ❌，可合併；有 1 項 🟡 建議在合併前順手補）

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| A. CLI 骨架 `python -m agent.audit dump\|judge`，模組拆 `__main__.py`/`dump.py`/`judge.py` | ✅ | `src/agent/audit/` 三檔 + `paths.py`（共用路徑）+ `__init__.py`；argparse smoke 測試通過 |
| B. `dump --date`/`--table`，earnings → `snapshots/<date>/earnings.md` | ✅ | metadata 段（標題/產生時間/來源表/筆數）在首個 `## ` 前；每 symbol 一 section；symbol 字母序、日內日期升序；缺值省略欄位；`retrieve_scored` 對產出檔能命中對應 symbol section（有測試 + 真實跑驗證） |
| B. conversations → `audit/<date>/conversations.md` | ✅ | 按 chat_id 分組、組內 timestamp 升序，有測試 + 真實跑驗證（12 筆） |
| B. 目錄自動建立 | ✅ | `mkdir(parents=True, exist_ok=True)`，有測試 |
| C. `judge --date`/`--chat-id`/`--limit`(預設10)/`--top-k`(預設3) | ✅ | argparse 預設值與 contract 一致，有 smoke 測試 |
| C. Q/A 從 DynamoDB（非 md）配對，query=Q+A 檢索 snapshots | ✅ | `_fetch_qa_pairs` 直接 scan `conversations_table()`；`_judge_pair` query 拼接問題+回答 |
| C. 空命中 → `no-data`，不呼叫 Groq | ✅ | `_judge_pair` 在呼叫 `_ask_judge` 前對 `hits` 短路 return；測試 + 真實跑（report #3）皆驗證 |
| C. 有命中 → Groq judge（MODEL、temperature=0），tolerant JSON 解析 | ✅ | `_ask_judge` 固定 `temperature=0`；`_extract_json` 平衡括號解析，抓第一個 `{...}` |
| C. 記錄 `response.usage` token（比照 market_summary.py） | ✅ | 訊息格式與 `market_summary.py:344-346` 一致（`groq usage (audit judge): input=... output=...`） |
| C. `report.md`：檔頭統計 + 每筆 chat_id/問題/回答(截斷)/verdict/來源(`檔名#標題`+分數)/unsupported_claims/reason | ✅ | 逐項核對真實產出 `audit/2026-07-27/report.md` 皆齊全 |
| D. `judge` 指定日期快照缺失 → 友善錯誤（非 traceback） | ✅ | `找不到快照 ...，請先執行 python -m agent.audit dump --date ...`；測試鎖住 |
| D. DynamoDB scan 失敗 → 印錯誤、非 0 exit | ✅ | dump/judge 皆 `except Exception as exc: print(..., file=sys.stderr); return 1`，比照 `download_remote_store` |
| E. `.gitignore` 加 `snapshots/`、`audit/` | ✅ | `.gitignore` diff 新增 `/snapshots/` `/audit/`；`git status` 確認兩目錄實測產物未被 staged/tracked |
| Out of scope 未逾越（不動線上 runtime / 不新依賴 / 不動 retrieval 核心） | ✅ | `git diff --stat` 只新增 `src/agent/audit/`、對應測試、`.gitignore`、`docs/`；`retrieval.py` 零改動；無新 import 依賴 |
| Tests required（dump/judge/短路/limit/chat-id/配對/tolerant JSON/CLI smoke） | ✅ | 23 個新測試對應 contract 逐項要求，見下方 Test results |
| DoD：pytest 全綠 | ✅ | 421 passed（見下） |
| DoD：本機實測 dump 真實 DynamoDB + judge 真跑產 report | ✅ | 見下方「本機實測驗證」 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（sandbox 內 `test_local_integration.py` 因綁定本機 socket 被沙箱擋下 `PermissionError`，屬既有測試對 sandbox 網路限制的既知現象、與本次改動無關；停用 sandbox 重跑後全綠）
- 結果：**421 passed**，0 failed，0 skipped（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無（本次改動只新增檔案，未觸及既有模組）
- 新增測試：`tests/test_audit_cli.py`（6）+ `tests/test_audit_dump.py`（5）+ `tests/test_audit_judge.py`（12）= 23 個，逐一讀碼確認斷言對應 contract 要求（格式/排序/短路/limit/chat-id 過濾/配對邊角/tolerant JSON/CLI 預設值）

## 本機實測驗證（DoD）

在 worktree 內確認 generator 已留下的真實產物（gitignored、未 staged）：

- `snapshots/2026-07-27/earnings.md`：171 筆、171 symbol section，metadata 段正確（來源表 `earnings-agent-earnings-calendar`、筆數 171），抽查格式如 `- 2026-07-30（盤前）EPS 預估 $1.05，營收預估 0.2B`，無 `Decimal(...)` 洩漏字樣。
- `audit/2026-07-27/conversations.md`：12 筆、單一 chat_id（`2143054523`，已知測試 chat_id）分組、timestamp 升序。
- `audit/2026-07-27/report.md`：`--limit 5` 真跑，5 筆結果、統計 `grounded=1 ungrounded=3 no-data=1`；4 次 Groq 呼叫 + 1 次空命中短路（#3「本週有什麼科技大廠公布財報」檢索無命中 → `no-data`，未呼叫 Groq，與程式碼行為一致）。
- **真實抓到一則 hallucination**：report #5 `META的財報是什麼時候` bot 回答「META 的財報公佈時間為 7 月 31 日」，但 `snapshots/2026-07-27/earnings.md` 的 `## META` section 實際是 `2026-07-29（盤後）`，judge 正確判 `ungrounded` 並列出 `unsupported_claims: ['META 的財報公佈時間為 7 月 31 日']`。此為系統核心價值（量化幻覺）的正面實證。
- 產物內容 grep 未發現任何 `api_key`/`token`/`secret` 字樣，隱私上僅含既有測試用 chat_id 對話內容，且正確被 `.gitignore` 排除、`git status` 確認未進暫存區。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/audit/judge.py:212-226`（`_write_report`）**：spec D2 明文「judge 讀即時 DynamoDB 對話但檢索固定日期快照，有時間錯位、非從固定快照完全可重現——**記於報告 caveat**」，但目前 `_write_report` 的檔頭只有「產生時間 / 引用快照日期 / model / 統計」，**沒有落地這個 spec 已決策的 caveat 文字**。
  同時，真實跑出的 `audit/2026-07-27/report.md` 印證了另一個相關風險：v1 haystack 只有 earnings-calendar（D1 已知限制），report #1「最近有什麼AAPL新聞」與 #2「google最近有什麼新聞」都被判 `ungrounded`（各列出 2～5 項 `unsupported_claims`），但根因是新聞內容本來就不在稽核範圍內、不是 bot 真的編造財報數字（對照 #5 才是真的財報日期幻覺）。若使用者只看報告頂部「ungrounded=3」統計、不去翻 spec 的 Future work 段落，容易誤讀成「60% 回答有幻覺」，高估真實幻覺率。
  建議修法：在 `_write_report` 的 header 區塊加一行 caveat，例如「注意：問答內容讀自即時 DynamoDB（非固定快照），僅財報快照為引用日期的固定版本；v1 haystack 僅含財報行事曆，非財報類問題落 ungrounded/no-data 不代表答案錯誤，僅代表超出本次稽核範圍；稽核為啟發式初篩非絕對正確判定。」一行文字即可覆蓋 D1+D2 兩個已知限制，成本低、直接對齊 planner 已決策的要求。
  這不影響任何既有測試（無測試斷言 header 不含此行），也不影響任何行為邏輯，純文字補充，可視為快速跟進項，不阻塞合併。

### 🟢 Suggestions
- **`src/agent/audit/dump.py:54`（`_format_earnings_line`）**：負值 EPS（如 `ABUS` 的 `$-0.03`）顯示成 `$-0.03` 而非慣例的 `-$0.03`，純美觀問題，不影響 `retrieve_scored` 命中或人工 review 可讀性，無需修。
- **`src/agent/audit/judge.py:120-124`（`_fetch_qa_pairs`）**：`--limit 0` 目前語意是「不限制、回傳全部」（`limit > 0` 為 False 時走 else 分支），與「0 筆」的直覺不同；contract/spec 未定義 `--limit 0` 的語意，不算違反契約，僅留意此邊角行為若未來需要「稽核 0 筆」語意會與現況不同。
- **`audit/<date>/conversations.md`**：多行 content（如含換行的 bot 回答）直接接在同一個 `- timestamp [role] content` 條目後、無延續縮排，部分 markdown renderer 可能不會將換行後內容渲染進同一個 bullet。純人工 review 用途、不進檢索索引，不影響任何程式邏輯，僅為排版細節。

### ✅ What looks good
- **兩棵樹隔離確實生效**：`paths.py` 的 `SNAPSHOTS_DIR`/`AUDIT_DIR` 各自獨立，`retrieve_scored` 對 `snapshots/<date>/` 做非遞迴 `*.md` glob，`conversations.md`/`report.md` 不會混入檢索索引——程式碼、真實目錄結構、`test_dump_earnings_snapshot_is_retrievable` 三方一致確認。
- **空命中短路確實不呼叫 Groq**：`_judge_pair` 在 `if not hits: return {...}` 直接 return，`_ask_judge`（含 `_client().chat.completions.create`）不會被觸及；`test_judge_empty_hit_short_circuits_no_groq_call` 用 `mock_groq.chat.completions.create.assert_not_called()` 鎖住，且真實跑（report #3）行為一致。
- **`_pair_qa` 邊角案例正確**：懸空 user（結尾無後續 assistant）跳過、連續 user 覆蓋（新 user 取代舊的 pending_user）、非標準 role（如 `system`）忽略但不打斷既有 pending pairing——三種案例皆有獨立單元測試且邏輯讀碼確認無誤。
- **`_extract_json` 容錯正確**：用括號深度計數找「第一個平衡的 `{...}`」而非簡單 regex，可正確處理前後雜訊包裹；`json.JSONDecodeError` 與找不到 `{` 都回 `None`（呼叫端 fallback 為 `no-data` + 附原始內容前 200 字）。
- **Decimal 格式化無洩漏**：DynamoDB Number（`Decimal`）在 `.2f`/`.1f` format spec 下正確轉為十進位字串，真實產出檔 grep 無 `Decimal(` 字樣。
- **dump 產物與 `retrieval.py` parser 相容性已用真實 parser 驗證**：`test_dump_earnings_format_and_sort` 直接呼叫 `parse_sections`/`retrieve_scored`（非自製假 parser），且真實 DynamoDB dump 出的 171-symbol 檔案能被同一套 parser 正確解析並在真實 judge 跑中命中對應 section（如 `earnings#META`、`earnings#AAPL`）。
- **surgical diff**：`git diff --stat`/`git status` 確認本次改動僅新增 `src/agent/audit/`、3 個測試檔、`.gitignore` 兩行、`docs/CHANGELOG.md` 一段、以及 planner 產出的 spec/contract/ADR 三檔；未觸及任何既有線上模組（`agent.py`/`tools/`/Lambda handlers/IaC 零改動），完全對齊 contract 的「不改線上 runtime」邊界。
- **無密鑰/隱私外洩風險**：真實產物 grep 無 API key/token/secret；`snapshots/`、`audit/` 正確 gitignore 且未進暫存區，符合 D4 決策。
- **系統核心價值有真實實證**：真實 judge 跑抓到一則貨真價實的財報日期幻覺（META 7/31 vs 快照 7/29），證明「離線稽核量化幻覺」的設計目標确实可用。

## 結論
- 是否可合併：**是**
- 最該先處理的 1 件事：`judge.py` 的 `_write_report` 補一行 header caveat（時間錯位 D2 + news-scope D1 兩個已知限制），對齊 spec D2「記於報告 caveat」的既有決策；純文字補充，不影響任何測試或行為，可在下一輪小改動內順手處理，不需要因此卡住合併。
