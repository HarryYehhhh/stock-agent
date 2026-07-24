"""Unit tests for agent.check_bot pure decision logic.

These tests never touch the network — no localhost:8080, ngrok, Telegram, or
Groq calls. The I/O probes (probe_local_server, probe_ngrok,
probe_telegram_webhook, probe_groq) are exercised manually via
`make check-bot`, not here.
"""

from __future__ import annotations

from agent.check_bot import (
    FAIL,
    PASS,
    WARN,
    CheckResult,
    classify_server_probe,
    exit_code,
    find_ngrok_public_url,
    format_epoch,
    is_placeholder,
    mask_key_status,
    summarize,
    webhook_info_warnings,
    webhook_url_matches,
)


class TestClassifyServerProbe:
    def test_connect_error_is_fail(self):
        status, msg = classify_server_probe(None, connect_error=True)
        assert status == FAIL
        assert "make dev" in msg

    def test_200_is_pass(self):
        status, _ = classify_server_probe(200, connect_error=False)
        assert status == PASS

    def test_404_is_pass(self):
        status, _ = classify_server_probe(404, connect_error=False)
        assert status == PASS

    def test_405_is_pass(self):
        status, _ = classify_server_probe(405, connect_error=False)
        assert status == PASS


class TestFindNgrokPublicUrl:
    def test_finds_https_tunnel_for_target_addr(self):
        tunnels = [
            {"public_url": "http://abc.ngrok.io", "config": {"addr": "http://localhost:8080"}},
            {"public_url": "https://abc.ngrok.io", "config": {"addr": "http://localhost:8080"}},
        ]
        assert find_ngrok_public_url(tunnels) == "https://abc.ngrok.io"

    def test_ignores_tunnels_for_other_ports(self):
        tunnels = [{"public_url": "https://abc.ngrok.io", "config": {"addr": "http://localhost:9999"}}]
        assert find_ngrok_public_url(tunnels) is None

    def test_no_tunnels_returns_none(self):
        assert find_ngrok_public_url([]) is None

    def test_missing_config_does_not_crash(self):
        assert find_ngrok_public_url([{"public_url": "https://abc.ngrok.io"}]) is None


class TestWebhookUrlMatches:
    def test_exact_match(self):
        assert webhook_url_matches("https://abc.ngrok.io/webhook", "https://abc.ngrok.io")

    def test_trailing_slash_on_tunnel_url(self):
        assert webhook_url_matches("https://abc.ngrok.io/webhook", "https://abc.ngrok.io/")

    def test_trailing_slash_on_registered_url(self):
        assert webhook_url_matches("https://abc.ngrok.io/webhook/", "https://abc.ngrok.io")

    def test_mismatch_different_host(self):
        assert not webhook_url_matches("https://old.ngrok.io/webhook", "https://new.ngrok.io")

    def test_missing_webhook_suffix_is_mismatch(self):
        assert not webhook_url_matches("https://abc.ngrok.io", "https://abc.ngrok.io")


class TestIsPlaceholder:
    def test_none_is_placeholder(self):
        assert is_placeholder(None)

    def test_empty_string_is_placeholder(self):
        assert is_placeholder("")

    def test_your_prefix_is_placeholder(self):
        assert is_placeholder("your_finnhub_key")

    def test_xxx_is_placeholder(self):
        assert is_placeholder("xxxYYYzzz")

    def test_changeme_is_placeholder(self):
        assert is_placeholder("replace-with-changeme-value")

    def test_normal_value_is_not_placeholder(self):
        assert not is_placeholder("gsk_abcdef1234567890")


class TestMaskKeyStatus:
    def test_placeholder_is_fail_and_masked(self):
        status, desc = mask_key_status("your_key")
        assert status == FAIL
        assert desc == "EMPTY/PLACEHOLDER"

    def test_none_is_fail(self):
        status, desc = mask_key_status(None)
        assert status == FAIL
        assert desc == "EMPTY/PLACEHOLDER"

    def test_real_value_is_pass_and_length_only(self):
        status, desc = mask_key_status("gsk_abcdef1234567890")
        assert status == PASS
        assert desc == "set (len=20)"
        assert "gsk_abcdef1234567890" not in desc


class TestWebhookInfoWarnings:
    def test_no_warnings_when_clean(self):
        assert webhook_info_warnings({"pending_update_count": 0}) == []

    def test_pending_updates_warns(self):
        warnings = webhook_info_warnings({"pending_update_count": 5})
        assert len(warnings) == 1
        assert "5" in warnings[0]

    def test_last_error_warns(self):
        warnings = webhook_info_warnings(
            {
                "pending_update_count": 0,
                "last_error_date": 1700000000,
                "last_error_message": "Connection refused",
            }
        )
        assert len(warnings) == 1
        assert "Connection refused" in warnings[0]

    def test_both_warnings_present(self):
        warnings = webhook_info_warnings(
            {
                "pending_update_count": 3,
                "last_error_date": 1700000000,
                "last_error_message": "timeout",
            }
        )
        assert len(warnings) == 2


class TestFormatEpoch:
    def test_known_epoch(self):
        assert format_epoch(0) == "1970-01-01 00:00:00 UTC"


class TestSummarizeAndExitCode:
    def test_all_pass_summary_and_exit_0(self):
        results = [CheckResult("a", PASS), CheckResult("b", PASS)]
        assert summarize(results) == "總結：全部 PASS，bot 應可正常回覆。"
        assert exit_code(results) == 0

    def test_warn_only_summary_and_exit_0(self):
        results = [CheckResult("a", PASS), CheckResult("b", WARN)]
        assert summarize(results) == "總結：0 項 FAIL、1 項 WARN。"
        assert exit_code(results) == 0

    def test_fail_summary_and_exit_1(self):
        results = [CheckResult("a", FAIL), CheckResult("b", WARN)]
        assert summarize(results) == "總結：1 項 FAIL、1 項 WARN。"
        assert exit_code(results) == 1
