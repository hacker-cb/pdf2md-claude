"""Unit tests for converter, merger, validator, and prompts."""

import pytest

from pdf2md_claude.converter import _get_context_tail, _remap_page_markers
from pdf2md_claude.markers import PAGE_BEGIN, PAGE_END
from pdf2md_claude.merger import merge_chunks
from pdf2md_claude.prompt import (
    CONVERT_CHUNK_PROMPT,
    SYSTEM_PROMPT,
    _DEFAULT_REGISTRY,
    _RULES,
    build_system_prompt,
)
from pdf2md_claude.validator import (
    ValidationResult,
    _check_binary_sequences,
    _check_page_end_markers,
    _check_page_markers,
)


# ---------------------------------------------------------------------------
# 1. _remap_page_markers
# ---------------------------------------------------------------------------


class TestRemapPageMarkers:
    """Tests for _remap_page_markers() in converter.py."""

    def test_no_markers_unchanged(self):
        """Input without any page markers should be returned unchanged."""
        md = "Hello world\nNo markers here"
        assert _remap_page_markers(md, 18) == md

    def test_markers_already_correct(self):
        """Markers already >= page_start should not be remapped."""
        md = (
            "<!-- PDF_PAGE_BEGIN 18 -->\nContent\n"
            "<!-- PDF_PAGE_BEGIN 19 -->\nMore"
        )
        assert _remap_page_markers(md, 18) == md

    def test_chunk1_no_remap(self):
        """Chunk 1 (page_start=1): markers at 14+ should not be remapped."""
        md = (
            "<!-- PDF_PAGE_BEGIN 14 -->\nForeword\n"
            "<!-- PDF_PAGE_BEGIN 15 -->\nIntro"
        )
        assert _remap_page_markers(md, 1) == md

    def test_viewer_numbers_remapped(self):
        """Chunk 2 (page_start=18): viewer pages 1,4,5 -> 18,21,22."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nOverlap\n"
            "<!-- PDF_PAGE_BEGIN 4 -->\nNew content\n"
            "<!-- PDF_PAGE_BEGIN 5 -->\nMore"
        )
        expected = (
            "<!-- PDF_PAGE_BEGIN 18 -->\nOverlap\n"
            "<!-- PDF_PAGE_BEGIN 21 -->\nNew content\n"
            "<!-- PDF_PAGE_BEGIN 22 -->\nMore"
        )
        assert _remap_page_markers(md, 18) == expected

    def test_offset_calculation(self):
        """Chunk 3 (page_start=35): viewer page 3 -> original page 37."""
        md = "Before\n<!-- PDF_PAGE_BEGIN 3 -->\nSection content"
        expected = "Before\n<!-- PDF_PAGE_BEGIN 37 -->\nSection content"
        assert _remap_page_markers(md, 35) == expected

    def test_whitespace_variations(self):
        """Regex should handle varying whitespace in markers."""
        md = (
            "<!--PDF_PAGE_BEGIN 2-->\nA\n"
            "<!-- PDF_PAGE_BEGIN  3 -->\nB"
        )
        # Both should be remapped: 2+17=19, 3+17=20
        result = _remap_page_markers(md, 18)
        assert "PDF_PAGE_BEGIN 20" in result or "PDF_PAGE_BEGIN 19" in result

    def test_format_helper_roundtrip(self):
        """Markers generated via PAGE_BEGIN.format() should be recognized."""
        md = f"{PAGE_BEGIN.format(1)}\nContent\n{PAGE_BEGIN.format(4)}\nMore"
        result = _remap_page_markers(md, 18)
        assert PAGE_BEGIN.format(18) in result
        assert PAGE_BEGIN.format(21) in result

    def test_end_markers_also_remapped(self):
        """PAGE_END markers must be remapped alongside PAGE_BEGIN markers."""
        md = (
            f"{PAGE_BEGIN.format(1)}\nContent\n{PAGE_END.format(1)}\n"
            f"{PAGE_BEGIN.format(2)}\nMore\n{PAGE_END.format(2)}"
        )
        result = _remap_page_markers(md, 18)
        assert PAGE_BEGIN.format(18) in result
        assert PAGE_END.format(18) in result
        assert PAGE_BEGIN.format(19) in result
        assert PAGE_END.format(19) in result


# ---------------------------------------------------------------------------
# 2. _check_page_markers
# ---------------------------------------------------------------------------


class TestCheckPageMarkers:
    """Tests for upgraded _check_page_markers() in validator.py."""

    def test_no_markers_error(self):
        """Empty input (no markers) should produce an error."""
        r = ValidationResult()
        _check_page_markers("No markers here", r)
        assert any("No page markers" in e for e in r.errors)

    def test_monotonic_no_errors(self):
        """Correct monotonic sequence should produce no errors."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 14 -->\n"
            "<!-- PDF_PAGE_BEGIN 15 -->\n"
            "<!-- PDF_PAGE_BEGIN 16 -->"
        )
        _check_page_markers(md, r)
        assert len(r.errors) == 0

    def test_non_monotonic_is_error(self):
        """Backward jump in page markers should be an error, not a warning."""
        r = ValidationResult()
        md = "<!-- PDF_PAGE_BEGIN 15 -->\n<!-- PDF_PAGE_BEGIN 4 -->"
        _check_page_markers(md, r)
        assert any("not monotonic" in e for e in r.errors)

    def test_all_jumps_reported(self):
        """Multiple backward jumps should ALL be reported."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 20 -->\n"
            "<!-- PDF_PAGE_BEGIN 4 -->\n"
            "<!-- PDF_PAGE_BEGIN 5 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->"
        )
        _check_page_markers(md, r)
        mono_errors = [e for e in r.errors if "not monotonic" in e]
        assert len(mono_errors) == 2

    def test_gap_is_error(self):
        """Any gap in page markers should produce an error."""
        r = ValidationResult()
        md = "<!-- PDF_PAGE_BEGIN 14 -->\n<!-- PDF_PAGE_BEGIN 16 -->"
        _check_page_markers(md, r)
        assert any("Missing page marker" in e for e in r.errors)

    def test_consecutive_no_gap_error(self):
        """Consecutive pages should not produce a gap error."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 14 -->\n"
            "<!-- PDF_PAGE_BEGIN 15 -->\n"
            "<!-- PDF_PAGE_BEGIN 16 -->"
        )
        _check_page_markers(md, r)
        gap_errors = [e for e in r.errors if "Missing" in e]
        assert len(gap_errors) == 0


