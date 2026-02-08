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
./.venv/bin/python -m pdf2md_claude document.pdf
```

## Key Architecture

All source modules live in `pdf2md_claude/`, tests in `tests/`.

Start from `cli.py` to understand the entry point, then `pipeline.py` for single-document orchestration:

- `pdf2md_claude/cli.py` -- Entry point. Parses args, iterates PDFs, delegates each to `pipeline.convert_document()`, prints summary.
- `pdf2md_claude/pipeline.py` -- Single-document orchestration: creates work directory, calls converter, merges chunks, validates, writes output.
- `pdf2md_claude/workdir.py` -- Chunk persistence and resume. Manages a `.chunks/` directory with manifest-based staleness detection. All cross-chunk data flows through disk (never in memory). Key types: `Manifest`, `ChunkUsageStats`, `WorkDir`.
- `pdf2md_claude/converter.py` -- Chunked PDF conversion with page-aligned context passing. Each chunk is saved to disk immediately via `WorkDir`. On resume, cached chunks are skipped. `_remap_page_markers()` remaps both BEGIN and END markers. Key types: `ChunkResult`, `ConversionResult`.
- `pdf2md_claude/merger.py` -- Deterministic page-marker concatenation (no LLM). Joins disjoint chunks by page number. Also merges continuation tables flagged with `TABLE_CONTINUE` markers into a single `<table>`, preserving page markers inside `<tbody>`.
- `pdf2md_claude/images.py` -- Image extraction and injection. Parses `IMAGE_RECT` bounding-box markers, renders regions from the PDF via pymupdf (two-pass structural matching with raster snap), saves PNG files, and injects `![caption](path)` references. Key types: `ImageRect`, `RenderedImage`.
- `pdf2md_claude/validator.py` -- Post-conversion checks (page markers, page-end matching, image block pairing, tables, heading sequence gaps, binary sequence monotonicity, fabrication detection).
- `pdf2md_claude/markers.py` -- Single source of truth for all HTML comment markers (`PAGE_BEGIN`, `PAGE_END`, `TABLE_CONTINUE`, `IMAGE_BEGIN`, `IMAGE_END`, `IMAGE_RECT`, `IMAGE_AI_GENERATED_DESCRIPTION_*`). All regex patterns and format strings live here.
- `pdf2md_claude/prompt.py` -- Claude prompts. References marker definitions from `markers.py` via f-strings. Uses `{{placeholder}}` for runtime `.format()` values.
- `pdf2md_claude/models.py` -- Model configs, pricing, `DocumentUsageStats`, cost calculation.
- `pdf2md_claude/client.py` -- Anthropic API client setup.

Tests: `tests/test_converter.py`, `tests/test_images.py`, `tests/test_markers.py`, `tests/test_table_merger.py`, `tests/test_validator.py`, `tests/test_workdir.py`.

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
./.venv/bin/python -m pdf2md_claude samples/multi_page_table.pdf --pages-per-chunk 1 -v -f

# Verify work directory was created
ls samples/multi_page_table.chunks/
# Expected: manifest.json, stats.json, chunk_01..04 .md/_context.md/_meta.json

# Resume test: run again without -f (should skip all 4 chunks)
./.venv/bin/python -m pdf2md_claude samples/multi_page_table.pdf --pages-per-chunk 1 -v
```

The `samples/` directory contains `multi_page_table.pdf` (4 pages) for quick pipeline testing.
