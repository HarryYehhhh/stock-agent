# QA Report: off-topic-intercept

- **Contract**: 無 contract（feature skill，直接對照需求敘述驗收）
- **Date**: 2026-07-30
- **Verdict**: ✅ Pass

## 需求檢查表

| 需求條目 | Status | Note |
|---|---|---|
| 新增例外語料 `knowledge/exceptions/off-topic.md`，獨立目錄不污染 KB/playbook index | ✅ | `knowledge/exceptions/off-topic.md`（6 類：天氣/飲食/旅遊/運動/娛樂/閒聊）；`_load_sections` 用非遞迴 `source_dir.glob("*.md")`（`retrieval.py:110`），`_KNOWLEDGE_DIR`／`PLAYBOOKS_DIR`／`EXCEPTIONS_DIR` 三個目錄互不相交，已用 `python -m agent.knowledge.debug --source kb` 系列指令核對無交叉命中 |
| 用現有 BM25 `retrieve_scored` 比對使用者訊息 | ✅ | `_is_off_topic()` 直接呼叫既有 `retrieve_scored(query, top_k=1, source_dir=EXCEPTIONS_DIR)`，未新增比對邏輯 |
| 例外語料命中（分數 ≥ 校準門檻）且 playbook 無命中時，不進 Groq LLM，直接回固定句 | ✅ | `agent.py:246-248`：`if not playbook_hits and _is_off_topic(user_message): return finalize("off_topic", OFF_TOPIC_REPLY)`；`OFF_TOPIC_REPLY = "非美股相關問題無法回答"`（`agent.py:71`） |
| 範例「今天天氣如何?」→ 直接回固定句 | ✅ | 手動驗證：`_is_off_topic("今天天氣如何?")` → `True`（分數 7.696）；`test_run_agent_off_topic_query_returns_fixed_reply_without_calling_groq` 斷言 `mock_client.chat.completions.create.assert_not_called()` |
| fast path（ticker 財報日期）優先順序維持最前 | ✅ | `agent.py:225-234` fast path 區塊在 `playbook_hits`/`_is_off_topic` 判斷（239-248）之前，順序未變；`test_run_agent_fast_path_query_still_uses_fast_path` 驗證 |
| trace 記錄新 outcome（`off_topic`） | ✅ | `finalize("off_topic", OFF_TOPIC_REPLY)` 走既有 `finalize()`，`outcome` 欄位自動帶入；`test_run_agent_off_topic_query_saves_off_topic_outcome_trace` 驗證 `trace["outcome"] == "off_topic"`、`playbook_hits == []`、`fast_path is False`、`iterations == 0`、`tools == []` |
| 門檻用 `python -m agent.knowledge.debug` 實測校準 | ✅ | `debug.py` `--source` 加 `exceptions`；`EXCEPTION_MIN_SCORE = 3.5` 旁註解列出實測分數，已用 `--source exceptions` 重跑核對（見下方「抽測」） |
| 合法問題（財報/新聞/大盤/KB 名詞問句）不可誤擋 | ✅ | 見下方抽測；`_retrieve_playbook_hits` 非空保護 + KB 名詞問句本身對例外語料分數低（0.28–1.83，均 < 3.5）雙重保護 |

## 抽測結果（`python -m agent.knowledge.debug --source exceptions` + 直接呼叫 `_is_off_topic`）

| 查詢 | exceptions 分數 | playbook 是否命中 | `_is_off_topic` | 實際攔截結果 |
|---|---|---|---|---|
| 今天天氣如何? | 7.696（天氣） | 無 | True | ✅ 攔截，回固定句，Groq 未被呼叫 |
| 什麼是 EPS | 0.384（飲食，雜訊） | 無 | False | ✅ 不攔截，進 LLM |
| 本益比是什麼 | 0.384（飲食，雜訊） | 無 | False | ✅ 不攔截，進 LLM |
| 本週有什麼公司發布財報 | 1.827（娛樂，雜訊） | 有（earnings 11.797） | — | ✅ 不攔截（playbook 非空短路整個 `_is_off_topic` 呼叫） |
| 今天大盤怎樣 | 1.200（閒聊，雜訊） | 有（market-trends，既有測試涵蓋） | — | ✅ 不攔截 |
| AAPL 有什麼新聞 | 3.380（娛樂，接近門檻） | 有（stock-news 7.107） | — | ✅ 不攔截；此句是校準註解中特別提到的邊界案例，靠 playbook 非空保護而非分數本身 |
| test（既有 `test_playbook_injection.py` 改用的無命中句） | 無命中 | 無 | False | ✅ 不攔截，符合改動意圖 |
| META的財報是什麼時候 | — | — | — | ✅ 走 fast path，`_is_off_topic` 完全未被呼叫（fast path 在其之前 return） |

