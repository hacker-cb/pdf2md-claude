"""Deterministic page-marker merge for chunked PDF conversion.

After chunked conversion, each chunk covers a disjoint page range.
This module concatenates chunks by page markers, deduplicating any
pages that appear in more than one chunk (first-writer-wins).

Additionally, ``merge_continued_tables`` reunifies tables that were
split across pages: it detects ``TABLE_CONTINUE`` markers emitted by
the converter and merges continuation tables into the preceding table,
preserving page-boundary markers inside the merged ``<tbody>``.
"""

from __future__ import annotations

import logging
import re

from pdf2md_claude.markers import (
    PAGE_BEGIN,
    PAGE_END,
    TABLE_BLOCK_RE,
    TABLE_CONTINUE_RE,
)

_log = logging.getLogger("merger")

# Regex that matches from a PAGE_BEGIN marker through its PAGE_END marker
# (inclusive), capturing the page number.
_PAGE_BLOCK_RE = re.compile(
    rf"(<!--\s*{re.escape(PAGE_BEGIN.tag)}\s+(\d+)\s*-->)"
    r"(.*?)"
    rf"(<!--\s*{re.escape(PAGE_END.tag)}\s+\d+\s*-->)",
    re.DOTALL,
)

# Regex helpers for table merging.
_TBODY_ROWS_RE = re.compile(
    r"<tbody[^>]*>(.*?)</tbody>",
    re.DOTALL | re.IGNORECASE,
)
_TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.DOTALL | re.IGNORECASE)
# "(continued)" title line that precedes a continuation table.
_CONTINUED_TITLE_RE = re.compile(
    r"\*\*Table\s+(?:\d+|[A-Z]\.\d+)\s*[–—-][^*]*\*\*\s*\*\(continued\)\*",
)


def _extract_pages(markdown: str) -> dict[int, str]:
    """Extract a mapping of page_number -> full page block (BEGIN to END).

    Each value includes the PAGE_BEGIN and PAGE_END markers.
    Content outside any page markers is attached to the nearest preceding
    page or dropped if before the first marker.
    """
    pages: dict[int, str] = {}
    for match in _PAGE_BLOCK_RE.finditer(markdown):
        page_num = int(match.group(2))
        if page_num not in pages:
            pages[page_num] = match.group(0)
    return pages


def merge_chunks(markdown_parts: list[str]) -> str:
    """Merge a list of markdown chunks into a single document.

    Concatenates chunks by page markers.  With disjoint chunks (no PDF
    overlap), this is a simple ordered join.  If any page appears in
    multiple chunks, the first occurrence wins.

    Args:
        markdown_parts: List of markdown strings from chunked conversion.

    Returns:
        Merged markdown string.
    """
    if len(markdown_parts) <= 1:
        return markdown_parts[0] if markdown_parts else ""

    _log.info("  Merging %d chunks by page markers...", len(markdown_parts))

    # Collect all pages across all chunks (first-writer-wins).
    all_pages: dict[int, str] = {}
    for i, part in enumerate(markdown_parts):
        chunk_pages = _extract_pages(part)
        new_pages = 0
        for page_num, content in chunk_pages.items():
            if page_num not in all_pages:
                all_pages[page_num] = content
                new_pages += 1
        _log.info(
            "    Chunk %d: %d pages (%d new)",
            i + 1, len(chunk_pages), new_pages,
        )

    if not all_pages:
        _log.warning("    No page markers found — falling back to simple join")
        return "\n\n".join(part.strip() for part in markdown_parts if part.strip())

    # Concatenate pages in order.
    sorted_pages = sorted(all_pages.keys())
    _log.info(
        "    Total: %d unique pages (%d-%d)",
        len(sorted_pages), sorted_pages[0], sorted_pages[-1],
    )

    merged = "\n\n".join(all_pages[p] for p in sorted_pages)
    return merged


# ---------------------------------------------------------------------------
# Table continuation merging
# ---------------------------------------------------------------------------


