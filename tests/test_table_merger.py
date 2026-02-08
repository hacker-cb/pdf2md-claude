"""Unit tests for merge_continued_tables() in merger.py."""

from __future__ import annotations

import re

import pytest

from pdf2md_claude.markers import TABLE_CONTINUE_MARKER, TABLE_CONTINUE_RE
from pdf2md_claude.merger import merge_continued_tables


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_tables(md: str) -> int:
    """Count the number of <table> elements in markdown."""
    return len(re.findall(r"<table\b", md, re.IGNORECASE))


def _count_rows(md: str) -> int:
    """Count the number of <tr> elements in markdown."""
    return len(re.findall(r"<tr\b", md, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Fixtures: minimal table fragments
# ---------------------------------------------------------------------------

_PAGE1_TABLE = """\
<!-- PDF_PAGE_BEGIN 1 -->

**Table 5 – Example table**

<table>
<thead>
<tr><th>Name</th><th>Value</th></tr>
</thead>
<tbody>
<tr><td>Alpha</td><td>1</td></tr>
<tr><td>Beta</td><td>2</td></tr>
</tbody>
</table>

<!-- PDF_PAGE_END 1 -->"""

_PAGE2_CONTINUATION = """\
<!-- PDF_PAGE_BEGIN 2 -->

<!-- TABLE_CONTINUE -->

**Table 5 – Example table** *(continued)*

<table>
<thead>
<tr><th>Name</th><th>Value</th></tr>
</thead>
<tbody>
<tr><td>Gamma</td><td>3</td></tr>
<tr><td>Delta</td><td>4</td></tr>
</tbody>
</table>

<!-- PDF_PAGE_END 2 -->"""

_PAGE3_CONTINUATION = """\
<!-- PDF_PAGE_BEGIN 3 -->

<!-- TABLE_CONTINUE -->

**Table 5 – Example table** *(continued)*

<table>
<thead>
<tr><th>Name</th><th>Value</th></tr>
</thead>
<tbody>
<tr><td>Epsilon</td><td>5</td></tr>
</tbody>
</table>

<sup>a</sup> This is a footnote.

<!-- PDF_PAGE_END 3 -->"""


# ---------------------------------------------------------------------------
# test_single_continuation
# ---------------------------------------------------------------------------


class TestSingleContinuation:
    """Two-page table where page 2 has a TABLE_CONTINUE marker."""

    @pytest.fixture()
    def merged(self) -> str:
        md = _PAGE1_TABLE + "\n\n" + _PAGE2_CONTINUATION
        return merge_continued_tables(md)

    def test_single_table_remains(self, merged: str):
        assert _count_tables(merged) == 1

    def test_all_rows_present(self, merged: str):
        """Header row + 4 data rows = 5 total <tr>."""
        assert _count_rows(merged) == 5

    def test_original_data_preserved(self, merged: str):
        assert "Alpha" in merged
        assert "Beta" in merged
        assert "Gamma" in merged
        assert "Delta" in merged

    def test_page_markers_inside_table(self, merged: str):
        """Page markers should be preserved inside the merged table."""
        # The table should contain the page boundary markers
        table_match = re.search(
            r"<table\b.*?</table>", merged, re.DOTALL | re.IGNORECASE
        )
        assert table_match is not None
        table_html = table_match.group(0)
        assert "<!-- PDF_PAGE_END 1 -->" in table_html or \
               "PDF_PAGE_END 1" in merged
        assert "<!-- PDF_PAGE_BEGIN 2 -->" in table_html or \
               "PDF_PAGE_BEGIN 2" in merged

    def test_no_table_continue_markers(self, merged: str):
        assert TABLE_CONTINUE_RE.search(merged) is None

    def test_single_thead(self, merged: str):
        """Only one <thead> should remain (the original)."""
        assert merged.count("<thead>") == 1

    def test_continued_title_removed(self, merged: str):
        """The '(continued)' title line should be removed."""
        assert "(continued)" not in merged


# ---------------------------------------------------------------------------
# test_multiple_continuations
# ---------------------------------------------------------------------------


class TestMultipleContinuations:
    """Three-page table: page 1 original, pages 2-3 continuations."""

    @pytest.fixture()
    def merged(self) -> str:
        md = (
            _PAGE1_TABLE + "\n\n"
            + _PAGE2_CONTINUATION + "\n\n"
            + _PAGE3_CONTINUATION
        )
        return merge_continued_tables(md)

    def test_single_table_remains(self, merged: str):
        assert _count_tables(merged) == 1

    def test_all_rows_present(self, merged: str):
        """Header row + 5 data rows = 6 total <tr>."""
        assert _count_rows(merged) == 6

    def test_all_data_values(self, merged: str):
        for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
            assert name in merged

    def test_footnotes_preserved(self, merged: str):
        """Footnotes after the final continuation should survive."""
        assert "<sup>a</sup> This is a footnote." in merged

    def test_no_table_continue_markers(self, merged: str):
        assert TABLE_CONTINUE_RE.search(merged) is None

    def test_page_markers_preserved(self, merged: str):
        """All page boundary markers should be present in the output."""
        for n in (1, 2, 3):
            assert f"PDF_PAGE_BEGIN {n}" in merged
            assert f"PDF_PAGE_END {n}" in merged


# ---------------------------------------------------------------------------
# test_no_marker
# ---------------------------------------------------------------------------


class TestNoMarker:
    """Tables without TABLE_CONTINUE markers are left untouched."""

    def test_no_change_without_markers(self):
        md = """\
<!-- PDF_PAGE_BEGIN 1 -->

<table>
<thead><tr><th>A</th></tr></thead>
<tbody><tr><td>1</td></tr></tbody>
</table>

<!-- PDF_PAGE_END 1 -->

<!-- PDF_PAGE_BEGIN 2 -->

<table>
<thead><tr><th>B</th></tr></thead>
<tbody><tr><td>2</td></tr></tbody>
</table>

<!-- PDF_PAGE_END 2 -->"""
        result = merge_continued_tables(md)
        assert _count_tables(result) == 2
        assert result == md


# ---------------------------------------------------------------------------
# test_no_preceding_table
# ---------------------------------------------------------------------------


class TestNoPrecedingTable:
    """Orphan TABLE_CONTINUE marker with no preceding table."""

    def test_orphan_marker_warning(self):
        md = f"""\
<!-- PDF_PAGE_BEGIN 1 -->

{TABLE_CONTINUE_MARKER}

<table>
<thead><tr><th>A</th></tr></thead>
<tbody><tr><td>1</td></tr></tbody>
</table>

<!-- PDF_PAGE_END 1 -->"""
        # Should not crash; marker is left in place.
        result = merge_continued_tables(md)
        assert "<table" in result


# ---------------------------------------------------------------------------
# test_marker_removal
# ---------------------------------------------------------------------------


class TestMarkerRemoval:
    """Verify no TABLE_CONTINUE markers remain after merging."""

    def test_all_markers_consumed(self):
        md = _PAGE1_TABLE + "\n\n" + _PAGE2_CONTINUATION
        result = merge_continued_tables(md)
        assert TABLE_CONTINUE_MARKER not in result
        assert TABLE_CONTINUE_RE.search(result) is None


# ---------------------------------------------------------------------------
# test_mixed_tables
# ---------------------------------------------------------------------------


class TestMixedTables:
    """A continuation table followed by an independent table."""

    def test_independent_table_preserved(self):
        independent = """\
<!-- PDF_PAGE_BEGIN 4 -->

**Table 6 – Different table**

<table>
<thead><tr><th>X</th><th>Y</th></tr></thead>
<tbody><tr><td>10</td><td>20</td></tr></tbody>
</table>

<!-- PDF_PAGE_END 4 -->"""

        md = (
            _PAGE1_TABLE + "\n\n"
            + _PAGE2_CONTINUATION + "\n\n"
            + independent
        )
        result = merge_continued_tables(md)

        # Table 5 merged into one, Table 6 untouched = 2 tables.
        assert _count_tables(result) == 2
        # Independent table data intact.
        assert "Different table" in result
        assert "<td>10</td>" in result
        assert "<td>20</td>" in result


# ---------------------------------------------------------------------------
# test_marker_inside_open_table
# ---------------------------------------------------------------------------


class TestMarkerInsideOpenTable:
    """TABLE_CONTINUE inside an already-open <table> (intra-chunk continuation).

    When Claude keeps the table open across a page boundary within the
    same chunk, the TABLE_CONTINUE marker sits inside <tbody>.  The
    merger should just strip the marker without touching the table.
    """

    def test_table_preserved(self):
        """A table with TABLE_CONTINUE inside it stays intact."""
        md = """\
<!-- PDF_PAGE_BEGIN 59 -->

**Table 17 – Standard commands**

<table>
<thead>
<tr><th>Name</th><th>Opcode</th></tr>
</thead>
<tbody>
<tr><td>OFF</td><td>0x00</td></tr>
<tr><td>UP</td><td>0x01</td></tr>

<!-- PDF_PAGE_END 59 -->

<!-- PDF_PAGE_BEGIN 60 -->

<!-- TABLE_CONTINUE -->

<tr><td>DOWN</td><td>0x02</td></tr>
<tr><td>STEP UP</td><td>0x03</td></tr>
</tbody>
</table>

<!-- PDF_PAGE_END 60 -->"""
        result = merge_continued_tables(md)

        # Table stays as one table.
        assert _count_tables(result) == 1
        # All rows preserved.
        assert "OFF" in result
        assert "UP" in result
        assert "DOWN" in result
        assert "STEP UP" in result
        # Marker removed.
        assert TABLE_CONTINUE_RE.search(result) is None
        # Table title preserved.
        assert "Table 17" in result

    def test_preceding_table_not_corrupted(self):
        """A separate table before the open table is not affected."""
        md = """\
<!-- PDF_PAGE_BEGIN 57 -->

<table>
<thead><tr><th>Var</th><th>Value</th></tr></thead>
<tbody>
<tr><td>X</td><td>1</td></tr>
</tbody>
</table>

<!-- PDF_PAGE_END 57 -->

<!-- PDF_PAGE_BEGIN 59 -->

<table>
<thead><tr><th>Name</th><th>Opcode</th></tr></thead>
<tbody>
<tr><td>OFF</td><td>0x00</td></tr>

<!-- PDF_PAGE_END 59 -->

<!-- PDF_PAGE_BEGIN 60 -->

<!-- TABLE_CONTINUE -->

<tr><td>DOWN</td><td>0x02</td></tr>
</tbody>
</table>

<!-- PDF_PAGE_END 60 -->"""
        result = merge_continued_tables(md)

        # Both tables survive.
        assert _count_tables(result) == 2
        # Both tables' data preserved.
        assert "<td>X</td>" in result
        assert "OFF" in result
        assert "DOWN" in result
        assert TABLE_CONTINUE_RE.search(result) is None
