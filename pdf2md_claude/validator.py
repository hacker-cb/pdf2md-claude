"""Content validation for converted markdown output.

Checks for common problems in Claude's PDF-to-Markdown conversion:
- Missing or non-monotonic page markers (BEGIN/END matching and gaps)
- Missing tables (referenced but not defined)
- Missing figures (referenced but not defined)
- Heading sequence gaps (missing top-level sections)
- Duplicate numbered headings (same section number appears more than once)
- Non-monotonic or duplicate binary values in HTML tables
- Fabricated summaries (Claude inventing text to replace omitted content)
- Per-page fidelity check against PDF source text (optional, needs PDF path)
"""

from __future__ import annotations

import logging
import re
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


@dataclass
class ValidationResult:
    """Result of validating a converted markdown document."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no errors (warnings are tolerable)."""
        return len(self.errors) == 0

    def log_all(self) -> None:
        """Log all errors, warnings, and informational messages."""
        for e in self.errors:
            _log.error("  ✗ %s", e)
        for w in self.warnings:
            _log.warning("  ⚠ %s", w)
        for i in self.info:
            _log.info("  ℹ %s", i)


# Table reference: "Table 17" or "Table B.1" etc.
_TABLE_REF_RE = re.compile(r"\bTable\s+(\d+|[A-Z]\.\d+)\b")

# Table definition: **Table 17 – Something** (bold heading above the table)
_TABLE_DEF_RE = re.compile(r"\*\*Table\s+(\d+|[A-Z]\.\d+)\s*[–—-]")

# Figure reference: "Figure 5" or "Figure A.1" etc.
_FIGURE_REF_RE = re.compile(r"\bFigure\s+(\d+|[A-Z]\.\d+)\b")

# Figure definition: **Figure 5 – Something** (bold caption in IMAGE block)
_FIGURE_DEF_RE = re.compile(r"\*\*Figure\s+(\d+|[A-Z]\.\d+)\s*[–—-]")

# Page markers: <!-- PDF_PAGE_BEGIN 42 --> / <!-- PDF_PAGE_END 42 -->
_PAGE_MARKER_RE = PAGE_BEGIN.re_value
_PAGE_END_MARKER_RE = PAGE_END.re_value

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


def validate_output(markdown: str) -> ValidationResult:
    """Check converted markdown for common problems.

    Args:
        markdown: The converted markdown content.

    Returns:
        ValidationResult with errors and warnings.
    """
    result = ValidationResult()

    _check_missing_tables(markdown, result)
    _check_missing_figures(markdown, result)
    _check_fabrication(markdown, result)
    _check_page_markers(markdown, result)
    _check_page_end_markers(markdown, result)
    _check_image_block_pairing(markdown, result)
    _check_heading_sequence(markdown, result)
    _check_duplicate_headings(markdown, result)
    _check_binary_sequences(markdown, result)

    return result


def _check_missing_tables(markdown: str, result: ValidationResult) -> None:
    """Verify that all referenced tables are actually defined."""
    # Collect all table references and definitions.
    refs = set(_TABLE_REF_RE.findall(markdown))
    defs = set(_TABLE_DEF_RE.findall(markdown))

    # Only check numeric tables (not annex tables like B.1).
    numeric_refs = {r for r in refs if r.isdigit()}
    numeric_defs = {d for d in defs if d.isdigit()}

    missing = numeric_refs - numeric_defs
    if missing:
        # Sort numerically for readability.
        for t in sorted(missing, key=int):
            result.warnings.append(
                f"Table {t} is referenced in text but not defined in output"
            )


def _check_missing_figures(markdown: str, result: ValidationResult) -> None:
    """Verify that all referenced figures are actually defined."""
    # Collect all figure references and definitions.
    refs = set(_FIGURE_REF_RE.findall(markdown))
    defs = set(_FIGURE_DEF_RE.findall(markdown))

    # Only check numeric figures (not annex figures like A.1).
    numeric_refs = {r for r in refs if r.isdigit()}
    numeric_defs = {d for d in defs if d.isdigit()}

    missing = numeric_refs - numeric_defs
    if missing:
        # Sort numerically for readability.
        for f in sorted(missing, key=int):
            result.warnings.append(
                f"Figure {f} is referenced in text but not defined in output"
            )


def _check_fabrication(markdown: str, result: ValidationResult) -> None:
    """Detect patterns that indicate Claude fabricated summary text."""
    for name, pattern in _FABRICATION_PATTERNS:
        match = pattern.search(markdown)
        if match:
            # Show the matched text (truncated) for context.
            snippet = match.group(0)[:100]
            result.errors.append(
                f"Possible fabricated {name}: \"{snippet}\""
            )


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
                result.errors.append(
                    "Fidelity check skipped: PAGE_BEGIN markers present "
                    "but no PAGE_END markers (needed for content extraction)"
                )
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
            result.warnings.append(
                f"Page fidelity: {len(suspect)} page(s) have low text overlap "
                f"with PDF source"
            )
            for page_num, ratio in suspect:
                result.warnings.append(
                    f"  Page {page_num}: {ratio:.0%} of markdown words found "
                    f"in PDF text (threshold: "
                    f"{_FIDELITY_OVERLAP_THRESHOLD:.0%})"
                )
    finally:
        doc.close()


def _count_skipped_pages(markdown: str) -> int:
    """Count pages containing a PDF_PAGE_SKIP marker."""
    return len(PAGE_SKIP.re.findall(markdown))


