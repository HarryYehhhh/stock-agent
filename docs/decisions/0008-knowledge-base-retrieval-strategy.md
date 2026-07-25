# ADR-0008: 知識庫斷詞與檢索策略（BM25 + 中文 bigram，無 embeddings）

- **Date**: 2026-07-25
- **Status**: Accepted

## Context
新增本地 Markdown 知識庫，需要一個檢索器把中文（含中英混合）查詢對應到最相關的 `##` section。限制條件：
- 語料極小（種子只有 2-3 檔、數十個 section），且維護者手寫、密度高。
- 明確要求：不用 embeddings、不加重依賴、stock-agent 內自含實作、索引不落地。
- 查詢多為繁體中文短句（「道瓊怎麼查」「amc 是什麼」），內容為中英混合（heading/同義詞刻意寫中英雙語）。

需要決定兩件事：(1) 斷詞策略（中文無空白分隔）、(2) 相關性計分方式。

## Decision

### 斷詞（tokenizer）
單一輕量規則，全程 lowercase：
1. 連續的 ASCII 英數字（`[a-z0-9]+`，含底線可選）→ 一個 word token（word-level）。
2. 連續的 CJK 字元 run → 切成 **相鄰字元 bigram**（`道瓊怎麼查` → `道瓊`、`瓊怎`、`怎麼`、`麼查`）；若 run 長度為 1，輸出該單字 unigram。
3. 其他字元（標點、空白）視為分隔符，丟棄。

同一套 tokenizer 同時用於「建索引」與「查詢」，保證對稱。

### 計分（scoring）
自含實作 **BM25**（`k1=1.5`、`b=0.75`），corpus = 所有 section。
- 每個 section 的 token 流 = heading tokens + body tokens。
- **heading 加權**：heading 的 token 以較高權重計入 term frequency（實作上 heading tokens 計入 2 次），因為 heading 依編寫規則就是「使用者會問的關鍵字」。
- 回傳前依 BM25 分數排序取 top-k，並**濾掉分數 <= 0**（查詢無任何 token 命中該 section 時該 section 不回傳），使「無相關內容」自然回傳空結果。

## Consequences
- 正面：零額外依賴、實作約數十行、process 內 lazy build + `@lru_cache` 即可，語料小效能無虞；bigram 對「道瓊」「財報」這類雙字詞精準命中；中英雙語內容因 word + bigram 混合 token 都能覆蓋。
- 正面：無 embeddings、無模型下載、無網路，測試可完全離線、決定性（同輸入同輸出），易寫斷言。
- 負面 / trade-off：bigram 對單字詞（len==1 CJK）退化成 unigram，精度較低——可接受，因查詢多為多字詞。
- 負面 / trade-off：無語意泛化（同義但字面不同且未寫進內文者查不到）——用「編寫規則要求中英同義詞寫進內文」補償。
- 負面：heading 2x 權重是經驗值 knob，非嚴謹調參；語料小、可日後微調，不構成不可逆決策。

## Alternatives considered
- **Embeddings / 向量檢索**：語意最好，但需模型或外部 API、加重依賴、與「本地小語料、離線可測」目標衝突。明確被需求排除。
- **jieba / 中文分詞套件**：分詞更自然，但新增第三方依賴，違反「不加重依賴」；小語料下 bigram 已足夠。
- **rank_bm25 / whoosh 等現成檢索套件**：省實作，但同樣是新依賴，且自含 BM25 僅數十行，不值得引入。
- **純子字串 / 關鍵字命中計分**：最簡單，但無 idf、無長度正規化，多 section 命中時排序品質差、易被長 section 拉偏；BM25 成本相近但排序明顯更好。
