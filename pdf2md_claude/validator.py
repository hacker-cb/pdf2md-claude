"""Content validation for converted markdown output.

Checks for common problems in Claude's PDF-to-Markdown conversion:
- Page marker validation (BEGIN/END matching, monotonicity, gaps)
- Image block pairing (IMAGE_BEGIN/IMAGE_END)
- Heading sequence gaps at all depth levels (missing sections/subsections)
- Duplicate numbered headings (same section number appears more than once)
- Section ordering continuity (backward jumps in section numbering)
- Missing tables (referenced but not defined)
- Missing figures (referenced but not defined)
- Non-monotonic or duplicate binary values in HTML tables
- Table column-count consistency (rowspan/colspan mismatch detection)
- Fabricated summaries (Claude inventing text to replace omitted content)
- Per-page fidelity check against PDF source text (optional, needs PDF path)
"""

from __future__ import annotations

import bisect
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pdf2md_claude.markers import (
    IMAGE_AI_DESCRIPTION_BLOCK_RE,
    IMAGE_BEGIN,
    IMAGE_END,
    PAGE_BEGIN,
    PAGE_END,
    PAGE_SKIP,
    TABLE_BLOCK_RE,
)

_log = logging.getLogger("validator")


# ---------------------------------------------------------------------------
# Warning / error category constants
# ---------------------------------------------------------------------------

CAT_PAGE_MARKERS = "Page markers"
CAT_IMAGE_BLOCKS = "Image block pairing"
CAT_SECTION_GAP = "Section gap"
CAT_DUPLICATE_HEADINGS = "Duplicate headings"
CAT_SECTION_ORDERING = "Section ordering"
CAT_MISSING_REFERENCE = "Missing table/figure reference"
CAT_TABLE_COLUMNS = "Table column consistency"
CAT_BINARY_SEQUENCE = "Binary sequence"
CAT_FABRICATION = "Fabrication detection"
CAT_PAGE_FIDELITY = "Page fidelity"