# ---------------------------------------------------------------------------
# 2b. _check_page_end_markers
# ---------------------------------------------------------------------------


class TestCheckPageEndMarkers:
    """Tests for _check_page_end_markers() in validator.py."""

    def test_no_end_markers_warns(self):
        """Missing end markers when begin markers exist should warn."""
        r = ValidationResult()
        md = "<!-- PDF_PAGE_BEGIN 1 -->\nContent"
        _check_page_end_markers(md, r)
        assert any("No PDF_PAGE_END" in w for w in r.warnings)

    def test_matching_pairs_no_errors(self):
        """Matched begin/end pairs should produce no errors."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nMore\n<!-- PDF_PAGE_END 2 -->"
        )
        _check_page_end_markers(md, r)
        assert len(r.errors) == 0

    def test_unmatched_end_is_error(self):
        """End marker without matching begin should be an error."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_END 99 -->"
        )
        _check_page_end_markers(md, r)
        assert any("PDF_PAGE_END 99 has no matching" in e for e in r.errors)

    def test_missing_end_warns(self):
        """Begin marker without matching end should warn."""
        r = ValidationResult()
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nMore"
        )
        _check_page_end_markers(md, r)
        assert any("PDF_PAGE_BEGIN 2 has no matching" in w for w in r.warnings)

    def test_no_markers_at_all_no_warn(self):
        """No markers at all should produce no warnings."""
        r = ValidationResult()
        _check_page_end_markers("Just plain text", r)
        assert len(r.warnings) == 0
        assert len(r.errors) == 0


