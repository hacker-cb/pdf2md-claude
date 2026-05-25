"""Unit tests for the claude_cli_api module (claude -p backend)."""

import json
import subprocess
from unittest.mock import patch

import pytest

from pdf2md_claude.claude_cli_api import (
    ClaudeCliApi,
    resolve_claude_bin,
    _strip_cache_control,
    claude_cli_available,
)
from pdf2md_claude.models import OPUS_4_7


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stream_json(events: list[dict]) -> str:
    """Render a list of events as newline-delimited JSON (CLI stdout shape)."""
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _result_event(**overrides) -> dict:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "# Hello\n\nWorld.",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 34,
            "cache_creation_input_tokens": 5000,
            "cache_read_input_tokens": 0,
        },
    }
    base.update(overrides)
    return base


def _ok_stdout() -> str:
    return _stream_json([
        {"type": "system", "subtype": "init", "apiKeySource": "none"},
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "let me think"},
            {"type": "text", "text": "# Hello\n\nWorld."},
        ]}},
        _result_event(),
    ])


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _api() -> ClaudeCliApi:
    # Pass claude_bin explicitly so tests stay hermetic regardless of any
    # PDF2MD_CLAUDE_BIN value in the developer's environment.
    with patch("pdf2md_claude.claude_cli_api.claude_cli_available", return_value=True):
        return ClaudeCliApi(OPUS_4_7, claude_bin="claude", max_retries=3)


_DUMMY_MESSAGES = [{"role": "user", "content": [
    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "QUJD"}},
    {"type": "text", "text": "Convert this."},
]}]


# ---------------------------------------------------------------------------
# _strip_cache_control
# ---------------------------------------------------------------------------


class TestStripCacheControl:
    def test_removes_nested_cache_control(self):
        content = [
            {"type": "document", "source": {"type": "base64"}, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "hi"},
        ]
        assert _strip_cache_control(content) == [
            {"type": "document", "source": {"type": "base64"}},
            {"type": "text", "text": "hi"},
        ]

    def test_passthrough_without_cache_control(self):
        content = [{"type": "text", "text": "hi"}]
        assert _strip_cache_control(content) == content


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_raises_when_cli_missing(self):
        with patch("pdf2md_claude.claude_cli_api.claude_cli_available", return_value=False):
            with pytest.raises(RuntimeError, match="not found on PATH"):
                ClaudeCliApi(OPUS_4_7)

    def test_use_cache_warns(self, caplog):
        with patch("pdf2md_claude.claude_cli_api.claude_cli_available", return_value=True):
            ClaudeCliApi(OPUS_4_7, use_cache=True)
        assert any("no effect" in r.message for r in caplog.records)

    def test_model_property(self):
        assert _api().model is OPUS_4_7

    def test_cached_block_is_noop(self):
        block = {"type": "document", "source": {}}
        assert _api().cached_block(block) is block

    def test_claude_cli_available_uses_which(self):
        with patch("pdf2md_claude.claude_cli_api.shutil.which", return_value="/usr/bin/claude") as w:
            assert claude_cli_available("claude") is True
            w.assert_called_once_with("claude")
        with patch("pdf2md_claude.claude_cli_api.shutil.which", return_value=None):
            assert claude_cli_available("claude") is False

    def test_resolve_claude_bin_precedence(self, monkeypatch):
        monkeypatch.delenv("PDF2MD_CLAUDE_BIN", raising=False)
        assert resolve_claude_bin(None) == "claude"
        assert resolve_claude_bin("/x/claude") == "/x/claude"
        monkeypatch.setenv("PDF2MD_CLAUDE_BIN", "/opt/claude")
        assert resolve_claude_bin(None) == "/opt/claude"
        assert resolve_claude_bin("/x/claude") == "/x/claude"  # explicit arg wins over env


# ---------------------------------------------------------------------------
# command + stdin construction
# ---------------------------------------------------------------------------


