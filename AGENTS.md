# Agent Notes for pdf2md-claude

PDF-to-Markdown converter using Claude's native PDF API. Converts PDF documents with chunked conversion, context passing, and validation.

## Dev Setup

Dependencies (`anthropic`, `pymupdf`, `colorlog`, `httpx[socks]`, `pytest`) are only installed in the .venv. Do **not** use the system Python. `httpx[socks]` is required for SOCKS proxy support (the dev environment routes traffic through a local SOCKS proxy).

If `./.venv/` does not exist, run the setup script (creates .venv, installs deps, configures git hooks):

```bash
bash scripts/setup-dev.sh
```

To recreate the .venv from scratch: `bash scripts/setup-dev.sh --force`

Then use the .venv for all commands:

```bash
./.venv/bin/python -m pytest tests/ -v
./.venv/bin/python -m pdf2md_claude convert document.pdf
```

## Key Architecture

All source modules live in `pdf2md_claude/`, tests in `tests/`.

Start from `cli.py` to understand the entry point, then `pipeline.py` for single-document orchestration:

- `pdf2md_claude/cli.py` -- Entry point. Uses subcommands (`convert`, `validate`, `show-prompt`, `init-rules`). Each subcommand has its own handler and only accepts compatible arguments. Shared arg groups (verbose, output, processing, jobs) are defined via argparse parent parsers. All PDF-accepting subcommands (`convert`, `validate`) validate that inputs have a `.pdf` extension. The `convert` handler creates a `ConversionPipeline` (passing API key, model, image/format config flags), then delegates to `pipeline.run()`. Supports `--from merge` to skip chunk conversion API calls and re-merge from cached chunks (loads chunks from disk; post-processing steps like table fixing may still make API calls unless disabled via `--no-fix-tables`). Supports parallel document processing via `-j/--jobs [N]` using `ThreadPoolExecutor`; `-j` alone = one worker per document, `-j N` = exactly N workers; each document is fully independent (own WorkDir, pipeline, output path). Thread-local logging context (`set_document_context`/`clear_document_context`) injects a `[doc_name]` prefix into all log lines from worker threads, with right-padding for alignment. Rules are pre-resolved per-PDF in the main thread before spawning workers. The `validate` handler accepts PDF paths (like `convert`), derives `.md` output paths via `resolve_output()`, and always runs page fidelity checks against the source PDF; supports `-o/--output-dir`.
- `pdf2md_claude/pipeline.py` -- Single-document orchestration via `ConversionPipeline` class. Created per document with `pdf_path`, `output_file`, API config (`api_key`, `model`, `use_cache`, `max_retries`, `system_prompt`), and step config flags (image mode/dpi, format options); derives work directory and image directory paths from `output_file`. The pipeline is self-contained for all API concerns: it lazily creates the Anthropic client, `ClaudeApi`, and `PdfConverter` internally when `run()` needs them. Uses a step-based architecture: processing steps are built internally from config flags via private `_build_steps()` method. Step chain is always: tables → fix-tables → images → strip-ai → format → validate (some conditionally included). Built-in steps: `MergeContinuedTablesStep`, `FixTablesStep`, `ExtractImagesStep`, `StripAIDescriptionsStep`, `FormatMarkdownStep`, `ValidateStep`. The `run()` method provides a unified entry point: full API conversion (`from_step=None`) or re-running from cached chunks (`from_step="merge"`). Both paths share the `_process()` method (merge + steps + write). Instance method `needs_conversion()` checks staleness (uses `self._model` for model staleness detection). `resolve_pages_per_chunk()` reads the workdir manifest to preserve chunk size on resume (warns if CLI value differs; `force=True` bypasses). Free function `resolve_output()` computes the output path before pipeline construction. `ProcessingContext` provides shared mutable state (markdown, validation) and resources (`api`, `work_dir`, `pdf_path`) for all steps; table-fix costs from `FixTablesStep` are accumulated into `DocumentUsageStats` via `ctx.table_fix_stats` for inclusion in the final summary. Key types: `ProcessingContext`, `ProcessingStep` (protocol), `ConversionPipeline`, `PipelineResult`.
- `pdf2md_claude/workdir.py` -- Chunk persistence and resume. Manages a `.staging/` directory with manifest-based staleness detection. Chunks are stored in the `chunks/` subdirectory with per-chunk markdown, context, and metadata files. The merged output is saved as `merged.md` in the staging root (not inside `chunks/`). Also manages a `table_fixer/` subdirectory for persisting table regeneration results: per-table metadata (`.json`), before/after HTML (`_before.html`, `_after.html`), aggregate stats (`stats.json`), and cached output (`output.md`). Files use page-range prefixes for sorting (e.g., `p001-001_table_1.json`, `p003-006_table_23.json`). Provides content hashing utilities (`content_hash()`, `content_hash_glob()`) for cache validation. The `TableFixStats` dataclass includes an `input_hash` field (SHA256 of `merged.md`) to enable output caching; `save_table_fixer_output()` and `load_table_fixer_output()` handle cache I/O. All cross-chunk data flows through disk (never in memory). `load_manifest()` provides lenient manifest reading (returns `None` on missing/corrupt). Key types: `Manifest`, `ChunkUsageStats`, `TableFixResult`, `TableFixStats`, `WorkDir`.
- `pdf2md_claude/claude_api.py` -- Claude API client wrapper. `ClaudeApi` class bundles the Anthropic client with retry logic (exponential backoff on transient errors), streaming response handling, prompt caching support, and optional extended thinking. Provides a single `send_message()` entry point used by all phases that call the Claude API; accepts optional `thinking` parameter for extended thinking config. `_is_retryable()` classifies transient vs. permanent errors. Exposes `model` property for callers to inspect model configuration. Key types: `ClaudeApi`, `ApiResponse`.
- `pdf2md_claude/converter.py` -- Chunked PDF conversion via `PdfConverter` class. Takes a `ClaudeApi` instance and model config; `convert()` splits PDF into chunks with context passing. Each chunk is saved to disk immediately via `WorkDir`. On resume, cached chunks are skipped. `_remap_page_markers()` remaps both BEGIN and END markers. Key types: `PdfConverter`, `ChunkResult`, `ConversionResult`.
- `pdf2md_claude/merger.py` -- Deterministic page-marker concatenation (no LLM). Joins disjoint chunks by page number. Also merges continuation tables flagged with `TABLE_CONTINUE` markers into a single `<table>`, preserving page markers inside `<tbody>`.
- `pdf2md_claude/images.py` -- Image extraction and injection via `ImageExtractor` class. Holds PDF path, output dir, image mode, DPI; `extract_and_inject()` parses `IMAGE_RECT` markers, renders regions from the PDF via pymupdf (two-pass structural matching with raster snap), saves PNG files, and injects `![caption](path)` references. Key types: `ImageExtractor`, `ImageRect`, `RenderedImage`.
- `pdf2md_claude/formatter.py` -- Markdown and HTML table formatter. Prettifies `<table>` blocks with consistent 2-space indentation using stdlib `html.parser`, normalizes blank lines and trailing whitespace. Pure function `format_markdown()` plus `FormatMarkdownStep` for the pipeline. Enabled by default (`--no-format` to skip).
- `pdf2md_claude/table_fixer.py` -- AI-based table regeneration from PDF with output caching. `FixTablesStep` detects complex tables with colspan/rowspan attributes (via `find_complex_tables()`), regenerates each from source PDF pages using comprehensive table conversion rules (`_RULE_TABLES` from `prompt.py`) with extended thinking for improved accuracy. Caches the post-fix output keyed by SHA256 hash of `merged.md`; on cache hit (matching hash in `table_fixer/stats.json` + `output.md` present), skips all API calls and loads cached result. Replaces complex tables in-place. Uses extended thinking (adaptive for models with `supports_adaptive_thinking=True`, budget-based for others) to improve structural analysis of merged cells. Enabled by default; use `--no-fix-tables` to disable (table fixing makes additional API calls). `fix_single_table()` encapsulates per-table logic (PDF extraction, prompt building, API call, response parsing, timing/cost tracking). `_build_thinking_config()` selects appropriate thinking mode based on `ModelConfig.supports_adaptive_thinking`. Requires `ProcessingContext.api` and `ProcessingContext.pdf_path`; skips gracefully if either is `None`. Tables are processed in reverse order to preserve string offsets during replacement. Key types: `ComplexTable`, `FixTablesStep`, `find_complex_tables()`, `fix_single_table()`.
- `pdf2md_claude/validator.py` -- Post-conversion checks (page markers, page-end matching, image block pairing, tables, figures, heading sequence gaps, duplicate headings, binary sequence monotonicity, table column consistency, fabrication detection). `check_table_column_consistency()` validates table structure by computing effective column counts with colspan/rowspan tracking. Exposes public helper functions `table_page_numbers()` and `find_table_title()` for use by other modules (e.g., table_fixer).
- `pdf2md_claude/markers.py` -- Single source of truth for all HTML comment markers (`PAGE_BEGIN`, `PAGE_END`, `TABLE_CONTINUE`, `PAGE_SKIP`, `IMAGE_BEGIN`, `IMAGE_END`, `IMAGE_RECT`, `IMAGE_AI_DESC_BEGIN`, `IMAGE_AI_DESC_END`). Every marker is a `MarkerDef` instance; all regex patterns and format strings live here.
- `pdf2md_claude/prompt.py` -- Claude prompts. References marker definitions from `markers.py` via f-strings. Uses `{{placeholder}}` for runtime `.format()` values.
- `pdf2md_claude/rules.py` -- Custom rules file support. Parses user rules files (`@replace`, `@append`, `@add`, `@add after`), builds custom system prompts, and generates rules templates. Key types: `RulesFileResult`.
- `pdf2md_claude/models.py` -- Model configs, pricing, `DocumentUsageStats`, cost calculation.
- `pdf2md_claude/client.py` -- Anthropic API client setup.

