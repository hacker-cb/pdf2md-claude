"""CLI entry point for pdf2md-claude.

Convert PDF documents to Markdown using Claude's native PDF API.

Usage::

    pdf2md-claude convert document.pdf
    pdf2md-claude convert *.pdf -o output/
    pdf2md-claude convert document.pdf --from merge
    pdf2md-claude validate document.pdf
    pdf2md-claude show-prompt
    pdf2md-claude init-rules
"""

import argparse
import logging
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import anthropic
import colorlog

from pdf2md_claude import __version__
from pdf2md_claude.converter import DEFAULT_PAGES_PER_CHUNK
from pdf2md_claude.images import ImageMode
from pdf2md_claude.models import MODELS, ModelConfig, DocumentUsageStats, format_summary
from pdf2md_claude.pipeline import ConversionPipeline, resolve_output
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
# Thread-local logging context (for parallel document processing)
# ---------------------------------------------------------------------------

_thread_context = threading.local()
"""Per-thread storage for the current document name."""

_doc_prefix_width: int = 0
"""Minimum width for the ``[doc_name]`` prefix (set before spawning workers).

When non-zero the prefix is right-padded so that log messages align
across documents with different name lengths.  Zero means no prefix.
"""


class _DocumentContextFilter(logging.Filter):
    """Inject per-thread document name into every log record.

    When a worker thread sets ``_thread_context.doc_name``, all log
    records emitted from that thread will carry a ``doc_prefix`` field
    (e.g. ``"[my_doc]    "``), right-padded to ``_doc_prefix_width``
    for aligned output.  When not set, ``doc_prefix`` is the empty
    string so single-threaded output is unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        doc = getattr(_thread_context, "doc_name", "")
        if doc:
            tag = f"[{doc}]"
            # +1 for the trailing space after the padded bracket tag.
            record.doc_prefix = tag.ljust(_doc_prefix_width) + " "  # type: ignore[attr-defined]
        else:
            record.doc_prefix = ""  # type: ignore[attr-defined]
        return True


def set_document_context(doc_name: str) -> None:
    """Set the document name for the current thread's log lines."""
    _thread_context.doc_name = doc_name


def clear_document_context() -> None:
    """Clear the document name for the current thread."""
    _thread_context.doc_name = ""


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_colorized_logging():
    """Configure colorized logging output."""
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s %(blue)s%(name)-9s%(reset)s: "
            "%(doc_prefix)s%(message)s",
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
    handler.addFilter(_DocumentContextFilter())
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

