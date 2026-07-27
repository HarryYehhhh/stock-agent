# ADR-0010: 資料快照稽核以離線 LLM-judge over BM25 檢索實作

- **Date**: 2026-07-27
- **Status**: Accepted
- 相關：ADR-0008（BM25 + CJK bigram 檢索核心，本 ADR 沿用）、ADR-0009（playbook grounding pre-injection，本 ADR 為其 spec 明列的 phase 2）

## Context
使用者核心訴求「驗證 LLM 的回答依據」。phase 1（playbook grounding）只在**進 tool loop 前**注入路由指引，沒有任何機制事後檢查「bot 實際回答的內容是否真的被資料支持」。需要一個能量化 bot 幻覺 / 答非所問比例的稽核閉環。

需決定：(1) 用什麼方法判定「回答有無資料依據」；(2) 稽核在何時 / 何處跑；(3) 稽核的資料依據（haystack）從哪來、如何固化；(4) judge 讀對話的來源；(5) 產物是否進版控。

## Decision
1. **離線批次稽核，非線上即時驗證**：獨立 CLI（`python -m agent.audit dump|judge`），不進 `run_agent()` 熱路徑、不部署 Lambda、不改線上 bot。「離線」指後處理批次，仍讀 DynamoDB、仍呼叫 Groq。
2. **資料快照固化成 markdown**：把 DynamoDB（v1：earnings-calendar）dump 成 `snapshots/<date>/earnings.md`，格式相容 `retrieval.py` 的 `## ` section parser，成為稽核 haystack，同時人可 review。每 symbol 一 section（heading 得 BM25 2x 權重）。
3. **LLM-judge over BM25 檢索**：對每組 Q/A，用「問題+回答」對快照跑 `retrieve_scored`（沿用 ADR-0008/0009 檢索核心，`source_dir` 指向快照目錄），命中的 section 連同 Q/A 餵 Groq（`llama-3.3-70b-versatile`、temperature=0）判 grounded / ungrounded / no-data。檢索空命中直接判 no-data、跳過 Groq（省 token）。
4. **judge 從 DynamoDB 讀 Q/A，快照僅為 haystack + 人工 review**：conversations dump（`audit/<date>/conversations.md`）供人看，judge 的 Q/A 來源是即時 DynamoDB，避免脆弱的 markdown→Q/A parser。
5. **快照與報告 gitignore**：`snapshots/` 與 `audit/` 都不進版控。

## Consequences
- 正面：稽核完全在既有技術棧內（stdlib BM25 + 現有 Groq/boto3），零新依賴、離線可測。
- 正面：判定結果附「不被支持的聲明 + 來源 section」，把「有無依據」從人工逐則核對變成可量化輸出。
- 正面：haystack 是人可讀 md，稽核判定可被人覆核（知道 judge 看到了什麼）。
- 正面：fast-path 與 LLM 回答同源於 conversations，一視同仁被稽核。
- 負面 / trade-off：judge 讀即時對話但檢索日期快照，有時間錯位、非從固定快照完全可重現——記為報告 caveat（稽核是啟發式非證明）；「judge 讀快照」列 spec Future work。
- 負面 / trade-off：BM25 靠字面命中，query 用完全不同字面時可能漏檢而誤判 no-data——可接受（no-data 是誠實的「無法判定」而非誤報 grounded），且靠「query 含 bot 回答」提高命中。
- 負面 / trade-off：judge 是 LLM，判定本身可能有誤——temperature=0 求決定性，附來源供人覆核；非追求絕對正確而是規模化初篩。
- 負面：v1 只 earnings haystack，stock-news 型回答多落 no-data——news 列 Future work（同機制、production 表存在）。

## Alternatives considered
- **線上 runtime 即時 grounding（進 `run_agent()`）**：即時攔幻覺最理想，但侵入熱路徑、增延遲與 token、與「離線稽核」訴求不符；phase 2 先做離線量測，即時驗證留後續。否決（本 phase）。
- **Embeddings / 語意 faithfulness 模型判斷 grounding**：語意泛化最好，但需模型或外部 API、加重依賴，違反離線可測（同 ADR-0008/0009）。否決。
- **judge 從 conversations 快照 md 讀 Q/A（完全可重現）**：稽核可從固定快照重現，但需可靠的 markdown→Q/A parser，脆弱且與 dump 格式強耦合。否決（simplicity first），列 Future work。
- **快照進版控**：利於 PR review 與長期 diff，但 conversations / report 內含真實使用者對話與 chat_id，屬隱私，不宜提交；且快照是 DynamoDB 的可再生衍生物、非原始碼；本地工作目錄即可 diff review。否決，改 gitignore。
- **date 分組 earnings section（而非 symbol 分組）**：利於「本週財報」型 date query，但 judge query 已含 bot 回答（列出具體 symbol），symbol 分組即可命中；date 分組會與 symbol 分組內容重複、膨脹索引。否決，採 symbol 分組。