class TestCommandBuilding:
    def test_command_has_expected_flags(self):
        cmd = _api()._build_command("SYS PROMPT", thinking=None)
        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert cmd[cmd.index("--input-format") + 1] == "stream-json"
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert cmd[cmd.index("--model") + 1] == OPUS_4_7.model_id
        assert cmd[cmd.index("--system-prompt") + 1] == "SYS PROMPT"
        assert cmd[cmd.index("--tools") + 1] == ""
        assert "--no-session-persistence" in cmd
        assert "--effort" not in cmd

    def test_thinking_adds_effort(self):
        cmd = _api()._build_command("x", thinking={"type": "enabled", "budget_tokens": 1})
        assert cmd[cmd.index("--effort") + 1] == "high"

    def test_stdin_wraps_messages_and_strips_cache_control(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
        ]}]
        stdin = _api()._build_stdin(msgs)
        lines = stdin.strip().splitlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj == {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_success_parses_result_event(self):
        api = _api()
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", return_value=_FakeProc(_ok_stdout())) as run:
            resp = api.send_message("sys", _DUMMY_MESSAGES, retry_context="pages 1-1")
        assert resp.markdown == "# Hello\n\nWorld."
        assert resp.input_tokens == 12
        assert resp.output_tokens == 34
        assert resp.cache_creation_tokens == 5000
        assert resp.cache_read_tokens == 0
        assert resp.stop_reason == "end_turn"
        # stdin carried the document block
        _, kwargs = run.call_args
        assert "application/pdf" in kwargs["input"]

    def test_thinking_delta_callback_invoked(self):
        api = _api()
        seen = []
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", return_value=_FakeProc(_ok_stdout())):
            api.send_message("sys", _DUMMY_MESSAGES, thinking={"type": "enabled"}, on_thinking_delta=seen.append)
        assert "".join(seen) == "let me think"

    def test_fallback_to_text_blocks_when_no_result_string(self):
        stdout = _stream_json([
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "FALLBACK"}]}},
            _result_event(result=""),
        ])
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", return_value=_FakeProc(stdout)):
            resp = _api().send_message("sys", _DUMMY_MESSAGES)
        assert resp.markdown == "FALLBACK"

    def test_permanent_api_error_raises(self):
        stdout = _stream_json([
            _result_event(is_error=True, subtype="error_during_execution",
                          api_error_status=400, result="bad request"),
        ])
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", return_value=_FakeProc(stdout)):
            with pytest.raises(RuntimeError, match="bad request"):
                _api().send_message("sys", _DUMMY_MESSAGES)

    def test_transient_api_error_retried_then_succeeds(self):
        err_stdout = _stream_json([
            _result_event(is_error=True, subtype="error_during_execution",
                          api_error_status=529, result="overloaded"),
        ])
        procs = [_FakeProc(err_stdout), _FakeProc(_ok_stdout())]
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", side_effect=procs), \
             patch("pdf2md_claude.claude_cli_api.time.sleep"):
            resp = _api().send_message("sys", _DUMMY_MESSAGES)
        assert resp.markdown == "# Hello\n\nWorld."

    def test_no_result_event_retries_then_raises(self):
        with patch("pdf2md_claude.claude_cli_api.subprocess.run",
                   return_value=_FakeProc(stdout="", stderr="boom", returncode=1)), \
             patch("pdf2md_claude.claude_cli_api.time.sleep"):
            with pytest.raises(RuntimeError, match="no result"):
                _api().send_message("sys", _DUMMY_MESSAGES)

    def test_timeout_is_retryable(self):
        timeout_exc = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        with patch("pdf2md_claude.claude_cli_api.subprocess.run",
                   side_effect=[timeout_exc, _FakeProc(_ok_stdout())]), \
             patch("pdf2md_claude.claude_cli_api.time.sleep"):
            resp = _api().send_message("sys", _DUMMY_MESSAGES)
        assert resp.markdown == "# Hello\n\nWorld."

    def test_max_retries_exhausted_raises(self):
        timeout_exc = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        with patch("pdf2md_claude.claude_cli_api.subprocess.run", side_effect=timeout_exc), \
             patch("pdf2md_claude.claude_cli_api.time.sleep"):
            with pytest.raises(RuntimeError, match="timed out"):
                _api().send_message("sys", _DUMMY_MESSAGES)