_JOBS_AUTO = 0
"""Sentinel for ``-j`` without a number (auto = one worker per document)."""


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

    jobs_parent = argparse.ArgumentParser(add_help=False)
    jobs_parent.add_argument(
        "-j", "--jobs",
        type=int,
        default=1,
        nargs="?",
        const=_JOBS_AUTO,
        metavar="N",
        help="Number of documents to process in parallel. "
             "'-j' alone = one worker per document; "
             "'-j N' = exactly N workers (default: 1, sequential).",
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
    image_parent.add_argument(
        "--no-format",
        action="store_true",
        help="Skip markdown formatting. By default, HTML tables are "
             "prettified with consistent indentation and markdown spacing "
             "is normalized (blank lines, trailing whitespace).",
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
  validate      Validate converted output (no API key needed)
  show-prompt   Print the system prompt to stdout
  init-rules    Generate a rules template file

Examples:
  %(prog)s convert document.pdf              Convert single PDF
  %(prog)s convert *.pdf -o output/          Convert all PDFs to output dir
  %(prog)s convert doc.pdf -f --cache        Force reconvert with caching
  %(prog)s convert doc.pdf --from merge      Re-merge from cached chunks
  %(prog)s validate document.pdf              Validate converted output
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
        parents=[verbose_parent, output_parent, jobs_parent, image_parent],
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
  %(prog)s doc.pdf --from merge             Re-merge from cached chunks
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
    p_convert.add_argument(
        "--from",
        dest="from_step",
        choices=["merge"],
        default=None,
        metavar="STEP",
        help="Skip earlier pipeline stages and start from STEP. "
             "'merge' re-runs merge + post-processing from cached "
             "chunks (no API calls). Requires a prior conversion "
             "with a populated .staging/ directory.",
    )

    # -- validate --------------------------------------------------------------
    p_validate = subparsers.add_parser(
        "validate",
        parents=[verbose_parent, output_parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Validate converted output (no API key needed)",
        description="Run structural validation checks on converted Markdown "
                    "output, including per-page fidelity checks against the "
                    "source PDF. Accepts PDF paths and derives .md paths "
                    "using the same convention as 'convert'.",
        epilog="""
Examples:
  %(prog)s document.pdf                     Validate single PDF's output
  %(prog)s *.pdf                            Validate all PDFs in current dir
  %(prog)s *.pdf -o output/                 Validate with custom output dir
  %(prog)s -v document.pdf                  Validate with verbose output
        """,
    )
    p_validate.add_argument(
        "pdfs",
        nargs="+",
        type=Path,
        help="PDF file(s) whose converted .md output will be validated",
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
    expected_suffix: str | None = None,
) -> list[Path] | None:
    """Resolve and validate a list of file paths.

    Args:
        raw_paths: Paths from argparse.
        kind: Human-readable label for error messages (e.g. ``"PDF"``).
        expected_suffix: If set, reject files with a different suffix
            (e.g. ``".pdf"``).  The check is case-insensitive.

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
        if (
            expected_suffix
            and rp.suffix.lower() != expected_suffix.lower()
        ):
            _log.error(
                "Expected a %s file, got %s: %s",
                expected_suffix, rp.suffix or "(no extension)", p,
            )
            return None
        resolved.append(rp)
    resolved.sort(key=lambda p: p.name)
    return resolved



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



def _validate_files(
    pdf_paths: list[Path],
    output_dir: Path | None,
) -> int:
    """Run standalone validation on converted output.

    For each PDF, derives the ``.md`` output path (same convention as
    ``convert``), reads the markdown, runs structural validation, and
    cross-checks page content against the source PDF.

    Both the PDF and its derived ``.md`` must exist; missing files are
    logged as errors and counted as failures.

    Args:
        pdf_paths: Resolved paths to source PDF files.
        output_dir: Optional output directory (mirrors ``-o`` in convert).

    Returns:
        Exit code: 0 if all files pass (warnings OK), 1 if any has errors.
    """
    total_errors = 0
    total_warnings = 0
    validated = 0
    failed = 0

    # category -> list of (doc_name, count)
    category_summary: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for pdf_path in pdf_paths:
        doc_name = pdf_path.stem
        md_path = resolve_output(pdf_path, output_dir)

        if not pdf_path.is_file():
            _log.error("%s: PDF not found: %s", doc_name, pdf_path)
            failed += 1
            continue

        if not md_path.is_file():
            _log.error(
                "%s: Markdown not found: %s — run 'convert' first",
                doc_name, md_path,
            )
            failed += 1
            continue

        _log.info("%s:", doc_name)

        markdown = md_path.read_text(encoding="utf-8")
        result: ValidationResult = validate_output(markdown)
        check_page_fidelity(pdf_path, markdown, result)

        result.log_all()

        if not result.ok:
            _log.warning(
                "  ⚠ %d error(s), %d warning(s)",
                len(result.errors), len(result.warnings),
            )
        elif result.warnings:
            _log.warning(
                "  ⚠ %d warning(s)", len(result.warnings),
            )
        else:
            _log.info("  ✓ OK")

        # Accumulate per-category counts for the grouped summary.
        cat_counts = Counter(cat for cat, _ in (*result.errors, *result.warnings))
        for cat, cnt in cat_counts.items():
            category_summary[cat].append((doc_name, cnt))

        total_errors += len(result.errors)
        total_warnings += len(result.warnings)
        validated += 1

    # Print grouped summary before the totals line.
    _log.info("")
    _log.info(_SUMMARY_SEP)
    if category_summary:
        _log.info("Problem summary by category:")
        _log.info("")
        for cat, file_counts in sorted(category_summary.items()):
            total_cat = sum(cnt for _, cnt in file_counts)
            _log.info(
                "  %s (%d issue(s) in %d file(s)):",
                cat, total_cat, len(file_counts),
            )
            for doc_name, cnt in file_counts:
                _log.info("    %-40s %d", doc_name, cnt)
            _log.info("")
        _log.info(_SUMMARY_SEP)

    parts = [f"{validated} validated"]
    if failed:
        parts.append(f"{failed} failed")
    parts.append(f"{total_errors} error(s)")
    parts.append(f"{total_warnings} warning(s)")
    _log.info(
        "Validate %d PDF(s): %s",
        len(pdf_paths), ", ".join(parts),
    )
    _log.info(_SUMMARY_SEP)

    return 1 if (total_errors > 0 or failed > 0) else 0


@dataclass
class _DocConvertResult:
    """Result of processing a single document (for parallel collection)."""

    pdf_path: Path
    status: str  # "converted", "cached", "failed"
    stats: DocumentUsageStats | None = None
    error: str | None = None


def _convert_one_document(
    pdf_path: Path,
    *,
    output_dir: Path | None,
    model: ModelConfig,
    api_key: str,
    pages_per_chunk: int,
    max_pages: int | None,
    force: bool,
    use_cache: bool,
    max_retries: int,
    system_prompt: str | None,
    image_mode: ImageMode,
    image_dpi: int | None,
    no_images: bool,
    strip_ai_descriptions: bool,
    no_format: bool,
    from_step: str | None = None,
) -> _DocConvertResult:
    """Convert a single PDF: check staleness, run pipeline.

    Used as the per-document worker in both sequential and parallel modes.
    Sets the per-thread document context so that all log lines emitted
    during this document's processing carry the ``[doc_name]`` prefix.
    """
    doc_name = pdf_path.stem
    set_document_context(doc_name)
    try:
        output_file = resolve_output(pdf_path, output_dir)
        pipeline = ConversionPipeline(
            pdf_path,
            output_file,
            api_key=api_key,
            model=model,
            use_cache=use_cache,
            max_retries=max_retries,
            system_prompt=system_prompt,
            image_mode=image_mode,
            image_dpi=image_dpi,
            no_images=no_images,
            strip_ai_descriptions=strip_ai_descriptions,
            no_format=no_format,
        )

        if not pipeline.needs_conversion(force=force or bool(from_step)):
            cached_stats = pipeline.load_cached_stats()
            if cached_stats is not None:
                _log.info(
                    "⊙ %s (cached, $%.2f)", doc_name, cached_stats.cost,
                )
            else:
                _log.info("⊙ %s (cached)", doc_name)
            return _DocConvertResult(pdf_path, "cached", stats=cached_stats)

        _log.info("Converting %s...", doc_name)

        effective_ppc = pipeline.resolve_pages_per_chunk(
            pages_per_chunk, force=force,
        )

        result = pipeline.run(
            pages_per_chunk=effective_ppc,
            max_pages=max_pages,
            force=force,
            from_step=from_step,
        )
        return _DocConvertResult(pdf_path, "converted", stats=result.stats)

    except Exception as e:
        _log.error("  ✗ %s: %s: %s", doc_name, type(e).__name__, e)
        return _DocConvertResult(pdf_path, "failed", error=str(e))

    finally:
        clear_document_context()



def _log_summary(
    model: ModelConfig,
    all_stats: list[DocumentUsageStats],
    total_elapsed: float,
    success: int,
    failure: int,
    cached: int,
) -> None:
    """Print the final conversion summary block."""
    _log.info("")
    _log.info(_SUMMARY_SEP)
    if all_stats:
        _log.info("")
        summary = format_summary(model, all_stats)
        for line in summary.split("\n"):
            _log.info(line)
        _log.info("")
    _log.info(_SUMMARY_SEP)
    _log.info("Total time: %.1fs", total_elapsed)
    _log.info("Results: %d processed, %d failed, %d cached", success, failure, cached)
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

    pdf_paths = _resolve_file_paths(args.pdfs, "PDF", expected_suffix=".pdf")
    if pdf_paths is None:
        return 1

    output_dir = args.output_dir.resolve() if args.output_dir else None

    _log.info("pdf2md-claude %s", __version__)
    _log.info("Mode: validate (%d PDF(s))", len(pdf_paths))
    if output_dir:
        _log.info("Output directory: %s", output_dir)
    return _validate_files(pdf_paths, output_dir)


def _cmd_convert(args: argparse.Namespace) -> int:
    """Handle the ``convert`` command."""
    # Early validation: --from and --force are mutually exclusive.
    if args.from_step and args.force:
        print(
            "error: --from and --force are mutually exclusive",
            file=sys.stderr,
        )
        return 1

    # Validate rules file early (before logging setup).
    if args.rules and not args.rules.is_file():
        print(
            f"error: Rules file not found: {args.rules}",
            file=sys.stderr,
        )
        return 1

    _setup_logging(args.verbose)

    pdf_paths = _resolve_file_paths(args.pdfs, "PDF", expected_suffix=".pdf")
    if pdf_paths is None:
        return 1

    output_dir = args.output_dir.resolve() if args.output_dir else None

    # Model and chunking setup (always performed).
    model = MODELS[args.model]
    pages_per_chunk = args.pages_per_chunk
    image_mode = ImageMode(args.image_mode)

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

    try:
        _log.info("pdf2md-claude %s", __version__)
        _log.info("Found %d PDF(s) to process", len(pdf_paths))
        _log.info("Model: %s (%s)", model.display_name, model.model_id)
        _log.info(
            "Chunking: %d pages/chunk (API limit: %d)",
            pages_per_chunk, model.max_pdf_pages,
        )
        if args.from_step:
            _log.info("Starting from: %s (no API calls for earlier steps)", args.from_step)
        if args.cache:
            _log.info("Prompt caching: ENABLED (1h TTL)")

        if output_dir:
            _log.info("Output directory: %s", output_dir)
        else:
            _log.info("Output: next to each PDF")

        # API key validation (always performed).
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            _log.error("ANTHROPIC_API_KEY environment variable not set")
            return 1

        # Ensure output directory exists (if explicitly set).
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        total_start = time.time()
        all_stats: list[DocumentUsageStats] = []
        success = 0
        failure = 0
        cached = 0

        # Pre-resolve rules per PDF (before branching to sequential/parallel).
        rules_cache: dict[Path, str] = {}
        system_prompts: dict[Path, str | None] = {}
        for pdf_path in pdf_paths:
            system_prompts[pdf_path] = _resolve_rules(
                pdf_path, args.rules, rules_cache,
            )

        # Resolve effective worker count.
        jobs = args.jobs
        if jobs <= _JOBS_AUTO:
            # -j without a number (or -j 0): auto = one worker per document.
            jobs = len(pdf_paths)

        if jobs > 1:
            # -- Parallel path -------------------------------------------------
            # Compute aligned prefix width for parallel log output.
            global _doc_prefix_width  # noqa: PLW0603
            _doc_prefix_width = max(len(p.stem) for p in pdf_paths) + 2  # +2 for []

            max_workers = min(jobs, len(pdf_paths))
            if max_workers > 1:
                _log.info("Parallel: %d workers", max_workers)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _convert_one_document,
                        pdf_path,
                        output_dir=output_dir,
                        model=model,
                        api_key=api_key,
                        pages_per_chunk=pages_per_chunk,
                        max_pages=args.max_pages,
                        force=args.force,
                        use_cache=args.cache,
                        max_retries=args.retries,
                        system_prompt=system_prompts[pdf_path],
                        image_mode=image_mode,
                        image_dpi=args.image_dpi,
                        no_images=args.no_images,
                        strip_ai_descriptions=args.strip_ai_descriptions,
                        no_format=args.no_format,
                        from_step=args.from_step,
                    ): pdf_path
                    for pdf_path in pdf_paths
                }
                for future in as_completed(futures):
                    result = future.result()
                    if result.stats is not None:
                        all_stats.append(result.stats)
                    if result.status == "converted":
                        success += 1
                    elif result.status == "cached":
                        cached += 1
                    else:
                        failure += 1
        else:
            # -- Sequential path -----------------------------------------------
            for pdf_path in pdf_paths:
                result = _convert_one_document(
                    pdf_path,
                    output_dir=output_dir,
                    model=model,
                    api_key=api_key,
                    pages_per_chunk=pages_per_chunk,
                    max_pages=args.max_pages,
                    force=args.force,
                    use_cache=args.cache,
                    max_retries=args.retries,
                    system_prompt=system_prompts[pdf_path],
                    image_mode=image_mode,
                    image_dpi=args.image_dpi,
                    no_images=args.no_images,
                    strip_ai_descriptions=args.strip_ai_descriptions,
                    no_format=args.no_format,
                    from_step=args.from_step,
                )
                if result.stats is not None:
                    all_stats.append(result.stats)
                if result.status == "converted":
                    success += 1
                elif result.status == "cached":
                    cached += 1
                else:
                    failure += 1

        total_elapsed = time.time() - total_start
        _log_summary(
            model, all_stats, total_elapsed,
            success, failure, cached,
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
        "validate": _cmd_validate,
        "show-prompt": _cmd_show_prompt,
        "init-rules": _cmd_init_rules,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
