"""CLI entry point for pdf2md-claude.

Convert PDF documents to Markdown using Claude's native PDF API.

Usage::

    pdf2md-claude convert document.pdf
    pdf2md-claude convert *.pdf -o output/
    pdf2md-claude remerge document.pdf
    pdf2md-claude validate output/*.md
    pdf2md-claude show-prompt
    pdf2md-claude init-rules
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import anthropic
import colorlog

from pdf2md_claude import __version__
from pdf2md_claude.client import create_client
from pdf2md_claude.converter import DEFAULT_PAGES_PER_CHUNK, PdfConverter
from pdf2md_claude.images import ImageMode
from pdf2md_claude.models import MODELS, ModelConfig, DocumentUsageStats, format_summary
from pdf2md_claude.pipeline import (
    ConversionPipeline,
    ExtractImagesStep,
    MergeContinuedTablesStep,
    ProcessingStep,
    StripAIDescriptionsStep,
    ValidateStep,
    resolve_output,
)
from pdf2md_claude.prompt import SYSTEM_PROMPT
from pdf2md_claude.validator import ValidationResult, check_page_fidelity, validate_output
from pdf2md_claude.rules import (
    AUTO_RULES_FILENAME,
    build_custom_system_prompt,
    generate_rules_template,
    parse_rules_file,
)


_log = logging.getLogger("pdf2md")

DEFAULT_MODEL_ALIAS = "opus"
"""Short alias for the default model (key into ``MODELS`` dict)."""

DEFAULT_IMAGE_DPI = 600
"""Default DPI for page-region image rendering."""

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


def _setup_logging(verbose: bool) -> None:
    """Initialize colorized logging and optionally enable debug level."""
    setup_colorized_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser with subcommands."""
    # -- Parent parsers for shared argument groups -----------------------------
    verbose_parent = argparse.ArgumentParser(add_help=False)
    verbose_parent.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    output_parent = argparse.ArgumentParser(add_help=False)
    output_parent.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Output directory for Markdown files "
             "(default: same directory as each PDF)",
    )

    image_parent = argparse.ArgumentParser(add_help=False)
    image_parent.add_argument(
        "--no-images",
        action="store_true",
        help="Skip image extraction. By default, IMAGE_RECT bounding boxes "
             "emitted by Claude are rendered from the PDF and injected as "
             "image files alongside the Markdown output.",
    )
    image_parent.add_argument(
        "--image-mode",
        choices=[m.value for m in ImageMode],
        default=ImageMode.AUTO.value,
        help="Image extraction mode: 'auto' extracts native rasters when "
             "possible with render fallback; 'snap' renders page regions "
             "snapped to PDF raster bounds; 'bbox' renders AI-based "
             "bounding box directly; 'debug' renders all variants "
             "side-by-side in an HTML table (default: %(default)s).",
    )
    image_parent.add_argument(
        "--image-dpi",
        type=int,
        default=DEFAULT_IMAGE_DPI,
        metavar="DPI",
        help="DPI for page-region rendering — vector diagrams, composites, "
             f"and snap/bbox modes (default: {DEFAULT_IMAGE_DPI}).",
    )
    image_parent.add_argument(
        "--strip-ai-descriptions",
        action="store_true",
        help="Remove AI-generated image description blocks from the output. "
             "These are textual descriptions Claude generates for images, "
             "wrapped in IMAGE_AI_GENERATED_DESCRIPTION markers.",
    )

    # -- Main parser -----------------------------------------------------------
    parser = argparse.ArgumentParser(
        prog="pdf2md-claude",
        description="Convert PDF documents to Markdown using Claude's "
                    "native PDF API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  convert       Convert PDF documents to Markdown (requires ANTHROPIC_API_KEY)
  remerge       Re-merge from cached chunks (no API calls needed)
  validate      Validate existing .md files (no API key needed)
  show-prompt   Print the system prompt to stdout
  init-rules    Generate a rules template file

Examples:
  %(prog)s convert document.pdf              Convert single PDF
  %(prog)s convert *.pdf -o output/          Convert all PDFs to output dir
  %(prog)s convert doc.pdf -f --cache        Force reconvert with caching
  %(prog)s remerge document.pdf              Re-merge from cached chunks
  %(prog)s validate output/*.md              Validate existing .md files
  %(prog)s show-prompt                       Show default system prompt
  %(prog)s init-rules                        Generate .pdf2md.rules template

Run '%(prog)s COMMAND --help' for command-specific options.
        """,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # -- convert ---------------------------------------------------------------
    p_convert = subparsers.add_parser(
        "convert",
        parents=[verbose_parent, output_parent, image_parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Convert PDF documents to Markdown",
        description="Convert PDF documents to Markdown using Claude's "
                    "native PDF API.",
        epilog=f"""
Examples:
  %(prog)s document.pdf                     Convert single PDF
  %(prog)s *.pdf                            Convert all PDFs in current dir
  %(prog)s docs/*.pdf -o output/            Custom output directory
  %(prog)s doc.pdf --max-pages 5            First 5 pages only
  %(prog)s doc.pdf --pages-per-chunk {DEFAULT_PAGES_PER_CHUNK}     Smaller chunks
  %(prog)s doc.pdf --cache                  Enable prompt caching (1h TTL)
  %(prog)s doc.pdf -f                       Force reconvert
  %(prog)s doc.pdf --rules my_rules.txt     Use custom rules
  %(prog)s doc.pdf                          Auto-applies .pdf2md.rules if found
        """,
    )
    p_convert.add_argument(
        "pdfs",
        nargs="+",
        type=Path,
        help="PDF file(s) to convert (supports shell globs)",
    )
    p_convert.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force reconversion even if output already exists "
             "(also clears cached chunks)",
    )
    p_convert.add_argument(
        "--model",
        choices=list(MODELS.keys()),
        default=DEFAULT_MODEL_ALIAS,
        help="Claude model to use (default: %(default)s).",
    )
    p_convert.add_argument(
        "--pages-per-chunk",
        type=int,
        default=DEFAULT_PAGES_PER_CHUNK,
        metavar="N",
        help=f"Number of PDF pages per conversion chunk "
             f"(default: {DEFAULT_PAGES_PER_CHUNK}). "
             "Smaller values improve quality but increase API calls. "
             "Must not exceed the API limit of 100 pages per request.",
    )
    p_convert.add_argument(
        "--max-pages",
        type=int,
        metavar="N",
        help="Convert only the first N pages using the full pipeline "
             "(chunked, with title extraction and merging). "
             "Useful for debugging",
    )
    p_convert.add_argument(
        "--cache",
        action="store_true",
        help="Enable prompt caching (1h TTL) on system prompt and PDF "
             "content. Reduces cost on re-runs with the same PDF chunks "
             "(useful for debugging prompts/pipelines). First run pays "
             "~2x write cost, subsequent runs within 1h pay ~0.1x read cost.",
    )
    p_convert.add_argument(
        "--retries",
        type=int,
        default=10,
        metavar="N",
        help="Max attempts per chunk on transient API/network errors "
             "(default: %(default)s). Uses exponential backoff 1-30s. "
             "Set to 1 to disable retry.",
    )
    p_convert.add_argument(
        "--rules",
        type=Path,
        default=None,
        metavar="FILE",
        help="Custom rules file (replace/append/add rules). "
             "Use -f to reconvert after changing rules.",
    )

    # -- remerge ---------------------------------------------------------------
    p_remerge = subparsers.add_parser(
        "remerge",
        parents=[verbose_parent, output_parent, image_parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Re-merge from cached chunks (no API calls)",
        description="Re-run merge + post-processing + write from cached "
                    "chunks. No API calls, no ANTHROPIC_API_KEY needed. "
                    "Useful for debugging merge/post-processing logic.",
        epilog="""
Examples:
  %(prog)s document.pdf                     Re-merge single PDF
  %(prog)s *.pdf                            Re-merge all PDFs
  %(prog)s doc.pdf --no-images              Re-merge without image extraction
  %(prog)s doc.pdf --image-mode debug       Re-merge with debug image mode
        """,
    )
    p_remerge.add_argument(
        "pdfs",
        nargs="+",
        type=Path,
        help="PDF file(s) to re-merge "
             "(must have existing .chunks/ directories)",
    )

    # -- validate --------------------------------------------------------------
    p_validate = subparsers.add_parser(
        "validate",
        parents=[verbose_parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Validate existing .md files (no API key needed)",
        description="Run structural validation checks on existing Markdown "
                    "files. If a matching .pdf is found nearby, also runs "
                    "PDF fidelity checks.",
        epilog="""
Examples:
  %(prog)s output/*.md                      Validate all .md files
  %(prog)s -v samples/*.md                  Validate with verbose output
        """,
    )
    p_validate.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Markdown file(s) to validate",
    )

    # -- show-prompt -----------------------------------------------------------
    p_show = subparsers.add_parser(
        "show-prompt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Print the system prompt to stdout",
        description="Print the system prompt to stdout and exit.",
        epilog="""
Examples:
  %(prog)s                                  Show default system prompt
  %(prog)s --rules my_rules.txt             Show merged prompt with rules
        """,
    )
    p_show.add_argument(
        "--rules",
        type=Path,
        default=None,
        metavar="FILE",
        help="Custom rules file to merge into the prompt.",
    )

    # -- init-rules ------------------------------------------------------------
    p_init = subparsers.add_parser(
        "init-rules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Generate a rules template file",
        description="Generate a fully commented rules template and exit.",
        epilog=f"""
Examples:
  %(prog)s                                  Generate {AUTO_RULES_FILENAME}
  %(prog)s my_rules.txt                     Generate at custom path
        """,
    )
    p_init.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(AUTO_RULES_FILENAME),
        help=f"Output path for the template "
             f"(default: {AUTO_RULES_FILENAME})",
    )

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_file_paths(
    raw_paths: list[Path],
    kind: str,
) -> list[Path] | None:
    """Resolve and validate a list of file paths.

    Returns resolved paths on success, or ``None`` on first error
    (after logging the error).
    """
    resolved: list[Path] = []
    for p in raw_paths:
        rp = p.resolve()
        if not rp.exists():
            _log.error("%s not found: %s", kind, p)
            return None
        if not rp.is_file():
            _log.error("Not a file: %s", p)
            return None
        resolved.append(rp)
    return resolved


def _build_steps(args: argparse.Namespace) -> list[ProcessingStep]:
    """Build the processing step chain from CLI args.

    Used by both ``convert`` and ``remerge`` commands.
    """
    steps: list[ProcessingStep] = [MergeContinuedTablesStep()]
    if not args.no_images:
        steps.append(ExtractImagesStep(
            image_mode=ImageMode(args.image_mode),
            render_dpi=args.image_dpi,
        ))
    if args.strip_ai_descriptions:
        steps.append(StripAIDescriptionsStep())
    steps.append(ValidateStep())
    return steps


def _resolve_rules(
    pdf_path: Path,
    explicit_rules: Path | None,
    rules_cache: dict[Path, str],
) -> str | None:
    """Resolve the system prompt for a single PDF.

    Checks the explicit ``--rules`` path first, then auto-discovers
    ``.pdf2md.rules`` next to the PDF.  Results are cached by resolved
    path so that multiple PDFs sharing the same rules file don't re-parse.

    Returns:
        Custom system prompt string, or ``None`` if no rules apply.
    """
    rules_path = explicit_rules
    if not rules_path:
        auto_path = pdf_path.parent / AUTO_RULES_FILENAME
        if auto_path.is_file():
            rules_path = auto_path

    if not rules_path:
        return None

    resolved = rules_path.resolve()
    if resolved not in rules_cache:
        parsed = parse_rules_file(resolved)
        rules_cache[resolved] = build_custom_system_prompt(parsed)
        _log.info(
            "Custom rules (%s): %d replaced, %d appended, "
            "%d inserted, %d added",
            rules_path, len(parsed.replacements),
            len(parsed.appends), len(parsed.insertions),
            len(parsed.extras),
        )
    return rules_cache[resolved]


def _discover_pdf(md_path: Path) -> Path | None:
    """Find a matching source PDF for a markdown file.

    Checks for ``<stem>.pdf`` in the same directory as the ``.md`` file.
    Returns the PDF path if found, ``None`` otherwise.
    """
    candidate = md_path.with_suffix(".pdf")
    return candidate if candidate.is_file() else None


def _validate_files(md_paths: list[Path], verbose: bool) -> int:
    """Run standalone validation on existing ``.md`` files.

    For each file, reads the markdown content, runs structural
    validation checks, and optionally runs PDF fidelity checks if
    a matching ``.pdf`` is found alongside the ``.md`` file.

    Args:
        md_paths: Resolved paths to ``.md`` files.
        verbose: Whether verbose logging is enabled.

    Returns:
        Exit code: 0 if all files pass (warnings OK), 1 if any has errors.
    """
    total_errors = 0
    total_warnings = 0
    has_errors = False

    for md_path in md_paths:
        doc_name = md_path.name
        _log.info("%s:", doc_name)

        markdown = md_path.read_text(encoding="utf-8")
        result: ValidationResult = validate_output(markdown)

        # Optional PDF fidelity check.
        pdf_path = _discover_pdf(md_path)
        if pdf_path is not None:
            _log.debug("  Found source PDF: %s", pdf_path)
            check_page_fidelity(pdf_path, markdown, result)
        else:
            _log.debug("  No matching PDF found for fidelity check")

        result.log_all()

        if not result.ok:
            _log.warning(
                "  ⚠ %d error(s), %d warning(s)",
                len(result.errors), len(result.warnings),
            )
            has_errors = True
        elif result.warnings:
            _log.warning(
                "  ⚠ %d warning(s)", len(result.warnings),
            )
        else:
            _log.info("  ✓ OK")

        total_errors += len(result.errors)
        total_warnings += len(result.warnings)

    _log.info("")
    _log.info(_SUMMARY_SEP)
    _log.info(
        "Validated %d file(s): %d error(s), %d warning(s)",
        len(md_paths), total_errors, total_warnings,
    )
    _log.info(_SUMMARY_SEP)

    return 1 if has_errors else 0


def _convert_single_pdf(
    pdf_path: Path,
    *,
    model: ModelConfig,
    client: anthropic.Anthropic,
    pipeline: ConversionPipeline,
    pages_per_chunk: int,
    max_pages: int | None,
    force: bool,
    use_cache: bool,
    max_retries: int,
    rules_path: Path | None,
    rules_cache: dict[Path, str],
) -> DocumentUsageStats:
    """Convert a single PDF and return its usage stats.

    Raises on failure so the caller can count success/failure.
    """
    system_prompt = _resolve_rules(pdf_path, rules_path, rules_cache)

    converter = PdfConverter(
        client, model,
        use_cache=use_cache,
        system_prompt=system_prompt,
        max_retries=max_retries,
    )
    result = pipeline.convert(
        converter,
        pages_per_chunk=pages_per_chunk,
        max_pages=max_pages,
        force=force,
    )
    return result.stats


def _log_summary(
    model: ModelConfig | None,
    all_stats: list[DocumentUsageStats],
    total_elapsed: float,
    success: int,
    failure: int,
    cached: int,
    remerge: bool,
) -> None:
    """Print the final conversion/remerge summary block."""
    _log.info("")
    _log.info(_SUMMARY_SEP)
    if all_stats and not remerge and model is not None:
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
        _log.info(
            "Results: %d converted, %d failed, %d cached",
            success, failure, cached,
        )
    _log.info(_SUMMARY_SEP)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_init_rules(args: argparse.Namespace) -> int:
    """Handle the ``init-rules`` command."""
    generate_rules_template(args.path)
    print(f"Rules template written to {args.path}")
    return 0


def _cmd_show_prompt(args: argparse.Namespace) -> int:
    """Handle the ``show-prompt`` command."""
    if args.rules:
        if not args.rules.is_file():
            print(
                f"error: Rules file not found: {args.rules}",
                file=sys.stderr,
            )
            return 1
        parsed = parse_rules_file(args.rules)
        prompt = build_custom_system_prompt(parsed)
    else:
        prompt = SYSTEM_PROMPT
    print(prompt)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Handle the ``validate`` command."""
    _setup_logging(args.verbose)

    md_paths = _resolve_file_paths(args.files, "File")
    if md_paths is None:
        return 1

    _log.info("pdf2md-claude %s", __version__)
    _log.info("Mode: validate (%d file(s))", len(md_paths))
    return _validate_files(md_paths, verbose=args.verbose)


def _cmd_remerge(args: argparse.Namespace) -> int:
    """Handle the ``remerge`` command."""
    _setup_logging(args.verbose)

    pdf_paths = _resolve_file_paths(args.pdfs, "PDF")
    if pdf_paths is None:
        return 1

    output_dir = args.output_dir.resolve() if args.output_dir else None
    steps = _build_steps(args)

    _log.info("pdf2md-claude %s", __version__)
    _log.info("Found %d PDF(s) to process", len(pdf_paths))
    _log.info("Mode: remerge (re-merge from cached chunks, no API calls)")
    if output_dir:
        _log.info("Output directory: %s", output_dir)
    else:
        _log.info("Output: next to each PDF")

    # Ensure output directory exists (if explicitly set).
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    total_start = time.time()
    all_stats: list[DocumentUsageStats] = []
    success = 0
    failure = 0

    try:
        for pdf_path in pdf_paths:
            doc_name = pdf_path.stem
            output_file = resolve_output(pdf_path, output_dir)
            pipeline = ConversionPipeline(steps, pdf_path, output_file)

            _log.info("%s:", doc_name)
            try:
                result = pipeline.remerge()
                all_stats.append(result.stats)
                success += 1
            except Exception as e:
                _log.error(
                    "  ✗ %s: %s: %s", doc_name, type(e).__name__, e,
                )
                failure += 1

        total_elapsed = time.time() - total_start
        _log_summary(
            None, all_stats, total_elapsed,
            success, failure, cached=0, remerge=True,
        )
        return 1 if failure > 0 else 0

    except Exception as e:
        _log.error("Fatal error: %s", e)
        return 1


def _cmd_convert(args: argparse.Namespace) -> int:
    """Handle the ``convert`` command."""
    # Validate rules file early (before logging setup).
    if args.rules and not args.rules.is_file():
        print(
            f"error: Rules file not found: {args.rules}",
            file=sys.stderr,
        )
        return 1

    _setup_logging(args.verbose)

    pdf_paths = _resolve_file_paths(args.pdfs, "PDF")
    if pdf_paths is None:
        return 1

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

    steps = _build_steps(args)

    try:
        _log.info("pdf2md-claude %s", __version__)
        _log.info("Found %d PDF(s) to process", len(pdf_paths))
        _log.info(
            "Model: %s (%s)", model.display_name, model.model_id,
        )
        _log.info(
            "Chunking: %d pages/chunk (API limit: %d)",
            pages_per_chunk, model.max_pdf_pages,
        )
        if output_dir:
            _log.info("Output directory: %s", output_dir)
        else:
            _log.info("Output: next to each PDF")
        if args.cache:
            _log.info("Prompt caching: ENABLED (1h TTL)")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            _log.error("ANTHROPIC_API_KEY environment variable not set")
            return 1
        client = create_client(api_key, model)

        # Ensure output directory exists (if explicitly set).
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
            output_file = resolve_output(pdf_path, output_dir)
            pipeline = ConversionPipeline(steps, pdf_path, output_file)

            if not pipeline.needs_conversion(
                force=args.force, model_id=model.model_id,
            ):
                cached_stats = pipeline.load_cached_stats()
                if cached_stats is not None:
                    all_stats.append(cached_stats)
                    _log.info(
                        "⊙ %s (cached, $%.2f)",
                        doc_name, cached_stats.cost,
                    )
                else:
                    _log.info("⊙ %s (cached)", doc_name)
                cached += 1
                continue

            _log.info("%s:", doc_name)

            try:
                stats = _convert_single_pdf(
                    pdf_path,
                    model=model,
                    client=client,
                    pipeline=pipeline,
                    pages_per_chunk=pages_per_chunk,
                    max_pages=args.max_pages,
                    force=args.force,
                    use_cache=args.cache,
                    max_retries=args.retries,
                    rules_path=args.rules,
                    rules_cache=rules_cache,
                )
                all_stats.append(stats)
                success += 1
            except Exception as e:
                _log.error(
                    "  ✗ %s: %s: %s", doc_name, type(e).__name__, e,
                )
                failure += 1

        total_elapsed = time.time() - total_start
        _log_summary(
            model, all_stats, total_elapsed,
            success, failure, cached, remerge=False,
        )
        return 1 if failure > 0 else 0

    except Exception as e:
        _log.error("Fatal error: %s", e)
        return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Main entry point."""
    parser = _build_parser()

    # Show help if no arguments provided.
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # No subcommand given (e.g. only --version was handled by argparse).
    if args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "convert": _cmd_convert,
        "remerge": _cmd_remerge,
        "validate": _cmd_validate,
        "show-prompt": _cmd_show_prompt,
        "init-rules": _cmd_init_rules,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