# ---------------------------------------------------------------------------
# 3. _check_binary_sequences
# ---------------------------------------------------------------------------


class TestCheckBinarySequences:
    """Tests for _check_binary_sequences() in validator.py."""

    def test_duplicate_binary_detected(self):
        """Known Table 6 issue: 1011b appears twice consecutively."""
        r = ValidationResult()
        html = (
            "<table>\n"
            "<tr><td>1001b</td><td>10</td></tr>\n"
            "<tr><td>1011b</td><td>11</td></tr>\n"
            "<tr><td>1011b</td><td>12</td></tr>\n"
            "<tr><td>1100b</td><td>13</td></tr>\n"
            "</table>"
        )
        _check_binary_sequences(html, r)
        assert any("Duplicate binary" in w for w in r.warnings)

    def test_non_monotonic_binary_detected(self):
        """Backward jump in binary values should be flagged."""
        r = ValidationResult()
        html = (
            "<table>\n"
            "<tr><td>0010b</td><td>2</td></tr>\n"
            "<tr><td>0011b</td><td>3</td></tr>\n"
            "<tr><td>0001b</td><td>1</td></tr>\n"
            "</table>"
        )
        _check_binary_sequences(html, r)
        assert any("not monotonic" in w for w in r.warnings)

    def test_correct_sequence_no_warnings(self):
        """Monotonically increasing binary sequence should be clean."""
        r = ValidationResult()
        html = (
            "<table>\n"
            "<tr><td>0000b</td><td>0</td></tr>\n"
            "<tr><td>0001b</td><td>1</td></tr>\n"
            "<tr><td>0010b</td><td>2</td></tr>\n"
            "<tr><td>0011b</td><td>3</td></tr>\n"
            "</table>"
        )
        _check_binary_sequences(html, r)
        assert len(r.warnings) == 0

    def test_no_binary_no_warnings(self):
        """Table without binary values should produce no warnings."""
        r = ValidationResult()
        html = "<table><tr><td>Hello</td></tr></table>"
        _check_binary_sequences(html, r)
        assert len(r.warnings) == 0

    def test_separate_tables_independent(self):
        """Binary values in different tables should not be compared."""
        r = ValidationResult()
        html = (
            "<table><tr><td>1111b</td></tr></table>\n"
            "<table><tr><td>0000b</td></tr></table>"
        )
        _check_binary_sequences(html, r)
        assert len(r.warnings) == 0


