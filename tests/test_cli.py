"""Tests for CLI argument parsing and subcommand dispatch.

These tests exercise the argument parser structure — they do NOT call
the Anthropic API or touch real PDFs.
"""

from __future__ import annotations

import pytest

from pdf2md_claude.cli import _build_parser, main


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


# ---------------------------------------------------------------------------
# remerge subcommand
# ---------------------------------------------------------------------------


class TestRemergeArgs:
    """Argument parsing for the ``remerge`` subcommand."""

    def test_minimal(self):
        args = _parse(["remerge", "doc.pdf"])
        assert args.command == "remerge"
        assert len(args.pdfs) == 1

    def test_with_output_dir(self):
        args = _parse(["remerge", "doc.pdf", "-o", "/tmp/out"])
        assert str(args.output_dir) == "/tmp/out"

    def test_verbose(self):
        args = _parse(["remerge", "doc.pdf", "-v"])
        assert args.verbose is True

    def test_image_options(self):
        args = _parse([
            "remerge", "doc.pdf",
            "--no-images",
            "--image-mode", "debug",
            "--image-dpi", "150",
            "--strip-ai-descriptions",
        ])
        assert args.no_images is True
        assert args.image_mode == "debug"
        assert args.image_dpi == 150
        assert args.strip_ai_descriptions is True

    def test_requires_at_least_one_pdf(self):
        _parse_fails(["remerge"])

    def test_rejects_convert_only_flags(self):
        """Flags like --force, --model, --cache belong to convert only."""
        _parse_fails(["remerge", "doc.pdf", "--force"])
        _parse_fails(["remerge", "doc.pdf", "--model", "sonnet"])
        _parse_fails(["remerge", "doc.pdf", "--cache"])
        _parse_fails(["remerge", "doc.pdf", "--retries", "3"])
        _parse_fails(["remerge", "doc.pdf", "--max-pages", "5"])
        _parse_fails(["remerge", "doc.pdf", "--rules", "r.txt"])
        _parse_fails(["remerge", "doc.pdf", "--pages-per-chunk", "5"])


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
        """Old-style flags (--remerge, --validate) no longer work."""
        _parse_fails(["--remerge", "doc.pdf"])
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