Tests: `tests/conftest.py`, `tests/test_claude_api.py`, `tests/test_cli.py`, `tests/test_converter.py`, `tests/test_formatter.py`, `tests/test_images.py`, `tests/test_markers.py`, `tests/test_models.py`, `tests/test_pipeline.py`, `tests/test_rules.py`, `tests/test_table_fixer.py`, `tests/test_table_merger.py`, `tests/test_validator.py`, `tests/test_workdir.py`.

## Code Conventions

- **Markers and shared regexes belong in `markers.py`.** Do not define marker-related regex patterns locally in other modules — import from `markers.py`. This includes HTML-comment markers (`PAGE_BEGIN`, `PAGE_END`, etc.) and shared HTML patterns like `TABLE_BLOCK_RE`. When building regexes that reference marker tags, use `MarkerDef.tag` (e.g. `re.escape(PAGE_BEGIN.tag)`) instead of hardcoding the string `"PDF_PAGE_BEGIN"`.
- **No magic numbers.** Extract repeated or meaningful literals into named constants (module-level or class-level). Examples: `DEFAULT_IMAGE_DPI`, `_CACHE_CONTROL`, `_IMAGE_DIR_SUFFIX`, `_SUMMARY_SEP`.
- **No redundant variables.** If an object already exposes the value (e.g. `work_dir.path`), don't recompute it into a separate variable.

## Quick Verification

Unit tests (no API key needed):

```bash
./.venv/bin/python -m pytest tests/ -v
```

End-to-end with sample PDF (requires `ANTHROPIC_API_KEY`):

```bash
# Fresh conversion (4-page sample, 1 page/chunk = 4 API calls)
./.venv/bin/python -m pdf2md_claude convert samples/multi_page_table.pdf --pages-per-chunk 1 -v -f

# Verify work directory was created
ls samples/multi_page_table.staging/
# Expected: manifest.json, merged.md at root
ls samples/multi_page_table.staging/chunks/
# Expected: stats.json, chunk_01..04 .md/_context.md/_meta.json

# Resume test: run again without -f (should skip all 4 chunks)
./.venv/bin/python -m pdf2md_claude convert samples/multi_page_table.pdf --pages-per-chunk 1 -v
```

The `samples/` directory contains `multi_page_table.pdf` (4 pages) for quick pipeline testing.
