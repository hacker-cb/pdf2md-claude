"""AI-based table regeneration from PDF.

Detects HTML tables with complex structure (colspan/rowspan) and regenerates
each from source PDF pages using the full table conversion rules with
extended thinking for improved accuracy.

The regenerated table HTML is injected back into the markdown, replacing
the original table in-place.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pdf2md_claude.claude_api import ClaudeApi, ApiResponse
from pdf2md_claude.converter import extract_pdf_pages
from pdf2md_claude.markers import TABLE_BLOCK_RE
from pdf2md_claude.models import ModelConfig, calculate_cost
from pdf2md_claude.prompt import TABLE_FIX_SYSTEM_PROMPT
from pdf2md_claude.validator import find_table_title, table_page_numbers
from pdf2md_claude.workdir import TableFixResult, TableFixStats

if TYPE_CHECKING:
    from pdf2md_claude.pipeline import ProcessingContext

_log = logging.getLogger("table_fixer")

_CONTEXT_LINES_BEFORE = 3
"""Number of non-empty lines to include before the table for context."""

_CONTEXT_LINES_AFTER = 3
"""Number of non-empty lines to include after the table for context."""

_THINKING_BUDGET = 10_000
"""Token budget for extended thinking (non-Opus 4.6 models)."""

_HAS_SPAN_RE = re.compile(r'(?:col|row)span\s*=', re.IGNORECASE)
"""Regex to detect colspan or rowspan attributes in table HTML."""


@dataclass
class ComplexTable:
    """A table with complex structure (colspan/rowspan) detected for regeneration.

    Used by :func:`find_complex_tables` to return structured data about
    complex tables for AI-based regeneration with extended thinking.
    """

    table_html: str
    """Full ``<table>...</table>`` HTML block."""

    match_start: int
    """Start position of the table in the markdown string."""

    match_end: int
    """End position of the table in the markdown string."""

    page_numbers: list[int]
    """PDF page numbers this table spans."""

    label: str
    """Human-readable label (e.g. ``"Table 6"`` or ``"HTML table"``)."""


def find_complex_tables(markdown: str) -> list[ComplexTable]:
    """Find all HTML tables with colspan or rowspan attributes.

    Complex tables with merged cells benefit from AI regeneration with
    extended thinking for improved structural accuracy.

    Args:
        markdown: Markdown content with embedded HTML tables.

    Returns:
        List of :class:`ComplexTable` objects, one per table with
        colspan/rowspan. Empty list if no complex tables found.
    """
    complex_tables: list[ComplexTable] = []

    for table_match in TABLE_BLOCK_RE.finditer(markdown):
        _log.debug("  Scanning table at position %d-%d", table_match.start(), table_match.end())
        table_html = table_match.group(0)

        # Check if table contains colspan or rowspan
        if not _HAS_SPAN_RE.search(table_html):
            _log.debug("    Simple table (no colspan/rowspan), skipping")
            continue

        # Resolve page numbers and label
        page_numbers = table_page_numbers(
            markdown, table_match.start(), table_match.end()
        )
        title = find_table_title(markdown, table_match.start())
        label = title if title else "HTML table"

        _log.debug("    Complex table detected: %s (pages: %s, %d chars)", 
                  label, page_numbers, len(table_html))

        complex_tables.append(ComplexTable(
            table_html=table_html,
            match_start=table_match.start(),
            match_end=table_match.end(),
            page_numbers=page_numbers,
            label=label,
        ))

    return complex_tables


def _extract_context_lines(
    markdown: str, position: int, num_lines: int, before: bool
) -> str:
    """Extract N non-empty lines before or after a position.

    Args:
        markdown: Full markdown content.
        position: Character offset (table start or end).
        num_lines: Number of non-empty lines to extract.
        before: If True, extract lines before position; if False, after.

    Returns:
        Extracted context as a string.
    """
    lines = markdown.split('\n')
    
    # Find which line contains the position (count newlines before it).
    # This is deterministic and handles boundary cases correctly.
    target_line_idx = markdown[:position].count('\n')
    
    # Collect non-empty lines
    context_lines = []
    if before:
        # Search backwards
        idx = target_line_idx - 1
        while idx >= 0 and len(context_lines) < num_lines:
            line = lines[idx].strip()
            if line:  # Non-empty line
                context_lines.insert(0, lines[idx])  # Preserve original formatting
            idx -= 1
    else:
        # Search forwards
        idx = target_line_idx + 1
        while idx < len(lines) and len(context_lines) < num_lines:
            line = lines[idx].strip()
            if line:  # Non-empty line
                context_lines.append(lines[idx])  # Preserve original formatting
            idx += 1
    
    _log.debug("    Extracted %d context lines (%s)", 
              len(context_lines), "before" if before else "after")
    return '\n'.join(context_lines)


def _build_thinking_config(model: ModelConfig) -> dict:
    """Build extended thinking configuration for the given model.

    Uses adaptive thinking for models that support it (e.g., Opus 4.6),
    budget-based thinking for others.

    Args:
        model: Model configuration.

    Returns:
        Thinking config dict suitable for the Anthropic API.
    """
    if model.supports_adaptive_thinking:
        _log.debug("  Using adaptive thinking for %s", model.model_id)
        return {"type": "adaptive"}
    _log.debug("  Using budget thinking (%d tokens) for %s", 
              _THINKING_BUDGET, model.model_id)
    return {"type": "enabled", "budget_tokens": _THINKING_BUDGET}


def fix_single_table(
    api: ClaudeApi,
    pdf_path: Path,
    table: ComplexTable,
    markdown: str,
) -> tuple[str, ApiResponse, float, float] | None:
    """Regenerate one complex table from PDF with Claude.

    Extracts the relevant PDF pages, builds a regeneration prompt with
    the original table HTML as reference, and parses Claude's response
    to extract the regenerated ``<table>...</table>`` block.

    Args:
        api: Claude API client for sending the regeneration request.
        pdf_path: Path to the source PDF (for page extraction).
        table: Detected complex table (with colspan/rowspan).
        markdown: Full markdown content (for extracting surrounding context).

    Returns:
        Tuple of (regenerated_html, api_response, elapsed_seconds, cost), or ``None``
        if regeneration failed.
    """
    # --- extract PDF pages ---------------------------------------------
    if not table.page_numbers:
        _log.warning(
            "  %s: no page numbers available, skipping", table.label
        )
        return None

    page_start = min(table.page_numbers)
    page_end = max(table.page_numbers)

    try:
        pdf_base64 = extract_pdf_pages(pdf_path, page_start, page_end)
        _log.debug("    Extracted %d PDF pages (%.0f KB base64)", 
                  page_end - page_start + 1, len(pdf_base64) / 1024)
    except Exception as e:
        _log.error(
            "  %s: failed to extract PDF pages %d-%d: %s",
            table.label, page_start, page_end, e,
        )
        return None

    # --- build regeneration prompt -------------------------------------
    # Extract surrounding context for title/structure awareness.
    before_context = _extract_context_lines(
        markdown, table.match_start, _CONTEXT_LINES_BEFORE, before=True
    )
    after_context = _extract_context_lines(
        markdown, table.match_end, _CONTEXT_LINES_AFTER, before=False
    )
    _log.debug("    Context: %d lines before, %d lines after",
              len(before_context.split('\n')) if before_context else 0,
              len(after_context.split('\n')) if after_context else 0)

    user_message = f"""\
