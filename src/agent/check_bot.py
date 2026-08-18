"""Local-first health check for the Telegram bot pipeline.

Run via `make check-bot`. Checks (in order): local uvicorn server, ngrok
tunnel, Telegram webhook registration/health, required secrets, and a live
Groq API call. Every check is read-only — no DB writes, no Telegram
messages sent.

Pure decision logic (no I/O) is separated from the network probes so it can
be unit-tested without touching localhost, Telegram, or Groq. See
tests/test_check_bot.py.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from groq import Groq

LOCAL_SERVER_URL = "http://localhost:8080"
NGROK_API_URL = "http://localhost:4040/api/tunnels"
TELEGRAM_API = "https://api.telegram.org"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
HTTP_TIMEOUT = 10.0

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

PLACEHOLDER_MARKERS = ("your_", "xxx", "changeme")

REQUIRED_SECRET_KEYS = [
    "FINNHUB_API_KEY",
    "GROQ_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
]


@dataclass
class CheckResult:
    name: str
    status: str
    lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable, no I/O)
# ---------------------------------------------------------------------------


def classify_server_probe(status_code: int | None, connect_error: bool) -> tuple[str, str]:
    """Map a raw HTTP probe outcome to PASS/FAIL for the local server check."""
    if connect_error:
        return FAIL, "無法連線到 http://localhost:8080 — uvicorn 沒在跑，執行 make dev"
    return PASS, f"http://localhost:8080 有回應（HTTP {status_code}）"


def find_ngrok_public_url(tunnels: list[dict], target_addr: str = "localhost:8080") -> str | None:
    """Pick the https public_url among ngrok tunnels pointing at target_addr."""
    for t in tunnels:
        config = t.get("config") or {}
        addr = str(config.get("addr", ""))
        public_url = str(t.get("public_url", ""))
        if target_addr in addr and public_url.startswith("https://"):
            return public_url
    return None


def normalize_url(url: str) -> str:
    return url.rstrip("/")


def webhook_url_matches(registered_url: str, tunnel_public_url: str) -> bool:
    expected = normalize_url(tunnel_public_url) + "/webhook"
    return normalize_url(registered_url) == expected


def is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def mask_key_status(value: str | None) -> tuple[str, str]:
    """Return (status, description) for a secret value, never echoing it."""
    if is_placeholder(value):
        return FAIL, "EMPTY/PLACEHOLDER"
    return PASS, f"set (len={len(value)})"


def format_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def webhook_info_warnings(info: dict) -> list[str]:
    """Given Telegram getWebhookInfo `result`, return WARN messages (empty if healthy)."""
    warnings: list[str] = []
    pending = info.get("pending_update_count", 0)
    if pending:
        warnings.append(f"pending_update_count={pending}（訊息可能塞住）")
    last_error_date = info.get("last_error_date")
    if last_error_date:
        when = format_epoch(last_error_date)
        message = info.get("last_error_message", "unknown")
        warnings.append(f"最近錯誤 {when}: {message}")
    return warnings


def summarize(results: list[CheckResult]) -> str:
    fails = sum(1 for r in results if r.status == FAIL)
    warns = sum(1 for r in results if r.status == WARN)
    if fails == 0 and warns == 0:
        return "總結：全部 PASS，bot 應可正常回覆。"
    return f"總結：{fails} 項 FAIL、{warns} 項 WARN。"


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


# ---------------------------------------------------------------------------
# Network probes (I/O — not unit-tested; exercised via `make check-bot`)
# ---------------------------------------------------------------------------


def probe_local_server() -> CheckResult:
    try:
        r = httpx.get(LOCAL_SERVER_URL, timeout=HTTP_TIMEOUT)
        status, msg = classify_server_probe(r.status_code, connect_error=False)
    except httpx.RequestError:
        status, msg = classify_server_probe(None, connect_error=True)
    except Exception as e:
        status, msg = FAIL, f"檢查時發生未預期錯誤: {type(e).__name__}: {e}"
    return CheckResult("本地 server (localhost:8080)", status, [msg])


def probe_ngrok() -> tuple[CheckResult, str | None]:
    """Returns (result, public_url or None)."""
    try:
        r = httpx.get(NGROK_API_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        tunnels = r.json().get("tunnels", [])
    except httpx.RequestError:
        return CheckResult("ngrok tunnel", FAIL, ["無法連線到 http://localhost:4040 — ngrok 沒開"]), None
    except Exception as e:
        return CheckResult("ngrok tunnel", FAIL, [f"ngrok API 回應異常: {type(e).__name__}: {e}"]), None

    public_url = find_ngrok_public_url(tunnels)
    if public_url is None:
        return (
            CheckResult(
                "ngrok tunnel", FAIL, ["找不到指向 localhost:8080 的 https tunnel — ngrok 沒開或設定錯誤"]
            ),
            None,
        )
    return CheckResult("ngrok tunnel", PASS, [f"目前 public_url: {public_url}"]), public_url


def probe_telegram_webhook(token: str, tunnel_url: str | None) -> CheckResult:
    try:
        r = httpx.get(f"{TELEGRAM_API}/bot{token}/getWebhookInfo", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        info = r.json().get("result", {})
    except httpx.HTTPStatusError as e:
        return CheckResult("Telegram webhook", FAIL, [f"getWebhookInfo 失敗: HTTP {e.response.status_code}"])
    except httpx.RequestError as e:
        return CheckResult("Telegram webhook", FAIL, [f"無法連線 Telegram API: {type(e).__name__}"])
    except Exception as e:
        return CheckResult("Telegram webhook", FAIL, [f"檢查時發生未預期錯誤: {type(e).__name__}: {e}"])

    lines: list[str] = []
    status = PASS

    registered_url = info.get("url", "")
    if not registered_url:
        status = WARN
        lines.append("目前沒有註冊任何 webhook URL")
    elif tunnel_url is None:
        status = WARN
        lines.append(f"已註冊 webhook: {registered_url}（無法比對，因為 ngrok tunnel 未偵測到）")
    elif not webhook_url_matches(registered_url, tunnel_url):
        status = WARN
        lines.append("webhook URL 與目前 tunnel 不符，需重跑 make set-webhook")
        lines.append(f"  已註冊: {registered_url}")
        lines.append(f"  目前 tunnel: {normalize_url(tunnel_url)}/webhook")
    else:
        lines.append(f"webhook URL 與 tunnel 一致: {registered_url}")

    extra_warnings = webhook_info_warnings(info)
    if extra_warnings:
        status = WARN
        lines.extend(extra_warnings)

    return CheckResult("Telegram webhook", status, lines)


def check_secrets() -> CheckResult:
    lines = []
    overall = PASS
    for key in REQUIRED_SECRET_KEYS:
        value = os.environ.get(key)
        status, desc = mask_key_status(value)
        lines.append(f"{key}: {desc}")
        if status == FAIL:
            overall = FAIL
    return CheckResult("金鑰", overall, lines)


def probe_groq(model: str) -> CheckResult:
    api_key = os.environ.get("GROQ_API_KEY")
    if is_placeholder(api_key):
        return CheckResult("Groq API", FAIL, ["GROQ_API_KEY 缺失或為 placeholder，略過呼叫"])
    try:
        client = Groq(api_key=api_key, timeout=HTTP_TIMEOUT)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return CheckResult("Groq API", PASS, [f"model={model} 回應正常"])
    except Exception as e:
        return CheckResult("Groq API", FAIL, [f"{type(e).__name__}: {str(e)[:200]}"])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_checks() -> list[CheckResult]:
    """Run every check. Each probe already catches its own exceptions, but we
    guard again here so one unexpected failure never stops the rest."""
    results: list[CheckResult] = []

    try:
        results.append(probe_local_server())
    except Exception as e:
        results.append(CheckResult("本地 server (localhost:8080)", FAIL, [f"未預期錯誤: {e}"]))

    tunnel_url: str | None = None
    try:
        ngrok_result, tunnel_url = probe_ngrok()
        results.append(ngrok_result)
    except Exception as e:
        results.append(CheckResult("ngrok tunnel", FAIL, [f"未預期錯誤: {e}"]))

    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if is_placeholder(token):
            results.append(CheckResult("Telegram webhook", FAIL, ["TELEGRAM_BOT_TOKEN 缺失或為 placeholder，略過檢查"]))
        else:
            results.append(probe_telegram_webhook(token, tunnel_url))
    except Exception as e:
        results.append(CheckResult("Telegram webhook", FAIL, [f"未預期錯誤: {e}"]))

    try:
        results.append(check_secrets())
    except Exception as e:
        results.append(CheckResult("金鑰", FAIL, [f"未預期錯誤: {e}"]))

    try:
        model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        results.append(probe_groq(model))
    except Exception as e:
        results.append(CheckResult("Groq API", FAIL, [f"未預期錯誤: {e}"]))

    return results


def print_results(results: list[CheckResult]) -> None:
    for r in results:
        print(f"[{r.status}] {r.name}")
        for line in r.lines:
            print(f"  - {line}")
    print()
    print(summarize(results))


def main() -> int:
    results = run_checks()
    print_results(results)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
