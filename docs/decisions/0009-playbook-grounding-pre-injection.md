# ADR-0009: Playbook grounding 以 pre-injection 檢索取代模型自覺選路由

- **Date**: 2026-07-27
- **Status**: Accepted
- 相關：ADR-0008（BM25 + CJK bigram 斷詞策略，本 ADR 沿用其檢索核心）

## Context
Production 事件：使用者問「本週有什麼公司發布財報」，模型卻附上歷史對話裡的 AAPL 分析師評級（答非所問 + 歷史污染）。已修 IAM bug（PR #30）、加 prompt 防污染指示（PR #31）、清污染歷史，但這些都是「請模型自己注意」的補丁。

根因：工具選擇策略是一大段塞在 `SYSTEM_INSTRUCTION` 的文字，模型每題都要自行從中挑對的規則，判斷面過大、且容易被對話歷史帶偏。需要一個結構性、不依賴模型自覺的機制。

需要決定：(1) 指引要放哪、以什麼形式存在；(2) 什麼時候、由誰把對的指引送到模型面前；(3) 與既有術語知識庫（`knowledge/*.md` + `search_knowledge_base`）的關係。

## Decision
1. **把工具路由指引從 `SYSTEM_INSTRUCTION` 拆成分類 playbook**（`knowledge/playbooks/` 三份 md：market-trends / earnings / stock-news），每個 `## ` section = 一類問題，含 觸發語 / 工具指引 / 回覆要求 / 禁止。playbook 是這些規則的**單一真相來源**；`SYSTEM_INSTRUCTION` 只留通用行為（語言、格式、術語、知識庫引用規範、防歷史污染）。
2. **Pre-injection（程式碼驅動，非模型驅動）**：在 `run_agent()` 進 tool loop 前，用**當前 user message**（不含 history）對 playbook 索引跑 BM25，取 `top_k=2` 且分數 `>= MIN_SCORE` 的 section，組成 system context 注入（附 `檔名#標題` 來源）。未達門檻則不注入、fallback 到現行行為。
3. **兩套獨立索引**：playbook 索引（`knowledge/playbooks/`）與術語知識庫索引（`knowledge/*.md`，`search_knowledge_base` 用）各自 `lru_cache`、互不污染。沿用 `retrieval.py` 的 BM25 + CJK bigram 核心（ADR-0008），僅參數化索引來源目錄。
4. **斷詞維持 ADR-0008 的 CJK bigram（stdlib-only）**，不引入 jieba / CKIP。

## Consequences
- 正面：模型每題只看到「這一類」的指引，判斷面大幅縮小；正確路由由程式碼保證，不再靠模型自覺。
- 正面：路由規則集中在人類可讀、可檢索、可測的 md，改規則只改一處，測試（含 substring 斷言）鎖住內容不被誤刪。
- 正面：注入基於當前 user message 而非 history，結構上抑制歷史污染。
- 正面：零新依賴、離線可測、決定性（延續 ADR-0008 的優點）。
- 負面 / trade-off：`MIN_SCORE` 是未正規化 BM25 分數上的經驗門檻，需實測校準；設太高會漏注入、太低會誤注入。以測試鎖行為（正例注入、負例不注入）緩解。
- 負面 / trade-off：BM25 靠字面命中，若使用者用完全沒寫進「觸發語」的說法問，可能不注入而 fallback——可接受（fallback 即現行行為，非退步），且可持續補觸發語。
- 負面：多一次開場檢索（process 內小語料，成本可忽略）。

## Alternatives considered
- **維持指引在 `SYSTEM_INSTRUCTION`（現狀）**：正是造成事件的模式，模型判斷面過大、易被歷史帶偏。否決。
- **做成第 7 個工具讓模型自己呼叫 playbook**：仍依賴模型「知道要先查 playbook」，等同把失敗點往前移一步；且模型可能跳過。與「不依賴模型自覺」目標衝突。否決。
- **Embeddings / 向量檢索做語意匹配**：語意泛化最好，但需模型或外部 API、加重依賴，違反離線可測目標（同 ADR-0008）。否決。
- **jieba / CKIP 中文分詞**：分詞更自然，但新增第三方依賴；小語料下 bigram 已足夠（同 ADR-0008）。否決，維持 bigram。
- **playbook 與術語知識庫共用單一索引**：會讓兩類內容互相干擾排名、破壞 `search_knowledge_base` 既有行為。否決，改雙索引隔離。