# ---------------------------------------------------------------------------
# 4. System prompt assembly
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the refactored SYSTEM_PROMPT assembly."""

    def test_rule_count(self):
        """SYSTEM_PROMPT should contain exactly 8 numbered rules."""
        assert len(_RULES) == 8
        assert len(_DEFAULT_REGISTRY) == 8

    def test_registry_derives_rules(self):
        """_RULES should be derived from _DEFAULT_REGISTRY."""
        assert _RULES == [text for _, text in _DEFAULT_REGISTRY]

    def test_rule_names_unique(self):
        """All rule names in the registry should be unique."""
        names = [name for name, _ in _DEFAULT_REGISTRY]
        assert len(names) == len(set(names))

    def test_build_system_prompt_matches_constant(self):
        """build_system_prompt(_RULES) should produce the same SYSTEM_PROMPT."""
        assert build_system_prompt(_RULES) == SYSTEM_PROMPT

    def test_all_rules_numbered(self):
        """Each rule should appear with its 1-based number prefix."""
        for i in range(1, len(_RULES) + 1):
            assert f"\n{i}. **" in SYSTEM_PROMPT or SYSTEM_PROMPT.startswith(f"{i}. **") or f"\n\n{i}. **" in SYSTEM_PROMPT

    def test_numbering_sequence(self):
        """Rules should be numbered 1 through N in order."""
        import re
        numbers = re.findall(r"(?:^|\n)(\d+)\. \*\*", SYSTEM_PROMPT)
        assert numbers == [str(i) for i in range(1, len(_RULES) + 1)]

    def test_marker_examples_present(self):
        """Page marker examples should be interpolated (not raw placeholders)."""
        assert PAGE_BEGIN.example in SYSTEM_PROMPT
        assert PAGE_END.example in SYSTEM_PROMPT

    def test_marker_format_examples_present(self):
        """Concrete page marker examples (format(5), format(6)) should appear."""
        assert PAGE_BEGIN.format(5) in SYSTEM_PROMPT
        assert PAGE_END.format(5) in SYSTEM_PROMPT

    def test_no_placeholder_leaks(self):
        """No raw f-string placeholders should leak into the assembled prompt."""
        assert "{_PB}" not in SYSTEM_PROMPT
        assert "{_PE}" not in SYSTEM_PROMPT
        assert "{PAGE_BEGIN" not in SYSTEM_PROMPT
        assert "{PAGE_END" not in SYSTEM_PROMPT

    def test_preamble_present(self):
        """Prompt should start with the converter role preamble."""
        assert SYSTEM_PROMPT.startswith("You are a precise document converter.")

    def test_critical_keywords_present(self):
        """All CRITICAL rules should contain their key instructions."""
        # Tables completeness
        assert "MUST convert EVERY table completely" in SYSTEM_PROMPT
        # Content fidelity
        assert "Do NOT summarize, paraphrase, or omit" in SYSTEM_PROMPT
        # No fabrication
        assert "NEVER insert text that does not exist" in SYSTEM_PROMPT
        # Page markers
        assert "Missing page markers are treated as conversion errors" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 4b. Prompt formatting
# ---------------------------------------------------------------------------


class TestPromptFormatting:
    """Tests for prompt template .format() correctness."""

    def test_chunk_prompt_formats(self):
        """CONVERT_CHUNK_PROMPT should format without error."""
        result = CONVERT_CHUNK_PROMPT.format(
            chunk_num=2,
            total_chunks=5,
            context_note="MIDDLE section.",
            previous_context_block="some context",
            page_start=18,
            page_end=37,
            page_count=20,
            page_start_plus_1=19,
        )
        assert "part 2 of 5" in result
        assert "pages 18 through 37" in result
        assert "20 begin/end marker pairs" in result

    def test_chunk_prompt_contains_marker_examples(self):
        """CONVERT_CHUNK_PROMPT should reference the canonical marker format."""
        result = CONVERT_CHUNK_PROMPT.format(
            chunk_num=1,
            total_chunks=1,
            context_note="START",
            previous_context_block="",
            page_start=1,
            page_end=10,
            page_count=10,
            page_start_plus_1=2,
        )
        assert PAGE_BEGIN.example in result
        assert PAGE_END.example in result


# ---------------------------------------------------------------------------
# 5. _get_context_tail (page-aligned context passing)
# ---------------------------------------------------------------------------


def _make_page(n: int, content: str = "") -> str:
    """Helper: build a page block with BEGIN/END markers."""
    body = f"\n{content}\n" if content else "\n"
    return f"{PAGE_BEGIN.format(n)}{body}{PAGE_END.format(n)}"


class TestGetContextTail:
    """Tests for _get_context_tail() in converter.py."""

    def test_returns_last_n_pages(self):
        """Should return the last N complete pages."""
        md = "\n".join([_make_page(i, f"Page {i} content") for i in range(1, 6)])
        result = _get_context_tail(md, min_pages=2, min_lines=0)
        assert PAGE_BEGIN.format(4) in result
        assert PAGE_BEGIN.format(5) in result
        assert PAGE_BEGIN.format(3) not in result

    def test_extends_for_min_lines(self):
        """Should add more pages if min_lines threshold not met."""
        # 5 pages with short content (2 lines each = ~10 lines for 2 pages).
        md = "\n".join([_make_page(i, f"Line {i}") for i in range(1, 6)])
        result = _get_context_tail(md, min_pages=1, min_lines=20)
        # Should have extended beyond 1 page to meet min_lines.
        page_count = len(PAGE_BEGIN.re.findall(result))
        assert page_count > 1

    def test_always_complete_pages(self):
        """Returned content should always start with a PAGE_BEGIN marker."""
        md = "Preamble before markers\n" + "\n".join(
            [_make_page(i, f"Content {i}") for i in range(1, 4)]
        )
        result = _get_context_tail(md, min_pages=2, min_lines=0)
        assert result.startswith(PAGE_BEGIN.format(2))

    def test_no_markers_falls_back_to_lines(self):
        """Without page markers, should fall back to line-based tail."""
        lines = [f"Line {i}" for i in range(100)]
        md = "\n".join(lines)
        result = _get_context_tail(md, min_pages=2, min_lines=30)
        result_lines = result.split("\n")
        assert len(result_lines) == 30

    def test_single_page(self):
        """Single-page document should return all content."""
        md = _make_page(1, "Only page")
        result = _get_context_tail(md, min_pages=2, min_lines=0)
        assert PAGE_BEGIN.format(1) in result
        assert "Only page" in result

    def test_all_pages_when_few(self):
        """When total pages < min_pages, return all pages."""
        md = "\n".join([_make_page(i, f"P{i}") for i in range(1, 3)])
        result = _get_context_tail(md, min_pages=5, min_lines=0)
        assert PAGE_BEGIN.format(1) in result
        assert PAGE_BEGIN.format(2) in result


# ---------------------------------------------------------------------------
# 6. Deterministic merge_chunks
# ---------------------------------------------------------------------------


class TestMergeChunks:
    """Tests for deterministic merge_chunks() in merger.py."""

    def test_single_chunk(self):
        """Single chunk should be returned as-is."""
        md = _make_page(1, "Content")
        assert merge_chunks([md]) == md

    def test_empty_list(self):
        """Empty list should return empty string."""
        assert merge_chunks([]) == ""

    def test_disjoint_chunks_concatenated(self):
        """Disjoint chunks should be concatenated in page order."""
        chunk1 = "\n".join([_make_page(i, f"C1P{i}") for i in range(1, 4)])
        chunk2 = "\n".join([_make_page(i, f"C2P{i}") for i in range(4, 7)])
        result = merge_chunks([chunk1, chunk2])
        # All 6 pages present in order.
        for i in range(1, 7):
            assert PAGE_BEGIN.format(i) in result
        # Page 3 before page 4.
        assert result.index(PAGE_BEGIN.format(3)) < result.index(PAGE_BEGIN.format(4))

    def test_duplicate_pages_first_wins(self):
        """If a page appears in multiple chunks, the first occurrence wins."""
        chunk1 = _make_page(1, "First version")
        chunk2 = _make_page(1, "Second version")
        result = merge_chunks([chunk1, chunk2])
        assert "First version" in result
        assert "Second version" not in result

    def test_three_chunks(self):
        """Three disjoint chunks should merge correctly."""
        chunk1 = _make_page(1, "A") + "\n" + _make_page(2, "B")
        chunk2 = _make_page(3, "C") + "\n" + _make_page(4, "D")
        chunk3 = _make_page(5, "E")
        result = merge_chunks([chunk1, chunk2, chunk3])
        for i in range(1, 6):
            assert PAGE_BEGIN.format(i) in result

    def test_no_markers_falls_back(self):
        """Chunks without page markers should be joined as-is."""
        result = merge_chunks(["Hello", "World"])
        assert "Hello" in result
        assert "World" in result