def _check_page_markers(markdown: str, result: ValidationResult) -> None:
    """Verify that page markers are present, sequential, and without large gaps."""
    markers = _PAGE_MARKER_RE.findall(markdown)

    if not markers:
        result.errors.append("No page markers found in output")
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
            result.errors.append(
                f"Page markers not monotonic: page {pages[i]} "
                f"follows page {pages[i - 1]}"
            )

    # Check for gaps (every page should have a marker).
    for i in range(1, len(pages)):
        gap = pages[i] - pages[i - 1]
        if gap > 1:
            result.errors.append(
                f"Missing page marker(s): page {pages[i - 1]} jumps to "
                f"page {pages[i]} (missing {gap - 1} page(s))"
            )


def _check_page_end_markers(markdown: str, result: ValidationResult) -> None:
    """Verify that PDF_PAGE_END markers match PDF_PAGE_BEGIN markers."""
    begin_pages = [int(m) for m in _PAGE_MARKER_RE.findall(markdown)]
    end_pages = [int(m) for m in _PAGE_END_MARKER_RE.findall(markdown)]

    if not end_pages:
        if begin_pages:
            result.errors.append(
                "No PDF_PAGE_END markers found (PDF_PAGE_BEGIN markers present)"
            )
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
            result.errors.append(
                f"PDF_PAGE_END {p} has no matching PDF_PAGE_BEGIN"
            )

    # Every BEGIN page should have a matching END page.
    missing_ends = begin_set - end_set
    if missing_ends:
        for p in sorted(missing_ends):
            result.errors.append(
                f"PDF_PAGE_BEGIN {p} has no matching PDF_PAGE_END"
            )


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
                result.errors.append(
                    f"Nested IMAGE_BEGIN on page {current_page} — "
                    f"previous block opened{loc} was not closed"
                )
            in_block = True
            open_page = current_page

        if IMAGE_END.re.search(line):
            end_count += 1
            if not in_block:
                result.errors.append(
                    f"IMAGE_END without matching IMAGE_BEGIN "
                    f"on page {current_page}"
                )
            in_block = False
            open_page = None

    # Trailing unclosed block.
    if in_block:
        loc = f" on page {open_page}" if open_page else ""
        result.errors.append(
            f"IMAGE_BEGIN{loc} was never closed with IMAGE_END"
        )

    if begin_count or end_count:
        result.info.append(
            f"Image blocks: {begin_count} IMAGE_BEGIN, {end_count} IMAGE_END"
        )


# Section heading pattern: matches numbered (9.2.1) and lettered (A.1, B.2.1)
# section identifiers at the start of Markdown headings.
_SECTION_HEADING_RE = re.compile(
    r"^#{1,6}\s+((?:[A-Z]|\d+)(?:\.(?:[A-Z]|\d+))*)\s+", re.MULTILINE
)


def _check_heading_sequence(markdown: str, result: ValidationResult) -> None:
    """Warn if numbered section headings have gaps (missing sections)."""
    headings = _SECTION_HEADING_RE.findall(markdown)

    if len(headings) < 2:
        return

    # Check top-level sections (e.g., 1, 2, 3, ...) for gaps.
    top_level = []
    for h in headings:
        parts = h.split(".")
        if len(parts) == 1 and parts[0].isdigit():
            top_level.append(int(parts[0]))

    for i in range(1, len(top_level)):
        gap = top_level[i] - top_level[i - 1]
        if gap > 1:
            result.warnings.append(
                f"Section gap: section {top_level[i - 1]} jumps to "
                f"section {top_level[i]} (missing {gap - 1} sections)"
            )


def _section_sort_key(section: str) -> tuple:
    """Sort key for section numbers: numeric parts by value, letters after."""
    parts = section.split(".")
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p) for p in parts
    )


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
    result.warnings.append(
        f"Duplicate section headings: {len(sorted_sections)} sections "
        f"appear more than once"
    )
    for section in sorted_sections:
        pages = duplicates[section]
        page_str = ", ".join(f"p{p}" for p in pages)
        result.warnings.append(
            f"  Section {section} appears {len(pages)} times "
            f"(pages: {page_str})"
        )


# Regex to extract binary values from HTML table cells.
# Matches patterns like "0000b", "1010b", "01001111b" inside <td> content.
_BINARY_IN_TD_RE = re.compile(
    r"<td[^>]*>\s*([01]{4,8})b\s*</td>",
)


def _check_binary_sequences(markdown: str, result: ValidationResult) -> None:
    """Check for duplicate or non-monotonic binary values in HTML tables.

    Scans each HTML table for ``<td>`` cells containing binary values
    (e.g., ``0101b``) and verifies that consecutive binary values within
    the same table are monotonically increasing. Duplicates or backward
    jumps indicate Claude misread the PDF.
    """
    for table_match in TABLE_BLOCK_RE.finditer(markdown):
        table_html = table_match.group(0)
        bin_values = _BINARY_IN_TD_RE.findall(table_html)

        if len(bin_values) < 2:
            continue

        # Convert to integers for comparison.
        int_values = [int(v.replace(" ", ""), 2) for v in bin_values]

        for i in range(1, len(int_values)):
            if int_values[i] == int_values[i - 1]:
                result.warnings.append(
                    f"Duplicate binary value in table: {bin_values[i]}b "
                    f"appears twice consecutively"
                )
            elif int_values[i] < int_values[i - 1]:
                result.warnings.append(
                    f"Binary sequence not monotonic in table: "
                    f"{bin_values[i]}b follows {bin_values[i - 1]}b"
                )