@dataclass
class ValidationResult:
    """Result of validating a converted markdown document.

    Errors and warnings are stored as ``(category, message)`` tuples
    to enable grouped reporting in the validation summary.
    """

    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no errors (warnings are tolerable)."""
        return len(self.errors) == 0

    @property
    def warning_messages(self) -> list[str]:
        """Plain warning message strings (without category)."""
        return [msg for _, msg in self.warnings]

    @property
    def error_messages(self) -> list[str]:
        """Plain error message strings (without category)."""
        return [msg for _, msg in self.errors]

    def log_all(self) -> None:
        """Log all errors, warnings, and informational messages."""
        for _, e in self.errors:
            _log.error("  ✗ %s", e)
        for _, w in self.warnings:
            _log.warning("  ⚠ %s", w)
        for i in self.info:
            _log.info("  ℹ %s", i)


# Page markers: <!-- PDF_PAGE_BEGIN 42 --> / <!-- PDF_PAGE_END 42 -->
_PAGE_MARKER_RE = PAGE_BEGIN.re_value
_PAGE_END_MARKER_RE = PAGE_END.re_value


# ---------------------------------------------------------------------------
# Page-position helper — resolve the current page at any string offset
# ---------------------------------------------------------------------------


class _PageIndex:
    """Pre-built index for fast page-number lookup by string position.

    Scans the markdown once for all ``PAGE_BEGIN`` markers and builds a
    sorted list of ``(position, page_number)`` pairs.  Individual lookups
    are O(log n) via :func:`bisect.bisect_right`.
    """

    __slots__ = ("_positions", "_pages")

    def __init__(self, markdown: str) -> None:
        self._positions: list[int] = []
        self._pages: list[int] = []
        for m in _PAGE_MARKER_RE.finditer(markdown):
            self._positions.append(m.start())
            self._pages.append(int(m.group(1)))

    def page_at(self, pos: int) -> int | None:
        """Return the page number of the last PAGE_BEGIN before *pos*."""
        idx = bisect.bisect_right(self._positions, pos) - 1
        if idx < 0:
            return None
        return self._pages[idx]

    def format_page(self, pos: int) -> str:
        """Return ``' (page N)'`` or ``''`` if page is unknown."""
        page = self.page_at(pos)
        return f" (page {page})" if page is not None else ""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def validate_output(markdown: str) -> ValidationResult:
    """Check converted markdown for common problems.

    Args:
        markdown: The converted markdown content.

    Returns:
        ValidationResult with errors and warnings.
    """
    result = ValidationResult()

    # Structural markers
    _check_page_markers(markdown, result)
    _check_page_end_markers(markdown, result)
    _check_image_block_pairing(markdown, result)

    # Document outline
    _check_heading_sequence(markdown, result)
    _check_duplicate_headings(markdown, result)
    _check_section_continuity(markdown, result)

    # Content references
    _check_missing_tables(markdown, result)
    _check_missing_figures(markdown, result)

    # Table content quality
    _check_binary_sequences(markdown, result)
    check_table_column_consistency(markdown, result)

    # Add info message about table validation
    table_count = len(TABLE_BLOCK_RE.findall(markdown))
    if table_count > 0:
        result.info.append(
            f"Tables checked: {table_count} table{'s' if table_count != 1 else ''}"
        )

    # Content integrity
    _check_fabrication(markdown, result)

    return result


# ---------------------------------------------------------------------------
# Structural markers
# ---------------------------------------------------------------------------


def _count_skipped_pages(markdown: str) -> int:
    """Count pages containing a PDF_PAGE_SKIP marker."""
    return len(PAGE_SKIP.re.findall(markdown))


def _check_page_markers(markdown: str, result: ValidationResult) -> None:
    """Verify that page markers are present, sequential, and without large gaps."""
    markers = _PAGE_MARKER_RE.findall(markdown)

    if not markers:
        result.errors.append((CAT_PAGE_MARKERS, "No page markers found in output"))
        return

    pages = [int(m) for m in markers]

    # Count intentionally-skipped pages (PDF_PAGE_SKIP markers).
    skipped = _count_skipped_pages(markdown)
    skip_suffix = f" ({skipped} skipped)" if skipped else ""
    result.info.append(
        f"Page markers found: {len(pages)} markers, "
        f"range {min(pages)}-{max(pages)}{skip_suffix}"
    )

    # Check for non-monotonic markers (pages going backward) — report ALL.
    for i in range(1, len(pages)):
        if pages[i] < pages[i - 1]:
            result.errors.append((
                CAT_PAGE_MARKERS,
                f"Page markers not monotonic: page {pages[i]} "
                f"follows page {pages[i - 1]}",
            ))

    # Check for gaps (every page should have a marker).
    for i in range(1, len(pages)):
        gap = pages[i] - pages[i - 1]
        if gap > 1:
            result.errors.append((
                CAT_PAGE_MARKERS,
                f"Missing page marker(s): page {pages[i - 1]} jumps to "
                f"page {pages[i]} (missing {gap - 1} page(s))",
            ))


def _check_page_end_markers(markdown: str, result: ValidationResult) -> None:
    """Verify that PDF_PAGE_END markers match PDF_PAGE_BEGIN markers."""
    begin_pages = [int(m) for m in _PAGE_MARKER_RE.findall(markdown)]
    end_pages = [int(m) for m in _PAGE_END_MARKER_RE.findall(markdown)]

    if not end_pages:
        if begin_pages:
            result.errors.append((
                CAT_PAGE_MARKERS,
                "No PDF_PAGE_END markers found (PDF_PAGE_BEGIN markers present)",
            ))
        return

    result.info.append(
        f"Page end markers found: {len(end_pages)} markers, "
        f"range {min(end_pages)}-{max(end_pages)}"
    )

    # Every END page should have a matching BEGIN page.
    begin_set = set(begin_pages)
    end_set = set(end_pages)
    unmatched_ends = end_set - begin_set
    if unmatched_ends:
        for p in sorted(unmatched_ends):
            result.errors.append((
                CAT_PAGE_MARKERS,
                f"PDF_PAGE_END {p} has no matching PDF_PAGE_BEGIN",
            ))

    # Every BEGIN page should have a matching END page.
    missing_ends = begin_set - end_set
    if missing_ends:
        for p in sorted(missing_ends):
            result.errors.append((
                CAT_PAGE_MARKERS,
                f"PDF_PAGE_BEGIN {p} has no matching PDF_PAGE_END",
            ))


def _check_image_block_pairing(markdown: str, result: ValidationResult) -> None:
    """Verify that IMAGE_BEGIN and IMAGE_END markers are properly paired.

    Checks for:
    - Unmatched IMAGE_BEGIN (opened but never closed).
    - Unmatched IMAGE_END (closed without a preceding open).
    - Nested IMAGE_BEGIN (opening inside an already-open block).
    """
    current_page: int | None = None
    in_block = False
    open_page: int | None = None
    begin_count = 0
    end_count = 0

    for line in markdown.splitlines():
        page_match = _PAGE_MARKER_RE.search(line)
        if page_match:
            current_page = int(page_match.group(1))

        if IMAGE_BEGIN.re.search(line):
            begin_count += 1
            if in_block:
                loc = f" (page {open_page})" if open_page else ""
                result.errors.append((
                    CAT_IMAGE_BLOCKS,
                    f"Nested IMAGE_BEGIN on page {current_page} — "
                    f"previous block opened{loc} was not closed",
                ))
            in_block = True
            open_page = current_page

        if IMAGE_END.re.search(line):
            end_count += 1
            if not in_block:
                result.errors.append((
                    CAT_IMAGE_BLOCKS,
                    f"IMAGE_END without matching IMAGE_BEGIN "
                    f"on page {current_page}",
                ))
            in_block = False
            open_page = None

    # Trailing unclosed block.
    if in_block:
        loc = f" on page {open_page}" if open_page else ""
        result.errors.append((
            CAT_IMAGE_BLOCKS,
            f"IMAGE_BEGIN{loc} was never closed with IMAGE_END",
        ))

    if begin_count or end_count:
        result.info.append(
            f"Image blocks: {begin_count} IMAGE_BEGIN, {end_count} IMAGE_END"
        )


# ---------------------------------------------------------------------------
# Document outline (section structure)
# ---------------------------------------------------------------------------

# Section heading pattern: matches numbered (9.2.1) and lettered (A.1, B.2.1)
# section identifiers at the start of Markdown headings.
_SECTION_HEADING_RE = re.compile(
    r"^#{1,6}\s+((?:[A-Z]|\d+)(?:\.(?:[A-Z]|\d+))*)\s+", re.MULTILINE
)


def _section_sort_key(section: str) -> tuple:
    """Sort key for section numbers: numeric parts by value, letters after."""
    parts = section.split(".")
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p) for p in parts
    )


def _check_heading_sequence(markdown: str, result: ValidationResult) -> None:
    """Warn if numbered section headings have gaps at any depth level.

    Groups sections by their parent prefix (e.g. all ``3.x`` sections share
    parent ``"3"``, all ``9.5.x`` sections share parent ``"9.5"``) and checks
    each group for numeric gaps in the last component.  Duplicate section
    numbers (from overlapping chunks) are deduplicated before gap checking.
    """
    matches = list(_SECTION_HEADING_RE.finditer(markdown))

    if len(matches) < 2:
        return

    # Group sections by parent prefix.
    # Key: parent prefix string ("" for top-level, "3" for 3.x, "9.5" for 9.5.x).
    # Value: list of (last_numeric_component, match_position).
    siblings: dict[str, list[tuple[int, int]]] = {}

    for m in matches:
        heading = m.group(1)
        parts = heading.split(".")
        last = parts[-1]
        if not last.isdigit():
            continue  # Skip lettered components (e.g. annex "A")
        parent = ".".join(parts[:-1])
        siblings.setdefault(parent, []).append((int(last), m.start()))

    pidx: _PageIndex | None = None

    for parent, entries in siblings.items():
        # Deduplicate: keep only the first occurrence of each number.
        seen: dict[int, int] = {}
        for num, pos in entries:
            if num not in seen:
                seen[num] = pos
        sorted_entries = sorted(seen.items())

        if len(sorted_entries) < 2:
            continue

        for i in range(1, len(sorted_entries)):
            cur_num, cur_pos = sorted_entries[i]
            prev_num, _prev_pos = sorted_entries[i - 1]
            gap = cur_num - prev_num
            if gap > 1:
                if pidx is None:
                    pidx = _PageIndex(markdown)
                page_suffix = pidx.format_page(cur_pos)
                # Build full section identifiers.
                prev_id = f"{parent}.{prev_num}" if parent else str(prev_num)
                cur_id = f"{parent}.{cur_num}" if parent else str(cur_num)
                result.warnings.append((
                    CAT_SECTION_GAP,
                    f"Section gap: section {prev_id} jumps to "
                    f"section {cur_id} (missing {gap - 1} section(s))"
                    f"{page_suffix}",
                ))


def _check_duplicate_headings(markdown: str, result: ValidationResult) -> None:
    """Warn if numbered section headings appear more than once.

    Wrapped standards PDFs (e.g., national-body wrappers around core content)
    often contain front-matter sections duplicated inside the wrapper.
    This check detects identical section numbers that appear more than
    once in the merged output, which usually indicates overlapping content
    between a wrapper and the embedded document.

    Each duplicate is reported on its own line with the PDF page numbers
    where it appears.
    """
    # Build a map: section_number -> list of page numbers.
    current_page: int | None = None
    occurrences: dict[str, list[int]] = {}

    for line in markdown.splitlines():
        # Track current page from PAGE_BEGIN markers.
        page_match = _PAGE_MARKER_RE.search(line)
        if page_match:
            current_page = int(page_match.group(1))
            continue

        heading_match = _SECTION_HEADING_RE.match(line)
        if heading_match:
            section = heading_match.group(1)
            page = current_page if current_page is not None else 0
            occurrences.setdefault(section, []).append(page)

    # Filter to duplicates only.
    duplicates = {s: pages for s, pages in occurrences.items() if len(pages) > 1}
    if not duplicates:
        return

    sorted_sections = sorted(duplicates.keys(), key=_section_sort_key)
    result.warnings.append((
        CAT_DUPLICATE_HEADINGS,
        f"Duplicate section headings: {len(sorted_sections)} sections "
        f"appear more than once",
    ))
    for section in sorted_sections:
        pages = duplicates[section]
        page_str = ", ".join(f"p{p}" for p in pages)
        result.warnings.append((
            CAT_DUPLICATE_HEADINGS,
            f"  Section {section} appears {len(pages)} times "
            f"(pages: {page_str})",
        ))


def _check_section_continuity(markdown: str, result: ValidationResult) -> None:
    """Check that section headings follow monotonically non-decreasing order.

    Detects backward jumps in section numbering (e.g. section 4.7 followed
    by section 3.24), which typically indicates overlapping chunk content
    during chunked PDF conversion.

    Equal consecutive sections are ignored here — they are already caught
    by :func:`_check_duplicate_headings`.
    """
    matches = list(_SECTION_HEADING_RE.finditer(markdown))

    if len(matches) < 2:
        return

    pidx: _PageIndex | None = None
    for i in range(1, len(matches)):
        cur_section = matches[i].group(1)
        prev_section = matches[i - 1].group(1)
        cur_key = _section_sort_key(cur_section)
        prev_key = _section_sort_key(prev_section)

        if cur_key < prev_key:
            if pidx is None:
                pidx = _PageIndex(markdown)
            page_suffix = pidx.format_page(matches[i].start())
            result.warnings.append((
                CAT_SECTION_ORDERING,
                f"Section ordering: {cur_section} follows "
                f"{prev_section} (backward jump){page_suffix}",
            ))


# ---------------------------------------------------------------------------
# Content references (completeness)
# ---------------------------------------------------------------------------

# Table reference: "Table 17" or "Table B.1" etc.
_TABLE_REF_RE = re.compile(r"\bTable\s+(\d+|[A-Z]\.\d+)\b")

# Table definition: **Table 17 – Something** (bold heading above the table)
_TABLE_DEF_RE = re.compile(r"\*\*Table\s+(\d+|[A-Z]\.\d+)\s*[–—-]")

# Figure reference: "Figure 5" or "Figure A.1" etc.
_FIGURE_REF_RE = re.compile(r"\bFigure\s+(\d+|[A-Z]\.\d+)\b")

# Figure definition: **Figure 5 – Something** (bold caption in IMAGE block)
_FIGURE_DEF_RE = re.compile(r"\*\*Figure\s+(\d+|[A-Z]\.\d+)\s*[–—-]")


def _check_missing_tables(markdown: str, result: ValidationResult) -> None:
    """Verify that all referenced tables are actually defined."""
    # Collect all table definitions.
    defs = set(_TABLE_DEF_RE.findall(markdown))
    numeric_defs = {d for d in defs if d.isdigit()}

    # Collect references with their positions for page lookup.
    ref_positions: dict[str, list[int]] = {}
    for m in _TABLE_REF_RE.finditer(markdown):
        num = m.group(1)
        if num.isdigit():
            ref_positions.setdefault(num, []).append(m.start())

    missing = set(ref_positions) - numeric_defs
    if missing:
        pidx = _PageIndex(markdown)
        # Sort numerically for readability.
        for t in sorted(missing, key=int):
            pages = sorted({pidx.page_at(p) for p in ref_positions[t]} - {None})
            page_suffix = (
                f" (referenced on page {', '.join(str(p) for p in pages)})"
                if pages else ""
            )
            result.warnings.append((
                CAT_MISSING_REFERENCE,
                f"Table {t} is referenced in text but not defined"
                f" in output{page_suffix}",
            ))


def _check_missing_figures(markdown: str, result: ValidationResult) -> None:
    """Verify that all referenced figures are actually defined."""
    # Collect all figure definitions.
    defs = set(_FIGURE_DEF_RE.findall(markdown))
    numeric_defs = {d for d in defs if d.isdigit()}

    # Collect references with their positions for page lookup.
    ref_positions: dict[str, list[int]] = {}
    for m in _FIGURE_REF_RE.finditer(markdown):
        num = m.group(1)
        if num.isdigit():
            ref_positions.setdefault(num, []).append(m.start())

    missing = set(ref_positions) - numeric_defs
    if missing:
        pidx = _PageIndex(markdown)
        # Sort numerically for readability.
        for f in sorted(missing, key=int):
            pages = sorted({pidx.page_at(p) for p in ref_positions[f]} - {None})
            page_suffix = (
                f" (referenced on page {', '.join(str(p) for p in pages)})"
                if pages else ""
            )
            result.warnings.append((
                CAT_MISSING_REFERENCE,
                f"Figure {f} is referenced in text but not defined"
                f" in output{page_suffix}",
            ))


# ---------------------------------------------------------------------------
# Table content quality
# ---------------------------------------------------------------------------

# Regex to extract binary values from HTML table cells.
# Matches patterns like "0000b", "1010b", "01001111b" inside <td> content.
_BINARY_IN_TD_RE = re.compile(
    r"<td[^>]*>\s*([01]{4,8})b\s*</td>",
)

# Extract all <tr>...</tr> blocks from a table.
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)

# Extract <td> or <th> cells with their attributes.
_CELL_RE = re.compile(r"<(td|th)\b([^>]*)>", re.IGNORECASE)

# Extract colspan="N" or rowspan="N" from cell attributes.
_COLSPAN_RE = re.compile(r'colspan\s*=\s*["\']?(\d+)', re.IGNORECASE)
_ROWSPAN_RE = re.compile(r'rowspan\s*=\s*["\']?(\d+)', re.IGNORECASE)

# Table title: **Table 6 – Something** — captures full title text.
_TABLE_TITLE_RE = re.compile(
    r"\*\*Table\s+(?:\d+|[A-Z]\.\d+)\s*[–—-]\s*([^*]+)\*\*"
)

# Extract <thead>...</thead> and <tbody>...</tbody> sections.
_THEAD_RE = re.compile(r"<thead\b[^>]*>(.*?)</thead>", re.DOTALL | re.IGNORECASE)
_TBODY_RE = re.compile(r"<tbody\b[^>]*>(.*?)</tbody>", re.DOTALL | re.IGNORECASE)

# Maximum per-table column-consistency warnings before summarising.
_MAX_COLUMN_WARNINGS_PER_TABLE = 3


def _find_table_title(markdown: str, table_start: int) -> str | None:
    """Find the **Table N – Title** line preceding a <table> tag.

    Searches backwards up to 200 characters before the table start position.
    Returns the table number label (e.g. "Table 6"), not the full title text.
    """
    search_start = max(0, table_start - 200)
    preceding = markdown[search_start:table_start]
    # Find the last **Table N – ...** in the preceding text.
    match = None
    for m in _TABLE_DEF_RE.finditer(preceding):
        match = m
    if match is None:
        return None
    # Return the table number portion (e.g. "Table 6").
    return f"Table {match.group(1)}"


def _deterministic_mode(counts: list[int]) -> int:
    """Return the mode of *counts* with deterministic tie-breaking.

    When multiple values share the highest frequency, the **largest**
    value wins.  This is appropriate for column-count analysis because
    missing cells (under-count) are the most common table error, so
    the larger value is more likely the intended table width.
    """
    freq = Counter(counts)
    max_freq = freq.most_common(1)[0][1]
    candidates = [v for v, c in freq.items() if c == max_freq]
    return max(candidates)


def _compute_table_column_counts(table_html: str) -> list[int]:
    """Compute effective column count for each row in an HTML table.

    Uses a grid-based rowspan tracker: each column slot records how many
    more rows it is occupied by an earlier rowspan cell.

    Returns:
        List of effective column counts, one per <tr> row.
    """
    rows = _TR_RE.findall(table_html)
    if not rows:
        return []

    # rowspan_remaining[col] = number of additional rows this slot is
    # occupied by a prior rowspan cell (0 = free).
    rowspan_remaining: list[int] = []
    counts: list[int] = []

    for row_html in rows:
        cells = _CELL_RE.findall(row_html)

        col = 0  # current column pointer
        width = 0  # total occupied columns (explicit + inherited)

        for _tag, attrs in cells:
            # Skip past columns occupied by rowspans from previous rows.
            while col < len(rowspan_remaining) and rowspan_remaining[col] > 0:
                rowspan_remaining[col] -= 1
                col += 1
                width += 1

            colspan_m = _COLSPAN_RE.search(attrs)
            rowspan_m = _ROWSPAN_RE.search(attrs)
            colspan = int(colspan_m.group(1)) if colspan_m else 1
            rowspan = int(rowspan_m.group(1)) if rowspan_m else 1

            # Place this cell: it occupies 'colspan' columns starting at 'col'.
            for c in range(col, col + colspan):
                # Grow the tracker if we're beyond current size.
                while c >= len(rowspan_remaining):
                    rowspan_remaining.append(0)
                # Mark additional rows (rowspan - 1) as occupied.
                if rowspan > 1:
                    rowspan_remaining[c] = rowspan - 1
            col += colspan
            width += colspan

        # Account for ALL remaining rowspan-occupied columns beyond the
        # last explicit cell — iterate the full grid, not just consecutive
        # occupied slots (free gaps between occupied slots must not stop
        # the scan).
        for c in range(col, len(rowspan_remaining)):
            if rowspan_remaining[c] > 0:
                rowspan_remaining[c] -= 1
                width += 1

        counts.append(width)

    return counts


def check_table_column_consistency(
    markdown: str, result: ValidationResult
) -> None:
    """Check that every row in each HTML table has the same column count.

    Parses colspan/rowspan attributes to compute the effective column count
    per row.  Analysis is split by ``<thead>`` / ``<tbody>`` sections so
    that rowspan tracking resets at the boundary.

    The *expected* width is determined by the **mode** (most frequent count)
    across all rows.  When header and body widths disagree, a single
    header-vs-body diagnostic is emitted.  Per-row mismatch warnings are
    capped at :data:`_MAX_COLUMN_WARNINGS_PER_TABLE` per table.
    """
    pidx: _PageIndex | None = None
    for table_match in TABLE_BLOCK_RE.finditer(markdown):
        table_html = table_match.group(0)

        # --- compute per-section column counts -------------------------
        thead_m = _THEAD_RE.search(table_html)
        tbody_m = _TBODY_RE.search(table_html)

        # Compute counts independently per section (rowspan resets).
        thead_counts = (
            _compute_table_column_counts(thead_m.group(0))
            if thead_m else []
        )
        tbody_counts = (
            _compute_table_column_counts(tbody_m.group(0))
            if tbody_m else []
        )

        # Fall back to whole-table counting when sections are absent.
        if not thead_counts and not tbody_counts:
            all_counts = _compute_table_column_counts(table_html)
        else:
            all_counts = thead_counts + tbody_counts

        if len(all_counts) < 2:
            continue

        # --- determine expected width via mode -------------------------
        expected = _deterministic_mode(all_counts)

        # --- resolve table label and page ------------------------------
        title = _find_table_title(markdown, table_match.start())
        label = title if title else "HTML table"
        if pidx is None:
            pidx = _PageIndex(markdown)
        page_suffix = pidx.format_page(table_match.start())

        # --- header-vs-body width diagnostic ---------------------------
        # Compare the predominant widths of thead and tbody directly.
        # This avoids misleading noise when a single outlier header row
        # (e.g. an empty <tr></tr> with only inherited rowspan columns)
        # has fewer columns but the header majority matches the body.
        if thead_counts and tbody_counts:
            thead_mode = _deterministic_mode(thead_counts)
            tbody_mode = _deterministic_mode(tbody_counts)
            if thead_mode != tbody_mode:
                def _fmt_widths(ws: list[int]) -> str:
                    widths = sorted(set(ws))
                    return (
                        str(widths[0]) if len(widths) == 1
                        else f"{widths[0]}-{widths[-1]}"
                    )
                result.warnings.append((
                    CAT_TABLE_COLUMNS,
                    f"{label}{page_suffix}: header rows define "
                    f"{_fmt_widths(thead_counts)} columns but body rows "
                    f"have {_fmt_widths(tbody_counts)} columns "
                    f"(expected {expected})",
                ))

        # --- per-row mismatches (capped) -------------------------------
        mismatches = [
            (i, c) for i, c in enumerate(all_counts) if c != expected
        ]
        if not mismatches:
            continue

        reported = 0
        for row_idx, actual in mismatches:
            if reported >= _MAX_COLUMN_WARNINGS_PER_TABLE:
                remaining = len(mismatches) - reported
                result.warnings.append((
                    CAT_TABLE_COLUMNS,
                    f"{label}{page_suffix}: ... and {remaining} more row(s) "
                    f"with column count != {expected}",
                ))
                break
            result.warnings.append((
                CAT_TABLE_COLUMNS,
                f"{label}{page_suffix}: row {row_idx} has {actual} columns, "
                f"expected {expected}",
            ))
            reported += 1


def _check_binary_sequences(markdown: str, result: ValidationResult) -> None:
    """Check for duplicate or non-monotonic binary values in HTML tables.

    Scans each HTML table for ``<td>`` cells containing binary values
    (e.g., ``0101b``) and verifies that consecutive binary values within
    the same table are monotonically increasing. Duplicates or backward
    jumps indicate Claude misread the PDF.
    """
    pidx: _PageIndex | None = None
    for table_match in TABLE_BLOCK_RE.finditer(markdown):
        table_html = table_match.group(0)
        bin_values = _BINARY_IN_TD_RE.findall(table_html)

        if len(bin_values) < 2:
            continue

        # Resolve table context (title + page) once per table.
        title = _find_table_title(markdown, table_match.start())
        if pidx is None:
            pidx = _PageIndex(markdown)
        page_suffix = pidx.format_page(table_match.start())
        label = title if title else "HTML table"

        # Convert to integers for comparison.
        int_values = [int(v.replace(" ", ""), 2) for v in bin_values]

        for i in range(1, len(int_values)):
            if int_values[i] == int_values[i - 1]:
                result.warnings.append((
                    CAT_BINARY_SEQUENCE,
                    f"Duplicate binary value in {label}{page_suffix}: "
                    f"{bin_values[i]}b appears twice consecutively",
                ))
            elif int_values[i] < int_values[i - 1]:
                result.warnings.append((
                    CAT_BINARY_SEQUENCE,
                    f"Binary sequence not monotonic in {label}{page_suffix}: "
                    f"{bin_values[i]}b follows {bin_values[i - 1]}b",
                ))


# ---------------------------------------------------------------------------
# Content integrity (fabrication detection)
# ---------------------------------------------------------------------------

# Known fabrication patterns — Claude's telltale signs of inventing content
# instead of converting it from the PDF.
_FABRICATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "summary substitution",
        re.compile(
            r"(?:presented|shown|provided) as (?:summary|brief) "
            r"(?:references?|overviews?)",
            re.IGNORECASE,
        ),
    ),
    (
        "complexity excuse",
        re.compile(
            r"Due to the complexity.*?(?:these are|they are|see|refer to)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "subclauses redirect",
        re.compile(
            r"(?:full|complete|detailed) (?:command )?(?:details|specifications?) "
            r".*?(?:subclauses|sections?) (?:that follow|below)",
            re.IGNORECASE,
        ),
    ),
    (
        "omission note",
        re.compile(
            r"(?:content|table|data) (?:has been |is |was )?"
            r"(?:omitted|summarized|abbreviated|condensed)",
            re.IGNORECASE,
        ),
    ),
]


def _check_fabrication(markdown: str, result: ValidationResult) -> None:
    """Detect patterns that indicate Claude fabricated summary text."""
    pidx: _PageIndex | None = None
    for name, pattern in _FABRICATION_PATTERNS:
        match = pattern.search(markdown)
        if match:
            if pidx is None:
                pidx = _PageIndex(markdown)
            # Show the matched text (truncated) for context.
            snippet = match.group(0)[:100]
            page_suffix = pidx.format_page(match.start())
            result.errors.append((
                CAT_FABRICATION,
                f"Possible fabricated {name}{page_suffix}: \"{snippet}\"",
            ))


# ---------------------------------------------------------------------------
# Per-page fidelity check (PDF source text vs. markdown)
# ---------------------------------------------------------------------------

# Minimum significant words in the markdown for a page to be checked.
# Pages with fewer words (e.g., image-only, formula-only) are skipped.
_FIDELITY_MIN_WORDS = 20

# Minimum significant words in the PDF raw text for a page to be checked.
# Pages with fewer words (scanned image, blank page) are skipped.
_FIDELITY_MIN_PDF_WORDS = 5

# Overlap threshold: fraction of markdown's significant words that must
# appear in the PDF page's raw text.  Below this → suspect fabrication.
_FIDELITY_OVERLAP_THRESHOLD = 0.50

# Regex for extracting alphabetic words from text.
_ALPHA_WORD_RE = re.compile(r"[a-zA-Z]+")

# Page content block: captures (page_num, content_between_markers).
_PAGE_CONTENT_RE = re.compile(
    rf"<!--\s*{re.escape(PAGE_BEGIN.tag)}\s+(\d+)\s*-->"
    r"(.*?)"
    rf"<!--\s*{re.escape(PAGE_END.tag)}\s+\d+\s*-->",
    re.DOTALL,
)


def _significant_words(text: str, min_length: int = 5) -> set[str]:
    """Extract significant lowercase alphabetic words from text.

    Strips AI-generated image descriptions, HTML tags, HTML comments,
    markdown formatting, and LaTeX blocks before extracting words of at
    least ``min_length`` characters.
    """
    # Remove AI-generated image descriptions (not from PDF source).
    # Must run before the generic HTML comment strip because the
    # description block is delimited by HTML comment markers.
    text = IMAGE_AI_DESCRIPTION_BLOCK_RE.sub(" ", text)
    # Remove HTML comments (includes page markers).
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # Remove HTML tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Remove LaTeX blocks.
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.DOTALL)
    text = re.sub(r"\$[^$]+\$", " ", text)
    # Remove markdown formatting characters.
    text = re.sub(r"[*_#`\[\]()>|]", " ", text)

    return {
        w
        for w in _ALPHA_WORD_RE.findall(text.lower())
        if len(w) >= min_length
    }


def _extract_page_contents(markdown: str) -> dict[int, str]:
    """Parse markdown into per-page content blocks.

    Returns:
        Dict mapping page number to the content between that page's
        ``PAGE_BEGIN`` and ``PAGE_END`` markers.
    """
    return {
        int(m.group(1)): m.group(2)
        for m in _PAGE_CONTENT_RE.finditer(markdown)
    }


# ---------------------------------------------------------------------------
# Public helpers for table detection (used by table_fixer)
# ---------------------------------------------------------------------------


def table_page_numbers(markdown: str, start: int, end: int) -> list[int]:
    """Resolve page numbers for a table spanning positions [start, end).

    Args:
        markdown: Full markdown content with page markers.
        start: Start position of the table in the markdown string.
        end: End position of the table in the markdown string.

    Returns:
        List of PDF page numbers the table spans. Empty list if page
        markers are not present or positions are invalid.
    """
    pidx = _PageIndex(markdown)
    start_page = pidx.page_at(start)
    end_page = pidx.page_at(end - 1)

    if start_page is None:
        return []

    if end_page is not None and end_page != start_page:
        return list(range(start_page, end_page + 1))
    else:
        return [start_page]


def find_table_title(markdown: str, position: int) -> str | None:
    """Find the title for a table starting at the given position.

    Searches backward for patterns like ``**Table 6 – Commands**`` and
    returns the table number label (not the full title text).

    Args:
        markdown: Full markdown content.
        position: Character offset where the table starts.

    Returns:
        Table number label (e.g. ``"Table 6"``), or ``None``
        if no title pattern is found.
    """
    return _find_table_title(markdown, position)


def check_page_fidelity(
    pdf_path: Path | str,
    markdown: str,
    result: ValidationResult,
) -> None:
    """Cross-check markdown content per page against PDF raw text.

    For each page in the markdown (identified by ``PAGE_BEGIN`` /
    ``PAGE_END`` markers), extracts significant words from both the
    markdown content and the corresponding PDF page's raw text.
    Pages where the markdown has substantial content but very few words
    overlap with the PDF text are flagged as potential fabrication.

    Skipped pages (containing ``PAGE_SKIP``) and pages with very little
    text content (images, formulas) are excluded from the check.

    Args:
        pdf_path: Path to the source PDF file.
        markdown: The converted (or merged) markdown content.
        result: :class:`ValidationResult` to append warnings/errors to.
    """
    import pymupdf

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        _log.debug("PDF not found at %s — skipping fidelity check", pdf_path)
        return

    doc = pymupdf.open(str(pdf_path))
    try:
        total_pdf_pages = len(doc)

        page_contents = _extract_page_contents(markdown)
        if not page_contents:
            if PAGE_BEGIN.re.search(markdown):
                result.errors.append((
                    CAT_PAGE_FIDELITY,
                    "Fidelity check skipped: PAGE_BEGIN markers present "
                    "but no PAGE_END markers (needed for content extraction)",
                ))
            return

        suspect: list[tuple[int, float]] = []

        for page_num, md_content in sorted(page_contents.items()):
            # Skip pages with PAGE_SKIP marker.
            if PAGE_SKIP.re.search(md_content):
                continue

            # Extract significant words from markdown.
            md_words = _significant_words(md_content)

            # Skip pages with very little markdown content
            # (image-only, formula-only, short reference pages).
            if len(md_words) < _FIDELITY_MIN_WORDS:
                continue

            # Bounds check: page_num is 1-indexed.
            if page_num < 1 or page_num > total_pdf_pages:
                continue

            # Extract raw text from the PDF page.
            pdf_text = doc[page_num - 1].get_text() or ""
            pdf_words = _significant_words(pdf_text)

            # Skip if PDF text extraction yielded very few words
            # (scanned image page, blank page, etc.).
            if len(pdf_words) < _FIDELITY_MIN_PDF_WORDS:
                continue

            # Overlap: fraction of markdown words found in PDF text.
            common = md_words & pdf_words
            overlap = len(common) / len(md_words)
            _log.debug(
                "  Fidelity p%d: %d/%d md words in PDF (%.0f%%), "
                "pdf has %d words",
                page_num, len(common), len(md_words),
                overlap * 100, len(pdf_words),
            )
            if overlap < _FIDELITY_OVERLAP_THRESHOLD:
                suspect.append((page_num, overlap))

        if suspect:
            result.warnings.append((
                CAT_PAGE_FIDELITY,
                f"Page fidelity: {len(suspect)} page(s) have low text overlap "
                f"with PDF source",
            ))
            for page_num, ratio in suspect:
                result.warnings.append((
                    CAT_PAGE_FIDELITY,
                    f"  Page {page_num}: {ratio:.0%} of markdown words found "
                    f"in PDF text (threshold: "
                    f"{_FIDELITY_OVERLAP_THRESHOLD:.0%})",
                ))
    finally:
        doc.close()
