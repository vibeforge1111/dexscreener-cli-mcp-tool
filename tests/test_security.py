"""Tests for security hardening: webhook validation, URL encoding, path validation, template safety, error sanitization."""
from __future__ import annotations

import pytest

from dexscreener_cli.alerts import _SafeTemplate, validate_webhook_url
from dexscreener_cli.client import _validate_path_segment
from dexscreener_cli.task_runner import _sanitize_error
from dexscreener_cli.watch_controls import _sanitize_clipboard


class TestWebhookValidation:
    def test_valid_https(self) -> None:
        url = "https://hooks.slack.com/services/T123/B456/abc"
        assert validate_webhook_url(url) == url

    def test_valid_discord_http(self) -> None:
        # Discord is in the allowed-hosts list for http.
        url = "http://discord.com/api/webhooks/123/abc"
        assert validate_webhook_url(url) == url

    def test_blocks_http_random_host(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            validate_webhook_url("http://example.com/hook")

    def test_blocks_ftp_scheme(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            validate_webhook_url("ftp://example.com/file")

    def test_blocks_localhost(self) -> None:
        with pytest.raises(ValueError, match="localhost"):
            validate_webhook_url("https://localhost:8080/admin")

    def test_blocks_127_0_0_1(self) -> None:
        with pytest.raises(ValueError, match="localhost"):
            validate_webhook_url("https://127.0.0.1/admin")

    def test_blocks_metadata_endpoint(self) -> None:
        with pytest.raises(ValueError, match="metadata"):
            validate_webhook_url("https://169.254.169.254/latest/meta-data/")

    def test_blocks_empty_hostname(self) -> None:
        with pytest.raises(ValueError, match="hostname"):
            validate_webhook_url("https:///path")


class TestPathSegmentValidation:
    def test_valid_chain_id(self) -> None:
        assert _validate_path_segment("solana", "chain_id") == "solana"

    def test_valid_evm_address(self) -> None:
        assert _validate_path_segment("0x1234abcdef", "token") == "0x1234abcdef"

    def test_valid_solana_address(self) -> None:
        addr = "So11111111111111111111111111111111111111112"
        assert _validate_path_segment(addr, "token") == addr

    def test_blocks_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _validate_path_segment("../etc/passwd", "chain_id")

    def test_blocks_query_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _validate_path_segment("solana?foo=bar", "chain_id")

    def test_blocks_slash(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _validate_path_segment("a/b", "chain_id")

    def test_blocks_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _validate_path_segment("", "chain_id")


class TestSafeTemplate:
    def test_basic_substitution(self) -> None:
        t = _SafeTemplate("$top_token on $top_chain")
        result = t.safe_substitute({"top_token": "PEPE", "top_chain": "solana"})
        assert result == "PEPE on solana"

    def test_missing_key_left_intact(self) -> None:
        t = _SafeTemplate("$known $unknown")
        result = t.safe_substitute({"known": "yes"})
        assert result == "yes $unknown"

    def test_attribute_access_blocked(self) -> None:
        # str.format() would allow {top.__class__}, but Template ignores it.
        t = _SafeTemplate("{top.__class__}")
        result = t.safe_substitute({"top": "value"})
        assert "__class__" in result  # Left as literal, not evaluated.
        assert "str" not in result


class TestSanitizeError:
    def test_strips_windows_path(self) -> None:
        msg = r"Failed to read C:\Users\admin\secrets\key.txt"
        cleaned = _sanitize_error(msg)
        assert r"C:\Users" not in cleaned
        assert "<path>" in cleaned

    def test_strips_unix_path(self) -> None:
        msg = "Error at /home/user/.config/tokens/secret.json"
        cleaned = _sanitize_error(msg)
        assert "/home/" not in cleaned
        assert "<path>" in cleaned

    def test_truncates_long_message(self) -> None:
        msg = "x" * 1000
        cleaned = _sanitize_error(msg)
        assert len(cleaned) == 500

    def test_preserves_normal_message(self) -> None:
        msg = "Connection timeout after 10 seconds"
        assert _sanitize_error(msg) == msg


class TestSanitizeClipboard:
    def test_strips_control_chars(self) -> None:
        assert _sanitize_clipboard("PEPE\x00\x01test") == "PEPEtest"

    def test_preserves_normal_text(self) -> None:
        assert _sanitize_clipboard("So1111TokenAddr") == "So1111TokenAddr"

    def test_truncates_long_payload(self) -> None:
        long = "A" * 1000
        assert len(_sanitize_clipboard(long)) == 500

    def test_preserves_newlines_and_tabs(self) -> None:
        # Newlines and tabs are fine for clipboard.
        assert _sanitize_clipboard("line1\nline2\ttab") == "line1\nline2\ttab"
