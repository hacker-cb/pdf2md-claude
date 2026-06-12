"""Tests for CLI argument parsing and subcommand dispatch.

These tests exercise the argument parser structure — they do NOT call
the Anthropic API or touch real PDFs.
"""

from __future__ import annotations

import pytest

import argparse
import logging

from pdf2md_claude.cli import (
    _API_KEY_ENV,
    _LEGACY_API_KEY_ENV,
    _build_parser,
    _resolve_api_key,
    _resolve_model,
    main,
)
from pdf2md_claude.models import MODELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(argv: list[str]):
    """Parse *argv* using the CLI parser and return the namespace."""
    parser = _build_parser()
    return parser.parse_args(argv)


def _parse_fails(argv: list[str]):
    """Assert that parsing *argv* raises SystemExit (argparse error)."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)


# ---------------------------------------------------------------------------
# convert subcommand
# ---------------------------------------------------------------------------


class TestConvertArgs:
    """Argument parsing for the ``convert`` subcommand."""

    def test_minimal(self):
        args = _parse(["convert", "doc.pdf"])
        assert args.command == "convert"
        assert len(args.pdfs) == 1
        assert str(args.pdfs[0]) == "doc.pdf"

    def test_multiple_pdfs(self):
        args = _parse(["convert", "a.pdf", "b.pdf", "c.pdf"])
        assert len(args.pdfs) == 3

    def test_all_options(self):
        args = _parse([
            "convert", "doc.pdf",
            "-v", "-f",
            "-o", "/tmp/out",
            "--model", "sonnet",
            "--pages-per-chunk", "5",
            "--max-pages", "20",
            "--cache",
            "--retries", "3",
            "--rules", "my.rules",
            "--no-images",
            "--image-mode", "snap",
            "--image-dpi", "300",
            "--strip-ai-descriptions",
        ])
        assert args.verbose is True
        assert args.force is True
        assert str(args.output_dir) == "/tmp/out"
        assert args.model == "sonnet"
        assert args.pages_per_chunk == 5
        assert args.max_pages == 20
        assert args.cache is True
        assert args.retries == 3
        assert str(args.rules) == "my.rules"
        assert args.no_images is True
        assert args.image_mode == "snap"
        assert args.image_dpi == 300
        assert args.strip_ai_descriptions is True

    def test_defaults(self):
        args = _parse(["convert", "doc.pdf"])
        assert args.verbose is False
        assert args.force is False
        assert args.output_dir is None
        assert args.cache is False
        assert args.max_pages is None
        assert args.rules is None
        assert args.no_images is False
        assert args.strip_ai_descriptions is False

    def test_requires_at_least_one_pdf(self):
        _parse_fails(["convert"])

    def test_from_merge(self):
        """--from merge parses correctly."""
        args = _parse(["convert", "doc.pdf", "--from", "merge"])
        assert args.command == "convert"
        assert args.from_step == "merge"
        assert len(args.pdfs) == 1

    def test_from_merge_default(self):
        """Without --from, from_step defaults to None."""
        args = _parse(["convert", "doc.pdf"])
        assert args.from_step is None

    def test_from_merge_with_image_options(self):
        """--from merge works with image processing options."""
        args = _parse([
            "convert", "doc.pdf", "--from", "merge",
            "--no-images",
            "--image-mode", "debug",
        ])
        assert args.from_step == "merge"
        assert args.no_images is True
        assert args.image_mode == "debug"

    def test_from_invalid_step(self):
        """Invalid --from value is rejected."""
        _parse_fails(["convert", "doc.pdf", "--from", "invalid"])

    def test_from_merge_help_text_accurate(self, capsys):
        """--from help text should mention that post-processing may call API."""
        from pdf2md_claude.cli import _build_parser
        
        parser = _build_parser()
        # Get help text for convert command
        try:
            parser.parse_args(["convert", "--help"])
        except SystemExit:
            pass  # --help causes sys.exit
        
        captured = capsys.readouterr()
        help_text = captured.out
        help_text_lower = help_text.lower()
        
        # Verify help text mentions chunk conversion vs post-processing distinction
        assert "--from" in help_text
        assert "merge" in help_text
        # Should explicitly mention BOTH that chunk conversion is skipped AND post-processing may call API
        assert "chunk conversion" in help_text_lower, "Help text should mention 'chunk conversion'"
        assert "post-processing" in help_text_lower, "Help text should mention 'post-processing'"
        # Should mention table fixing as an example of post-processing that calls API
        assert "table" in help_text_lower, "Help text should mention table fixing as example"


class TestResolveModel:
    """``--model`` accepts both an alias and a full model_id."""

    def test_alias_passthrough(self):
        assert _resolve_model("opus") == "opus"
        assert _resolve_model("sonnet") == "sonnet"
        assert _resolve_model("haiku") == "haiku"

    def test_explicit_pin_aliases(self):
        assert _resolve_model("opus-4-7") == "opus-4-7"
        assert _resolve_model("opus-4-6") == "opus-4-6"

    def test_model_id_resolves_to_alias(self):
        # The alias chosen for a model_id is the first MODELS entry whose
        # config matches; both "opus" and "opus-4-7" share OPUS_4_7, so
        # claude-opus-4-7 maps to "opus".
        assert _resolve_model("claude-opus-4-7") == "opus"
        assert _resolve_model("claude-opus-4-6") == "opus-4-6"

    def test_resolved_alias_points_to_expected_model_id(self):
        for input_value, expected_model_id in [
            ("opus", "claude-opus-4-7"),
            ("opus-4-7", "claude-opus-4-7"),
            ("claude-opus-4-7", "claude-opus-4-7"),
            ("opus-4-6", "claude-opus-4-6"),
            ("claude-opus-4-6", "claude-opus-4-6"),
        ]:
            alias = _resolve_model(input_value)
            assert MODELS[alias].model_id == expected_model_id

    def test_invalid_value_raises_with_helpful_message(self):
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            _resolve_model("bogus")
        msg = str(exc_info.value)
        assert "bogus" in msg
        assert "alias" in msg
        assert "model ID" in msg
        # Aliases listed in error help users discover the valid choices.
        assert "opus" in msg


# ---------------------------------------------------------------------------
# validate subcommand
# ---------------------------------------------------------------------------


class TestValidateArgs:
    """Argument parsing for the ``validate`` subcommand."""

    def test_minimal(self):
        args = _parse(["validate", "doc.pdf"])
        assert args.command == "validate"
        assert len(args.pdfs) == 1
        assert str(args.pdfs[0]) == "doc.pdf"

    def test_multiple_pdfs(self):
        args = _parse(["validate", "a.pdf", "b.pdf", "c.pdf"])
        assert len(args.pdfs) == 3

    def test_verbose(self):
        args = _parse(["validate", "doc.pdf", "-v"])
        assert args.verbose is True

    def test_output_dir(self):
        args = _parse(["validate", "doc.pdf", "-o", "/tmp/out"])
        assert str(args.output_dir) == "/tmp/out"

    def test_requires_at_least_one_pdf(self):
        _parse_fails(["validate"])

    def test_rejects_convert_flags(self):
        """validate only accepts -v/--verbose and -o/--output-dir."""
        _parse_fails(["validate", "doc.pdf", "--force"])
        _parse_fails(["validate", "doc.pdf", "--model", "sonnet"])
        _parse_fails(["validate", "doc.pdf", "--cache"])
        _parse_fails(["validate", "doc.pdf", "--no-images"])
        _parse_fails(["validate", "doc.pdf", "--rules", "r.txt"])


# ---------------------------------------------------------------------------
# show-prompt subcommand
# ---------------------------------------------------------------------------


class TestShowPromptArgs:
    """Argument parsing for the ``show-prompt`` subcommand."""

    def test_no_options(self):
        args = _parse(["show-prompt"])
        assert args.command == "show-prompt"
        assert args.rules is None

    def test_with_rules(self):
        args = _parse(["show-prompt", "--rules", "custom.rules"])
        assert str(args.rules) == "custom.rules"

    def test_rejects_unrelated_flags(self):
        _parse_fails(["show-prompt", "--verbose"])
        _parse_fails(["show-prompt", "--cache"])
        _parse_fails(["show-prompt", "--force"])


# ---------------------------------------------------------------------------
# init-rules subcommand
# ---------------------------------------------------------------------------


class TestInitRulesArgs:
    """Argument parsing for the ``init-rules`` subcommand."""

    def test_default_path(self):
        args = _parse(["init-rules"])
        assert args.command == "init-rules"
        assert str(args.path) == ".pdf2md.rules"

    def test_custom_path(self):
        args = _parse(["init-rules", "my_rules.txt"])
        assert str(args.path) == "my_rules.txt"

    def test_rejects_unrelated_flags(self):
        _parse_fails(["init-rules", "--verbose"])
        _parse_fails(["init-rules", "--rules", "x"])


# ---------------------------------------------------------------------------
# No subcommand / top-level
# ---------------------------------------------------------------------------


class TestTopLevel:
    """Top-level parser behavior (no subcommand)."""

    def test_no_args_returns_zero(self, monkeypatch):
        """Running with no arguments shows help and returns 0."""
        monkeypatch.setattr("sys.argv", ["pdf2md-claude"])
        assert main() == 0

    def test_unknown_subcommand_fails(self):
        """An unknown subcommand name is rejected by argparse."""
        _parse_fails(["nonexistent", "doc.pdf"])

    def test_old_flat_flags_rejected(self):
        """Old-style flags (--validate, --show-prompt, etc.) no longer work."""
        _parse_fails(["--validate", "doc.pdf"])
        _parse_fails(["--show-prompt"])
        _parse_fails(["--init-rules"])


# ---------------------------------------------------------------------------
# Command handler smoke tests (no I/O)
# ---------------------------------------------------------------------------


class TestShowPromptHandler:
    """Smoke-test the show-prompt handler (no API, no files)."""

    def test_prints_default_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["pdf2md-claude", "show-prompt"],
        )
        rc = main()
        assert rc == 0
        captured = capsys.readouterr()
        # The default prompt should contain some expected text.
        assert "PDF" in captured.out or "page" in captured.out.lower()


class TestInitRulesHandler:
    """Smoke-test the init-rules handler."""

    def test_generates_template(self, monkeypatch, tmp_path, capsys):
        target = tmp_path / "test.rules"
        monkeypatch.setattr(
            "sys.argv",
            ["pdf2md-claude", "init-rules", str(target)],
        )
        rc = main()
        assert rc == 0
        assert target.exists()
        captured = capsys.readouterr()
        assert "Rules template written" in captured.out


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


class TestResolveApiKey:
    """Env var precedence and deprecation warning in ``_resolve_api_key``."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Isolate tests from API keys set in the developer's environment."""
        monkeypatch.delenv(_API_KEY_ENV, raising=False)
        monkeypatch.delenv(_LEGACY_API_KEY_ENV, raising=False)

    def test_primary_var(self, monkeypatch, caplog):
        monkeypatch.setenv(_API_KEY_ENV, "new-key")
        with caplog.at_level(logging.WARNING, logger="pdf2md"):
            assert _resolve_api_key() == ("new-key", _API_KEY_ENV)
        assert not caplog.records

    def test_primary_wins_over_legacy(self, monkeypatch, caplog):
        monkeypatch.setenv(_API_KEY_ENV, "new-key")
        monkeypatch.setenv(_LEGACY_API_KEY_ENV, "old-key")
        with caplog.at_level(logging.WARNING, logger="pdf2md"):
            assert _resolve_api_key() == ("new-key", _API_KEY_ENV)
        assert not caplog.records

    def test_legacy_fallback_warns(self, monkeypatch, caplog):
        monkeypatch.setenv(_LEGACY_API_KEY_ENV, "old-key")
        with caplog.at_level(logging.WARNING, logger="pdf2md"):
            assert _resolve_api_key() == ("old-key", _LEGACY_API_KEY_ENV)
        assert len(caplog.records) == 1
        assert "deprecated" in caplog.text
        assert _LEGACY_API_KEY_ENV in caplog.text
        assert _API_KEY_ENV in caplog.text

    def test_empty_primary_falls_back(self, monkeypatch):
        """An empty primary var counts as unset, like the falsy check before."""
        monkeypatch.setenv(_API_KEY_ENV, "")
        monkeypatch.setenv(_LEGACY_API_KEY_ENV, "old-key")
        assert _resolve_api_key() == ("old-key", _LEGACY_API_KEY_ENV)

    def test_neither_set(self):
        assert _resolve_api_key() == (None, None)