`AAPL 有什麼新聞` 是設計上比較細緻的一點：例外語料分數 3.380 低於 3.5 門檻本身就不會誤判，但校準註解誠實承認它「可能接近門檻」，並說明真正的保護機制是 `not playbook_hits` 短路（`_is_off_topic` 在 playbook 非空時根本不會被呼叫）。程式碼與註解一致，未過度宣稱單一門檻萬無一失。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v --ignore=.env --ignore=.env.example`
- 結果：537 passed / 0 failed / 13 errors
- 失敗（error）測試：全部集中在 `tests/test_local_integration.py`（`ThreadingHTTPServer(...).socket.bind(...)` 拋 `PermissionError: Operation not permitted`），為 sandbox 禁止本機 bind port 的既有環境限制，與本次改動（`agent.py`／`retrieval.py`／`debug.py`／`knowledge/exceptions/`）無關；該檔案不 import 本次改動的任何模組。與近期多份 QA report（`tool-error-short-circuit.md` 等）記錄的既有現象一致。
- 既有 suite regression：✅ 無。`tests/test_playbook_injection.py` 兩個「無命中」情境測試把範例句從「今天天氣如何」改成「test」，理由是「今天天氣如何」現在會被新的離題攔截提前 return，若不改會讓這兩個測試意外驗證到攔截行為而非原本要驗證的「playbook 無命中 → 不注入」路徑；已用 `debug.py` 核對「test」對 playbook／exceptions 兩個索引皆無命中，改動後測試仍驗證原本的行為，意圖不變、非弱化測試。

## Findings

### 🔴 Critical（Must Fix）

無。

### 🟡 Warnings（Should Fix）

無。

### 🟢 Suggestions

- **`src/agent/agent.py:239-248`**：`playbook_hits` 從「建 messages 之後」搬到「建 messages 之前」，純屬為了讓 off-topic 判斷能用到同一次 BM25 查詢結果、避免重複呼叫 `_retrieve_playbook_hits`。已核對 `_retrieve_playbook_hits(user_message)` 不依賴 `messages`／`symbols`／任何搬移前後才建立的狀態，搬移對既有「LLM 路徑下 playbook 注入時機與內容」是純位置調換、無行為差異；`test_run_agent_playbook_context_placed_after_base_system_before_history` 等既有 playbook 注入測試全數通過也佐證了這點。無需修改，僅記錄供未來讀 diff 者參考「為什麼搬」。
- **`knowledge/exceptions/off-topic.md`**：6 類例句目前均為短句、無多輪對話或夾雜股票名詞的複合句（例如「今天天氣如何，順便問一下 AAPL 財報」這類混合意圖）。目前設計本來就只用「當前這一句」比對（與 playbook 一致，非 spec 要求處理混合意圖），純粹記錄為未來若要擴充語料覆蓋率時的參考方向，不影響本次驗收。

### ✅ What looks good

- Diff 精簡且 surgical：`agent.py` 新增部分（EXCEPTION_MIN_SCORE、OFF_TOPIC_REPLY、`_is_off_topic`、攔截判斷、`playbook_hits` 搬移）共約 30 行有效程式碼 + 校準註解，無無關改動。
- `EXCEPTION_MIN_SCORE` 的校準註解格式與既有 `MIN_SCORE` 註解風格完全一致（真陽性最低分／雜訊最高分／門檻落點說明），已實測核對三組分數（3.591 真陽性最低、0.384 KB 名詞問句雜訊、3.380 playbook 邊界案例）與程式碼行為一致，沒有「寫了註解但沒真的照著做」的落差。
- 攔截判斷用 `not playbook_hits and _is_off_topic(...)`（短路求值），playbook 非空時完全不執行 BM25 查詢 `EXCEPTIONS_DIR`，語意清楚且有效能上的好處（一次多餘查詢都不做）。
- `EXCEPTIONS_DIR` 與 `PLAYBOOKS_DIR` 目錄設計延續既有 ADR-0009 的「獨立目錄 + 非遞迴 glob」模式，沒有引入新的隔離機制或不一致的做法。
- `debug.py` 的 `_SOURCE_DIRS` 字典重構（取代原本的三元運算式）讓新增第三個 source 時改動最小，且是合理的漸進式重構，不算過度抽象。
- 測試涵蓋齊全且斷言精準：直接測 `_is_off_topic` 單元行為、`run_agent` 端到端固定句 + Groq 未被呼叫、trace 欄位（`outcome`/`playbook_hits`/`fast_path`/`iterations`/`tools`）、playbook 命中不誤攔、KB 名詞問句不誤攔、fast path 優先順序不受影響，共 10 個新測試，涵蓋所有需求條目與邊界案例。
- `tests/test_playbook_injection.py` 的測試句子調整有留 inline 註解說明原因並指向新測試檔，維護者不會困惑「為什麼改了一個看似無關的句子」。

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞事項；上述 🟢 皆為記錄性質，不影響本次合併。
