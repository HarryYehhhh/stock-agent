"""LLM-judge existing conversations against an earnings snapshot (ADR-0010).

For each Q/A pair (paired straight from DynamoDB conversations, see D2/D3 in
docs/specs/snapshot-audit.md): retrieve BM25 hits for "question + answer"
against snapshots/<date>/ (the earnings haystack). Empty hits short-circuit to
verdict "no-data" without calling Groq (token savings). Non-empty hits are
judged by Groq (temperature=0) into grounded / ungrounded / no-data, with
unsupported claims and a Traditional-Chinese reason.

Run with:
    PYTHONPATH=src uv run python -m agent.audit judge
    PYTHONPATH=src uv run python -m agent.audit judge --date 2026-07-27 --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from agent.agent import MODEL, _client
from agent.audit.paths import AUDIT_DIR, SNAPSHOTS_DIR
from agent.config import aws_region, conversations_table
from agent.db.download_remote_store import _scan_all
from agent.knowledge.retrieval import Section, retrieve_scored

log = logging.getLogger(__name__)

# Prompt token-saving character caps (D5: left to generator). Applied to the
# question/answer text embedded in the judge prompt and to each retrieved
# section's content -- both can otherwise be long enough to dominate cost.
# 2000 (spec default) under-covers real earnings-week peak volume (see
# generator DoD real-dump measurement: P95 ~4.5k chars on 2026-07-24, a
# multi-mega-cap earnings week). 2500 is the contract's authorized ceiling
# (docs/contracts/news-section-granularity.md Deliverable B) and stays
# within the token budget analyzed in the spec (~4,000 token/request,
# well under Groq TPM 12000); it does not fully close the gap on the most
# extreme earnings-week days but meaningfully reduces truncation vs. 1000.
MAX_TEXT_CHARS = 800
MAX_SECTION_CHARS = 2500

_VERDICTS = ("grounded", "ungrounded", "no-data")

_JUDGE_SYSTEM_PROMPT = (
    "你是財報行事曆機器人的稽核員。根據使用者問題、機器人回答、以及檢索到的財報資料片段，"
    "判斷回答內容是否有資料依據。只能輸出一個 JSON 物件，不要有其他文字或說明：\n"
    '{"verdict": "grounded|ungrounded|no-data", '
    '"unsupported_claims": ["不被支持的聲明1", "..."], '
    '"reason": "<一句話繁體中文說明>"}\n'
    "verdict 判定規則：\n"
    "- grounded：回答中的事實聲明都被資料片段支持；或回答本身沒有可查證的事實聲明"
    "（例如反問澄清、拒答、單純問候）。\n"
    "- ungrounded：回答中至少有一項事實聲明不被資料片段支持，需列在 unsupported_claims。\n"
    "- no-data：資料片段內容與回答無關，不足以判定回答正確與否。"
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--date", default=None, help="要稽核的快照日期 YYYY-MM-DD（預設今天，須已 dump 過）"
    )
    parser.add_argument("--chat-id", default=None, help="只稽核指定 chat_id 的對話")
    parser.add_argument(
        "--limit", type=int, default=10, help="最多稽核幾筆 Q/A，取最新（預設 10）"
    )
    parser.add_argument(
        "--top-k", type=int, default=3, help="每筆 Q/A 檢索快照的最多命中片段數（預設 3）"
    )


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…（截斷）"


def _pair_qa(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """items must already be sorted ascending by timestamp for a single
    chat_id. Pairs each user message with the *next* assistant message
    (D3): a user with no following assistant (end of list, or superseded by
    another user before any assistant arrives) is dropped; non-user/assistant
    roles are ignored without breaking a pending pairing."""
    pairs: list[dict[str, Any]] = []
    pending_user: dict[str, Any] | None = None
    for item in items:
        role = item.get("role")
        if role == "user":
            pending_user = item
        elif role == "assistant":
            if pending_user is not None:
                pairs.append(
                    {
                        "chat_id": item["chat_id"],
                        "question": pending_user["content"],
                        "answer": item["content"],
                        "timestamp": item["timestamp"],
                    }
                )
                pending_user = None
    return pairs


def _fetch_qa_pairs(
    chat_id_filter: str | None, limit: int
) -> list[dict[str, Any]]:
    table_name = conversations_table()
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    items = _scan_all(ddb.Table(table_name))

    by_chat: dict[str, list[dict]] = {}
    for item in items:
        by_chat.setdefault(item["chat_id"], []).append(item)

    pairs: list[dict[str, Any]] = []
    for chat_id, chat_items in by_chat.items():
        chat_items.sort(key=lambda it: it["timestamp"])
        pairs.extend(_pair_qa(chat_items))

    if chat_id_filter:
        pairs = [p for p in pairs if p["chat_id"] == chat_id_filter]

    pairs.sort(key=lambda p: p["timestamp"])
    return pairs[-limit:] if limit > 0 else pairs


def _extract_json(text: str) -> dict[str, Any] | None:
    """Tolerant parse: scan for the first balanced {...} run and json.loads
    it, ignoring any surrounding prose the model may have added."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _ask_judge(
    question: str, answer: str, hits: list[tuple[Section, float]]
) -> tuple[str, list[str], str]:
    sections_block = "\n\n".join(
        f"[來源：{section.source}]\n{_truncate(section.content, MAX_SECTION_CHARS)}"
        for section, _score in hits
    )
    user_content = (
        f"使用者問題：{_truncate(question, MAX_TEXT_CHARS)}\n"
        f"機器人回答：{_truncate(answer, MAX_TEXT_CHARS)}\n\n"
        f"檢索到的資料片段：\n{sections_block}"
    )
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
    )
    usage = getattr(response, "usage", None)
    if usage:
        log.info(
            "groq usage (audit judge): input=%s output=%s",
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )

    content = (response.choices[0].message.content or "").strip()
    parsed = _extract_json(content)
    if parsed is None:
        return "no-data", [], f"judge 回應無法解析為 JSON，原始內容：{_truncate(content, 200)}"

    verdict = parsed.get("verdict")
    if verdict not in _VERDICTS:
        verdict = "no-data"
    claims = parsed.get("unsupported_claims") or []
    if not isinstance(claims, list):
        claims = [str(claims)]
    reason = str(parsed.get("reason") or "")
    return verdict, [str(c) for c in claims], reason