def merge_continued_tables(markdown: str) -> str:
    """Merge continuation tables into their preceding tables.

    Scans for ``<!-- TABLE_CONTINUE -->`` markers.  For each marker the
    function locates the *preceding* ``<table>`` (the one that ends just
    before the marker) and the *continuation* ``<table>`` (immediately
    after the marker).  The continuation table's ``<tbody>`` rows are
    appended into the preceding table's ``<tbody>``, with any
    ``PDF_PAGE_END`` / ``PDF_PAGE_BEGIN`` markers preserved between the
    rows so that page provenance is maintained.

    The continuation table's ``<thead>``, ``<table>`` wrapper, the
    ``TABLE_CONTINUE`` marker itself, and any "(continued)" title line
    are removed.

    Multiple consecutive continuations are processed sequentially (each
    appends to the growing first table).

    Args:
        markdown: Merged markdown (output of :func:`merge_chunks`).

    Returns:
        Markdown with continuation tables merged.
    """
    # Find all TABLE_CONTINUE markers.  Process from last to first so
    # that string indices remain valid after each splice.
    markers = list(TABLE_CONTINUE_RE.finditer(markdown))
    if not markers:
        return markdown

    _log.info("  Merging %d continued table(s)...", len(markers))

    for match in reversed(markers):
        marker_start = match.start()
        marker_end = match.end()

        # --- Check if marker is already inside an open <table> -----------
        # Count <table> and </table> tags up to the marker.  If there are
        # more opens than closes, the marker sits inside an already-open
        # table (intra-chunk continuation) — just strip the marker.
        prefix = markdown[:marker_start]
        opens = len(re.findall(r"<table\b", prefix, re.IGNORECASE))
        closes = len(re.findall(r"</table>", prefix, re.IGNORECASE))
        if opens > closes:
            _log.info(
                "    TABLE_CONTINUE inside open table — removing marker only",
            )
            markdown = markdown[:marker_start] + markdown[marker_end:]
            continue

        # --- Locate the preceding table's </tbody></table> ---------------
        preceding_table_end = markdown.rfind("</table>", 0, marker_start)
        if preceding_table_end == -1:
            _log.warning(
                "    TABLE_CONTINUE at offset %d: no preceding </table>, skipping",
                marker_start,
            )
            continue
        # Find the </tbody> inside the preceding table (search backward
        # from the </table> tag).
        preceding_tbody_end = markdown.rfind("</tbody>", 0, preceding_table_end)
        if preceding_tbody_end == -1:
            _log.warning(
                "    TABLE_CONTINUE at offset %d: preceding table has no </tbody>, "
                "skipping",
                marker_start,
            )
            continue

        # --- Locate the continuation table after the marker ---------------
        cont_table_match = TABLE_BLOCK_RE.search(markdown, marker_end)
        if cont_table_match is None:
            _log.warning(
                "    TABLE_CONTINUE at offset %d: no continuation <table> found, "
                "skipping",
                marker_start,
            )
            continue

        cont_table_start = cont_table_match.start()
        cont_table_end_pos = cont_table_match.end()
        cont_table_html = cont_table_match.group(0)

        # --- Extract <tbody> rows from the continuation table -------------
        tbody_match = _TBODY_ROWS_RE.search(cont_table_html)
        if tbody_match is None:
            _log.warning(
                "    TABLE_CONTINUE at offset %d: continuation table has no <tbody>, "
                "skipping",
                marker_start,
            )
            continue

        cont_rows = tbody_match.group(1).strip()
        row_count = len(_TR_RE.findall(cont_rows))

        # --- Collect page markers between the two tables ------------------
        # The region between the preceding </table> and the continuation
        # <table> may contain PDF_PAGE_END / PDF_PAGE_BEGIN markers as well
        # as the TABLE_CONTINUE marker and an optional "(continued)" title.
        between = markdown[preceding_table_end + len("</table>"):cont_table_start]
        page_markers = _extract_page_markers(between)

        # Build the text to insert into the preceding table's <tbody>.
        insert_parts: list[str] = []
        if page_markers:
            insert_parts.append(page_markers)
        insert_parts.append(cont_rows)
        insert_text = "\n\n".join(insert_parts)

        # --- Splice -------------------------------------------------------
        # 1. Insert rows (+ page markers) into the preceding table's <tbody>
        #    right before the closing </tbody>.
        new_markdown = (
            markdown[:preceding_tbody_end]
            + "\n"
            + insert_text
            + "\n"
            + markdown[preceding_tbody_end:preceding_table_end + len("</table>")]
        )

        # 2. Remove everything from after the preceding </table> through
        #    the end of the continuation </table>, EXCEPT content that
        #    comes after the continuation table on the same page.
        after_preceding_table = preceding_table_end + len("</table>")
        # Offset shift: new_markdown is longer than the original prefix.
        shift = len(new_markdown) - (after_preceding_table)
        # The tail starts right after the continuation table in the
        # original string.
        tail = markdown[cont_table_end_pos:]
        new_markdown = new_markdown + tail

        markdown = new_markdown

        # Build a compact description of which page boundary was stitched.
        end_pages = PAGE_END.re.findall(page_markers)
        begin_pages = PAGE_BEGIN.re.findall(page_markers)
        if end_pages and begin_pages:
            boundary = f"p{end_pages[-1]} → p{begin_pages[0]}"
            _log.info(
                "    Merged continuation table (%d rows, boundary %s)",
                row_count, boundary,
            )
        else:
            _log.info(
                "    Merged continuation table (%d rows)", row_count,
            )

    # Final sanity: no TABLE_CONTINUE markers should remain.
    remaining = len(TABLE_CONTINUE_RE.findall(markdown))
    if remaining:
        _log.warning(
            "    %d TABLE_CONTINUE marker(s) still present after merging",
            remaining,
        )

    return markdown


def _extract_page_markers(text: str) -> str:
    """Extract PDF_PAGE_BEGIN/END markers from a text region.

    Returns all page markers found in *text* as a newline-joined string,
    preserving their order.  Non-marker content (TABLE_CONTINUE marker,
    "(continued)" titles, whitespace) is discarded.
    """
    markers: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if PAGE_BEGIN.re.match(stripped) or PAGE_END.re.match(stripped):
            markers.append(stripped)
    return "\n\n".join(markers)