class TestConvertBackendSelection:
    """Smoke-test the convert handler's backend gate (no API calls)."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Isolate tests from API keys set in the developer's environment."""
        monkeypatch.delenv(_API_KEY_ENV, raising=False)
        monkeypatch.delenv(_LEGACY_API_KEY_ENV, raising=False)

    def test_no_key_errors_with_primary_var_name(
        self, monkeypatch, tmp_path, capsys,
    ):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"")
        monkeypatch.setattr("sys.argv", ["pdf2md-claude", "convert", str(pdf)])
        assert main() == 1
        assert f"{_API_KEY_ENV} not set" in capsys.readouterr().err

    def test_legacy_key_warns_and_names_backend(
        self, monkeypatch, tmp_path, capsys,
    ):
        """The legacy key reaches the backend log line and warns once."""
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"")
        monkeypatch.setenv(_LEGACY_API_KEY_ENV, "fake-key")
        monkeypatch.setattr("sys.argv", ["pdf2md-claude", "convert", str(pdf)])
        # Fails later on the empty PDF (no API call is ever made); the
        # backend decision lines are logged before that.
        assert main() == 1
        err = capsys.readouterr().err
        assert "deprecated" in err
        assert f"Backend: Anthropic API ({_LEGACY_API_KEY_ENV})" in err