def _judge_pair(pair: dict[str, Any], source_dir: Path, top_k: int) -> dict[str, Any]:
    query = f"{pair['question']} {pair['answer']}"
    hits = retrieve_scored(query, top_k=top_k, source_dir=source_dir)
    base = {
        "chat_id": pair["chat_id"],
        "question": pair["question"],
        "answer": pair["answer"],
        "hits": [(section.source, score) for section, score in hits],
    }
    if not hits:
        return {
            **base,
            "verdict": "no-data",
            "unsupported_claims": [],
            "reason": "檢索無命中，快照缺乏相關資料可據以判定。",
        }

    verdict, unsupported_claims, reason = _ask_judge(pair["question"], pair["answer"], hits)
    return {**base, "verdict": verdict, "unsupported_claims": unsupported_claims, "reason": reason}


def _write_report(date_str: str, results: list[dict[str, Any]]) -> Path:
    counts = Counter(r["verdict"] for r in results)
    lines = [
        f"# 稽核報告 {date_str}",
        "",
        f"- 產生時間：{datetime.now(timezone.utc).isoformat()}",
        f"- 引用快照日期：{date_str}",
        f"- model：{MODEL}",
        (
            f"- 統計：grounded={counts.get('grounded', 0)} "
            f"ungrounded={counts.get('ungrounded', 0)} "
            f"no-data={counts.get('no-data', 0)}（共 {len(results)} 筆）"
        ),
        "",
        "已知限制（稽核為啟發式，非證明，判讀時請留意）：",
        (
            "- 時間錯位：Q/A 讀自即時 DynamoDB，檢索依據為固定日期快照，"
            "兩者可能有時間差（spec D2）。"
        ),
        (
            "- haystack 範圍：快照現含 earnings + news（ADR-0011/0012），"
            "一般新聞涵蓋近 ~14 天、市場摘要涵蓋近 ~2 天，"
            "皆為 dump 當下 NewsTable 的內容。"
        ),
        (
            "- 時間錯位：若對話問的新聞早於快照保留期（一般新聞 >14 天 / "
            "市場摘要 >2 天前）已被 TTL 淘汰、快照無此料，此時判 no-data "
            "屬「快照缺料」，非 bot 幻覺，請搭配 verdict 細節與檢索來源自行判讀。"
        ),
        (
            "- news 節現為 symbol×日期粒度（ADR-0012），命中節的完整證據"
            "（至 MAX_SECTION_CHARS 取證上限）會進 judge prompt，"
            "不再因單一大節被截斷而漏掉仍在保留窗口內的舊證據。"
        ),
        (
            "- 殘留尾端：單一 symbol×日期節新聞量特大（> MAX_SECTION_CHARS 取證上限）時，"
            "該日最舊數則仍可能被截；實測顯示財報週熱門 symbol（多檔大型股同週報財報）"
            "較常觸發此情況（非僅單一極端案例），仍非 TTL 時間錯位、非 bot 幻覺。"
        ),
        "",
    ]
    for i, r in enumerate(results, start=1):
        lines.append(f"## #{i} chat_id={r['chat_id']}")
        lines.append(f"- 問題：{_truncate(r['question'], MAX_TEXT_CHARS)}")
        lines.append(f"- 回答：{_truncate(r['answer'], MAX_TEXT_CHARS)}")
        lines.append(f"- verdict：{r['verdict']}")
        if r["hits"]:
            sources = "; ".join(f"{src}（{score:.3f}）" for src, score in r["hits"])
        else:
            sources = "（無命中）"
        lines.append(f"- 檢索來源：{sources}")
        lines.append(f"- unsupported_claims：{r['unsupported_claims']}")
        lines.append(f"- reason：{r['reason']}")
        lines.append("")

    out_dir = AUDIT_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.md"
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out_path


def run(args: argparse.Namespace) -> int:
    date_str = args.date or date.today().isoformat()
    source_dir = SNAPSHOTS_DIR / date_str
    snapshot_path = source_dir / "earnings.md"
    if not snapshot_path.is_file():
        print(
            f"找不到快照 {snapshot_path}，請先執行"
            f" `python -m agent.audit dump --date {date_str}`",
            file=sys.stderr,
        )
        return 1

    try:
        pairs = _fetch_qa_pairs(args.chat_id, args.limit)
    except Exception as exc:
        print(f"DynamoDB scan failed: {exc}", file=sys.stderr)
        return 1

    results = [_judge_pair(pair, source_dir, args.top_k) for pair in pairs]
    report_path = _write_report(date_str, results)
    print(f"judged {len(results)} Q/A -> {report_path}")
    return 0