Please regenerate this complex table by reading directly from the PDF pages.

**Table identification:** {table.label}

**Previous extraction (for reference only — complex table with merged cells):**
```html
{table.table_html}
```

**Context before:**
{before_context}

**Context after:**
{after_context}

Generate the complete, correctly structured table from the PDF with proper colspan/rowspan attributes.
"""

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_base64,
                    },
                },
                {
                    "type": "text",
                    "text": user_message,
                },
            ],
        },
    ]

    # --- call Claude API -----------------------------------------------
    _log.info(
        "  %s: regenerating complex table from PDF (pages %d-%d)",
        table.label, page_start, page_end,
    )

    # Build extended thinking config for better structural analysis.
    thinking = _build_thinking_config(api.model)
    _log.debug("    Thinking config: %s", thinking)

    # Callback to log thinking deltas in real-time (verbose mode).
    thinking_buffer = []
    def log_thinking_delta(delta: str):
        thinking_buffer.append(delta)
        # Log in chunks of ~100 chars to avoid spam.
        if sum(len(s) for s in thinking_buffer) >= 100:
            _log.debug("      thinking: %s", "".join(thinking_buffer))
            thinking_buffer.clear()

    _log.debug("    Calling Claude API with thinking enabled...")
    start_time = time.time()
    try:
        response = api.send_message(
            system=TABLE_FIX_SYSTEM_PROMPT,
            messages=messages,
            retry_context=table.label,
            thinking=thinking,
            on_thinking_delta=log_thinking_delta,
        )
    except Exception as e:
        _log.error("  %s: API call failed: %s", table.label, e)
        return None
    elapsed = time.time() - start_time

    # Flush any remaining thinking buffer.
    if thinking_buffer:
        _log.debug("      thinking: %s", "".join(thinking_buffer))

    # --- parse regenerated table ---------------------------------------
    corrected_match = TABLE_BLOCK_RE.search(response.markdown)
    if not corrected_match:
        _log.warning(
            "  %s: Claude's response does not contain a <table> block",
            table.label,
        )
        return None

    corrected_html = corrected_match.group(0)
    _log.debug("    Parsed regenerated table: %d chars", len(corrected_html))
    
    # Calculate cost for this table
    cost = calculate_cost(
        api.model,
        response.input_tokens,
        response.output_tokens,
        response.cache_creation_tokens,
        response.cache_read_tokens,
    )
    
    _log.info(
        "  %s: received regenerated table (%d chars, %d in / %d out tokens, $%.4f, %.1fs)",
        table.label,
        len(corrected_html),
        response.input_tokens + response.cache_creation_tokens + response.cache_read_tokens,
        response.output_tokens,
        cost,
        elapsed,
    )
    return corrected_html, response, elapsed, cost


@dataclass
class FixTablesStep:
    """Pipeline step: regenerate complex tables from PDF.

    Detects complex tables with colspan/rowspan attributes,
    regenerates each from source PDF pages using comprehensive table conversion
    rules with extended thinking for improved accuracy.

    Uses extended thinking (adaptive for Opus 4.6, budget-based for other models)
    to improve structural analysis of merged cells.

    Requires :attr:`~pdf2md_claude.pipeline.ProcessingContext.api` and
    :attr:`~pdf2md_claude.pipeline.ProcessingContext.pdf_path` to be set.
    Skips gracefully if either is ``None``.
    """

    @property
    def name(self) -> str:
        return "fix tables"

    @property
    def key(self) -> str:
        return "fix-tables"

    def run(self, ctx: ProcessingContext) -> None:
        """Regenerate complex tables from the source PDF."""
        # --- detect complex tables -------------------------------------
        complex_tables = find_complex_tables(ctx.markdown)
        if not complex_tables:
            _log.debug("No complex tables detected")
            return

        _log.info("Found %d complex table(s) with colspan/rowspan", len(complex_tables))
        _log.debug("  Details: %s", 
                  ", ".join(f"{t.label} (p{min(t.page_numbers) if t.page_numbers else '?'}-{max(t.page_numbers) if t.page_numbers else '?'})" 
                            for t in complex_tables[:5]))  # First 5
        if len(complex_tables) > 5:
            _log.debug("           ... and %d more", len(complex_tables) - 5)

        # --- guard: API and PDF path required --------------------------
        if ctx.api is None:
            _log.warning(
                "API client not available (test context?), skipping table fixes"
            )
            return

        if ctx.pdf_path is None:
            _log.warning(
                "PDF path not available, skipping table fixes"
            )
            return

        # --- cache check (before clearing old results) ----------------
        input_hash = ""
        if ctx.work_dir is not None:
            input_hash = ctx.work_dir.content_hash_glob("merged.md")
            cached_stats = ctx.work_dir.load_table_fix_stats()
            if (cached_stats is not None
                    and cached_stats.input_hash == input_hash
                    and input_hash):
                cached_output = ctx.work_dir.load_table_fixer_output()
                if cached_output is not None:
                    ctx.markdown = cached_output
                    ctx.table_fix_stats = cached_stats
                    _log.info(
                        "Table fixes cached (%d tables, $%.4f)",
                        cached_stats.tables_fixed, cached_stats.total_cost,
                    )
                    return

        # --- cache miss: clear and re-process -------------------------
        if ctx.work_dir is not None:
            ctx.work_dir.clear_table_fixer()
            _log.debug("  Cleared old table-fixer results from work directory")

        # --- process tables in reverse order (preserve offsets) --------
        _log.debug("  Processing tables in reverse order to preserve string offsets")
        fixed_count = 0
        total_input = 0
        total_output = 0
        total_cost = 0.0
        total_elapsed = 0.0
        
        # Create index mapping for reversed iteration
        indexed_tables = list(enumerate(complex_tables))
        
        for index, table in reversed(indexed_tables):
            result = fix_single_table(
                ctx.api, ctx.pdf_path, table, ctx.markdown
            )
            if result is None:
                _log.warning("  %s: regeneration failed, leaving unchanged", table.label)
                continue
            
            corrected_html, response, elapsed, cost = result  # unpack
            _log.debug("    Replaced table %d/%d: %d → %d chars", 
                      index + 1, len(complex_tables), 
                      len(table.table_html), len(corrected_html))
            
            # Accumulate stats
            total_input += (
                response.input_tokens +
                response.cache_creation_tokens +
                response.cache_read_tokens
            )
            total_output += response.output_tokens
            total_cost += cost
            total_elapsed += elapsed

            # Persist result to work directory
            if ctx.work_dir is not None:
                fix_result = TableFixResult(
                    index=index,
                    label=table.label,
                    page_numbers=table.page_numbers,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cache_creation_tokens=response.cache_creation_tokens,
                    cache_read_tokens=response.cache_read_tokens,
                    cost=cost,
                    elapsed_seconds=elapsed,
                    before_chars=len(table.table_html),
                    after_chars=len(corrected_html),
                )
                ctx.work_dir.save_table_fix(fix_result, table.table_html, corrected_html)
                _log.debug("    Saved table fix result to work directory")

            # Replace the complex table in-place.
            ctx.markdown = (
                ctx.markdown[:table.match_start]
                + corrected_html
                + ctx.markdown[table.match_end:]
            )
            fixed_count += 1
            _log.debug("  Progress: %d/%d tables regenerated", 
                      fixed_count, len(complex_tables))

        # Always create and set aggregate stats
        aggregate_stats = TableFixStats(
            tables_found=len(complex_tables),
            tables_fixed=fixed_count,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost=total_cost,
            total_elapsed_seconds=total_elapsed,
            input_hash=input_hash,
        )
        ctx.table_fix_stats = aggregate_stats

        # Persist to disk only when work_dir is available
        if ctx.work_dir is not None:
            ctx.work_dir.save_table_fix_stats(aggregate_stats)
            ctx.work_dir.save_table_fixer_output(ctx.markdown)
            _log.debug("  Saved aggregate stats and output to work directory")
        
        _log.info(
            "Regenerated %d of %d complex table(s) — %d input, %d output tokens, $%.4f, %.1fs",
            fixed_count,
            len(complex_tables),
            total_input,
            total_output,
            total_cost,
            total_elapsed,
        )
