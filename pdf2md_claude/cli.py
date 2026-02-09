"""CLI entry point for pdf2md-claude.

Convert PDF documents to Markdown using Claude's native PDF API.

Usage::

    pdf2md-claude document.pdf
    pdf2md-claude *.pdf
    pdf2md-claude docs/*.pdf -o output/
    python -m pdf2md_claude document.pdf
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import colorlog

from pdf2md_claude.client import create_client
from pdf2md_claude.converter import DEFAULT_PAGES_PER_CHUNK, needs_conversion
from pdf2md_claude.models import MODELS, DocumentUsageStats, format_summary
from pdf2md_claude.pipeline import convert_document, remerge_document
from pdf2md_claude.prompt import SYSTEM_PROMPT
from pdf2md_claude.rules import (
    AUTO_RULES_FILENAME,
    build_custom_system_prompt,
    generate_rules_template,
    parse_rules_file,
)


_log = logging.getLogger("pdf2md")

DEFAULT_MODEL_ALIAS = "opus"
"""Short alias for the default model (key into ``MODELS`` dict)."""

_SUMMARY_SEP = "=" * 78
"""Separator line for the conversion summary block."""


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_colorized_logging():
    """Configure colorized logging output."""
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s %(blue)s%(name)-9s%(reset)s: %(message)s",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_output(pdf_path: Path, suffix: str, output_dir: Path | None) -> Path:
    """Resolve output file path for a given PDF.

    Default: Markdown file is placed next to the source PDF.
    With --output-dir: all output goes to the specified directory.
    """
    base = output_dir if output_dir else pdf_path.parent
    return base / f"{pdf_path.stem}{suffix}.md"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Convert PDF documents to Markdown using Claude's native PDF API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  %(prog)s document.pdf                          Convert single PDF
  %(prog)s *.pdf                                 Convert all PDFs in current dir
  %(prog)s docs/*.pdf -o output/                 Custom output directory
  %(prog)s doc.pdf --max-pages 5                 First 5 pages only
  %(prog)s doc.pdf --pages-per-chunk {DEFAULT_PAGES_PER_CHUNK}          Smaller chunks (better quality)
  %(prog)s doc.pdf --cache                       Enable prompt caching (1h TTL)
  %(prog)s doc.pdf -f                            Force reconvert
  %(prog)s doc.pdf --remerge                     Re-merge from cached chunks (no API)
  %(prog)s --init-rules                          Generate .pdf2md.rules template
  %(prog)s --init-rules my_rules.txt             Generate template at custom path
  %(prog)s doc.pdf --rules my_rules.txt          Use custom rules
  %(prog)s doc.pdf                               Auto-applies .pdf2md.rules if found
  %(prog)s --show-prompt                         Show default system prompt
  %(prog)s --rules my_rules.txt --show-prompt    Show merged prompt
        """,
    )
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="PDF file(s) to convert (supports shell globs)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Output directory for Markdown files (default: same directory as each PDF)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force reconversion even if output already exists "
             "(also clears cached chunks)",
    )
    parser.add_argument(
        "--remerge",
        action="store_true",
        help="Re-run merge + validate + write from cached chunks "
             "(no API calls, no ANTHROPIC_API_KEY needed). "
             "Useful for debugging merge/post-processing logic.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        metavar="N",
        help="Convert only the first N pages using the full pipeline "
             "(chunked, with title extraction and merging). Useful for debugging",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable prompt caching (1h TTL) on system prompt and PDF content. "
             "Reduces cost on re-runs with the same PDF chunks (useful for "
             "debugging prompts/pipelines). First run pays ~2x write cost, "
             "subsequent runs within 1h pay ~0.1x read cost.",
    )
    parser.add_argument(
        "--pages-per-chunk",
        type=int,
        default=DEFAULT_PAGES_PER_CHUNK,
        metavar="N",
        help=f"Number of PDF pages per conversion chunk (default: {DEFAULT_PAGES_PER_CHUNK}). "
             "Smaller values improve quality but increase API calls. "
             "Must not exceed the API limit of 100 pages per request.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip image extraction. By default, IMAGE_RECT bounding boxes "
             "emitted by Claude are rendered from the PDF and injected as "
             "image files alongside the Markdown output.",
    )
    parser.add_argument(
        "--model",
        choices=list(MODELS.keys()),
        default=DEFAULT_MODEL_ALIAS,
        help="Claude model to use (default: %(default)s).",
    )
    parser.add_argument(
        "--init-rules",
        type=Path,
        nargs="?",
        const=Path(AUTO_RULES_FILENAME),
        default=None,
        metavar="FILE",
        help="Generate a rules template and exit "
             f"(default: {AUTO_RULES_FILENAME}).",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        metavar="FILE",
        help="Custom rules file (replace/append/add rules). "
             "Use -f to reconvert after changing rules.",
    )
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="Print the system prompt to stdout and exit.",
    )

    # Show help if no arguments provided
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # Standalone commands (no PDFs needed).
    if args.init_rules is not None:
        generate_rules_template(args.init_rules)
        print(f"Rules template written to {args.init_rules}")
        return 0

    if args.show_prompt:
        if args.rules:
            parsed = parse_rules_file(args.rules)
            prompt = build_custom_system_prompt(parsed)
        else:
            prompt = SYSTEM_PROMPT
        print(prompt)
        return 0

    # Validate: at least one PDF required
    if not args.pdfs:
        parser.print_help()
        return 0

    # Argument validation.
    if args.rules and args.remerge:
        _log.error("--rules and --remerge cannot be used together")
        return 1
    if args.rules and not args.rules.is_file():
        _log.error("Rules file not found: %s", args.rules)
        return 1

    # Setup logging
    setup_colorized_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve and validate PDF paths
    pdf_paths: list[Path] = []
    for p in args.pdfs:
        resolved = p.resolve()
        if not resolved.exists():
            _log.error("PDF not found: %s", p)
            return 1
        if not resolved.is_file():
            _log.error("Not a file: %s", p)
            return 1
        pdf_paths.append(resolved)

    # Resolve output directory
    output_dir = args.output_dir.resolve() if args.output_dir else None

    model = MODELS[args.model]
    pages_per_chunk: int = args.pages_per_chunk

    # Validate pages_per_chunk against the API hard limit.
    if pages_per_chunk < 1:
        _log.error("--pages-per-chunk must be at least 1")
        return 1
    if pages_per_chunk > model.max_pdf_pages:
        _log.error(
            "--pages-per-chunk %d exceeds API limit of %d pages per request",
            pages_per_chunk, model.max_pdf_pages,
        )
        return 1

    remerge = args.remerge

    try:
        _log.info("Found %d PDF(s) to process", len(pdf_paths))
        if remerge:
            _log.info("Mode: --remerge (re-merge from cached chunks, no API calls)")
        else:
            _log.info("Model: %s (%s)", model.display_name, model.model_id)
            _log.info("Chunking: %d pages/chunk (API limit: %d)", pages_per_chunk, model.max_pdf_pages)
        if output_dir:
            _log.info("Output directory: %s", output_dir)
        else:
            _log.info("Output: next to each PDF")
        if args.cache and not remerge:
            _log.info("Prompt caching: ENABLED (1h TTL)")

        # API client is only needed for full conversion.
        client = None
        if not remerge:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                _log.error("ANTHROPIC_API_KEY environment variable not set")
                return 1
            client = create_client(api_key, model)

        # Ensure output directory exists (if explicitly set)
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        total_start = time.time()
        all_stats: list[DocumentUsageStats] = []
        success = 0
        failure = 0
        cached = 0
        rules_cache: dict[Path, str] = {}

        for pdf_path in pdf_paths:
            doc_name = pdf_path.stem
            suffix = f"_first{args.max_pages}" if args.max_pages else ""
            per_pdf_output_dir = output_dir if output_dir else pdf_path.parent

            if not remerge and not needs_conversion(
                pdf_path, per_pdf_output_dir, args.force,
                suffix=suffix, model_id=model.model_id,
            ):
                _log.info("⊙ %s (cached)", doc_name)
                cached += 1
                continue

            _log.info("%s:", doc_name)

            try:
                output_file = resolve_output(pdf_path, suffix, output_dir)

                # Resolve custom rules for this PDF.
                system_prompt = None
                if not remerge:
                    rules_path = args.rules
                    if not rules_path:
                        auto_path = pdf_path.parent / AUTO_RULES_FILENAME
                        if auto_path.is_file():
                            rules_path = auto_path
                    if rules_path:
                        resolved_rules = rules_path.resolve()
                        if resolved_rules not in rules_cache:
                            parsed = parse_rules_file(resolved_rules)
                            rules_cache[resolved_rules] = build_custom_system_prompt(parsed)
                            _log.info(
                                "Custom rules (%s): %d replaced, %d appended, "
                                "%d inserted, %d added",
                                rules_path, len(parsed.replacements),
                                len(parsed.appends), len(parsed.insertions),
                                len(parsed.extras),
                            )
                        system_prompt = rules_cache[resolved_rules]

                if remerge:
                    result = remerge_document(
                        output_file, pdf_path=pdf_path,
                        extract_images=not args.no_images,
                    )
                else:
                    assert client is not None
                    result = convert_document(
                        client, model, pdf_path, output_file,
                        max_pages=args.max_pages,
                        use_cache=args.cache,
                        pages_per_chunk=pages_per_chunk,
                        force=args.force,
                        extract_images=not args.no_images,
                        system_prompt=system_prompt,
                    )
                all_stats.append(result.stats)
                success += 1
            except Exception as e:
                _log.error("  ✗ %s: %s: %s", doc_name, type(e).__name__, e)
                failure += 1

        total_elapsed = time.time() - total_start

        # Summary
        _log.info("")
        _log.info(_SUMMARY_SEP)
        if all_stats and not remerge:
            _log.info("")
            summary = format_summary(model, all_stats)
            for line in summary.split("\n"):
                _log.info(line)
            _log.info("")
        _log.info(_SUMMARY_SEP)
        _log.info("Total time: %.1fs", total_elapsed)
        if remerge:
            _log.info("Results: %d remerged, %d failed", success, failure)
        else:
            _log.info("Results: %d converted, %d failed, %d cached", success, failure, cached)
        _log.info(_SUMMARY_SEP)

        return 1 if failure > 0 else 0

    except Exception as e:
        _log.error("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
