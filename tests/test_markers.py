"""Unit tests for the MarkerDef helper class in markers.py."""

import re

import pytest

from pdf2md_claude.markers import (
    IMAGE_AI_DESCRIPTION_BLOCK_RE,
    IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_MARKER,
    IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE,
    IMAGE_AI_GENERATED_DESCRIPTION_END_MARKER,
    IMAGE_AI_GENERATED_DESCRIPTION_END_RE,
    IMAGE_BEGIN_MARKER,
    IMAGE_BEGIN_RE,
    IMAGE_END_MARKER,
    IMAGE_END_RE,
    PAGE_BEGIN,
    PAGE_END,
    PAGE_SKIP_MARKER,
    PAGE_SKIP_RE,
    MarkerDef,
)


# ---------------------------------------------------------------------------
# MarkerDef.format()
# ---------------------------------------------------------------------------


class TestMarkerDefFormat:
    """Tests for MarkerDef.format() output."""

    def test_page_begin_format(self):
        assert PAGE_BEGIN.format(42) == "<!-- PDF_PAGE_BEGIN 42 -->"

    def test_page_end_format(self):
        assert PAGE_END.format(42) == "<!-- PDF_PAGE_END 42 -->"

    def test_format_page_1(self):
        assert PAGE_BEGIN.format(1) == "<!-- PDF_PAGE_BEGIN 1 -->"

    def test_format_large_page(self):
        assert PAGE_BEGIN.format(999) == "<!-- PDF_PAGE_BEGIN 999 -->"

    def test_custom_marker_format(self):
        m = MarkerDef("SECTION")
        assert m.format(7) == "<!-- SECTION 7 -->"


# ---------------------------------------------------------------------------
# MarkerDef.example
# ---------------------------------------------------------------------------


class TestMarkerDefExample:
    """Tests for MarkerDef.example property."""

    def test_page_begin_example(self):
        assert PAGE_BEGIN.example == "<!-- PDF_PAGE_BEGIN N -->"

    def test_page_end_example(self):
        assert PAGE_END.example == "<!-- PDF_PAGE_END N -->"

    def test_custom_marker_example(self):
        m = MarkerDef("FOO_BAR")
        assert m.example == "<!-- FOO_BAR N -->"


# ---------------------------------------------------------------------------
# MarkerDef.re (basic regex)
# ---------------------------------------------------------------------------


class TestMarkerDefRe:
    """Tests for the basic regex (captures value only)."""

    def test_matches_canonical(self):
        """Canonical format should match and capture the page number."""
        m = PAGE_BEGIN.re.search("<!-- PDF_PAGE_BEGIN 42 -->")
        assert m is not None
        assert m.group(1) == "42"

    def test_matches_tight_whitespace(self):
        """Minimal whitespace should still match."""
        m = PAGE_BEGIN.re.search("<!--PDF_PAGE_BEGIN 7-->")
        assert m is not None
        assert m.group(1) == "7"

    def test_matches_extra_whitespace(self):
        """Extra whitespace should still match."""
        m = PAGE_BEGIN.re.search("<!--  PDF_PAGE_BEGIN  99  -->")
        assert m is not None
        assert m.group(1) == "99"

    def test_findall_multiple(self):
        text = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nA\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nB\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nC"
        )
        assert PAGE_BEGIN.re.findall(text) == ["1", "2", "3"]

    def test_no_match_on_different_tag(self):
        """PAGE_BEGIN regex should not match PAGE_END markers."""
        m = PAGE_BEGIN.re.search("<!-- PDF_PAGE_END 42 -->")
        assert m is None

    def test_end_marker_matches(self):
        m = PAGE_END.re.search("<!-- PDF_PAGE_END 10 -->")
        assert m is not None
        assert m.group(1) == "10"


# ---------------------------------------------------------------------------
# MarkerDef.re_groups (grouped regex for substitution)
# ---------------------------------------------------------------------------


class TestMarkerDefReGroups:
    """Tests for the grouped regex (prefix, value, suffix)."""

    def test_three_groups(self):
        m = PAGE_BEGIN.re_groups.search("<!-- PDF_PAGE_BEGIN 42 -->")
        assert m is not None
        assert m.group(1) == "<!-- PDF_PAGE_BEGIN "
        assert m.group(2) == "42"
        assert m.group(3) == " -->"

    def test_substitution(self):
        """Grouped regex can remap page numbers via .sub()."""
        text = "<!-- PDF_PAGE_BEGIN 1 -->\nA\n<!-- PDF_PAGE_BEGIN 2 -->\nB"

        def remap(match: re.Match) -> str:
            new_page = int(match.group(2)) + 10
            return f"{match.group(1)}{new_page}{match.group(3)}"

        result = PAGE_BEGIN.re_groups.sub(remap, text)
        assert "<!-- PDF_PAGE_BEGIN 11 -->" in result
        assert "<!-- PDF_PAGE_BEGIN 12 -->" in result

    def test_tight_whitespace_groups(self):
        m = PAGE_BEGIN.re_groups.search("<!--PDF_PAGE_BEGIN 5-->")
        assert m is not None
        assert m.group(2) == "5"


# ---------------------------------------------------------------------------
# MarkerDef.re_line (line-anchored regex)
# ---------------------------------------------------------------------------


class TestMarkerDefReLine:
    """Tests for the line-anchored regex."""

    def test_matches_standalone_line(self):
        text = "before\n<!-- PDF_PAGE_BEGIN 42 -->\nafter"
        m = PAGE_BEGIN.re_line.search(text)
        assert m is not None
        assert m.group(1) == "42"

    def test_no_match_inline(self):
        """Marker embedded within other text on the same line should not match."""
        text = "text <!-- PDF_PAGE_BEGIN 42 --> more text"
        m = PAGE_BEGIN.re_line.search(text)
        assert m is None

    def test_findall_multiline(self):
        text = (
            "<!-- PDF_PAGE_BEGIN 10 -->\n"
            "content\n"
            "<!-- PDF_PAGE_BEGIN 11 -->\n"
            "more content"
        )
        assert PAGE_BEGIN.re_line.findall(text) == ["10", "11"]


# ---------------------------------------------------------------------------
# MarkerDef is frozen
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PAGE_SKIP valueless marker
# ---------------------------------------------------------------------------


class TestPageSkipMarker:
    """Tests for the PAGE_SKIP valueless marker."""

    def test_marker_string(self):
        assert PAGE_SKIP_MARKER == "<!-- PDF_PAGE_SKIP -->"

    def test_regex_matches_canonical(self):
        assert PAGE_SKIP_RE.search("<!-- PDF_PAGE_SKIP -->") is not None

    def test_regex_matches_extra_whitespace(self):
        assert PAGE_SKIP_RE.search("<!--  PDF_PAGE_SKIP  -->") is not None

    def test_regex_no_match_on_other(self):
        assert PAGE_SKIP_RE.search("<!-- PDF_PAGE_BEGIN 1 -->") is None

    def test_inside_page_markers(self):
        """PAGE_SKIP sits between BEGIN and END for a skipped page."""
        text = (
            "<!-- PDF_PAGE_BEGIN 9 -->\n"
            "<!-- PDF_PAGE_SKIP -->\n"
            "<!-- PDF_PAGE_END 9 -->"
        )
        assert PAGE_BEGIN.re.findall(text) == ["9"]
        assert PAGE_END.re.findall(text) == ["9"]
        assert PAGE_SKIP_RE.search(text) is not None


# ---------------------------------------------------------------------------
# MarkerDef is frozen
# ---------------------------------------------------------------------------


class TestMarkerDefFrozen:
    """MarkerDef should be immutable (frozen dataclass)."""

    def test_cannot_set_tag(self):
        with pytest.raises(AttributeError):
            PAGE_BEGIN.tag = "CHANGED"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# IMAGE_BEGIN / IMAGE_END valueless markers
# ---------------------------------------------------------------------------


class TestImageBlockMarkers:
    """Tests for IMAGE_BEGIN / IMAGE_END valueless markers."""

    def test_image_begin_marker_string(self):
        assert IMAGE_BEGIN_MARKER == "<!-- IMAGE_BEGIN -->"

    def test_image_end_marker_string(self):
        assert IMAGE_END_MARKER == "<!-- IMAGE_END -->"

    def test_image_begin_re_canonical(self):
        assert IMAGE_BEGIN_RE.search("<!-- IMAGE_BEGIN -->") is not None

    def test_image_end_re_canonical(self):
        assert IMAGE_END_RE.search("<!-- IMAGE_END -->") is not None

    def test_image_begin_re_extra_whitespace(self):
        assert IMAGE_BEGIN_RE.search("<!--  IMAGE_BEGIN  -->") is not None

    def test_image_end_re_extra_whitespace(self):
        assert IMAGE_END_RE.search("<!--  IMAGE_END  -->") is not None

    def test_image_begin_re_no_match_on_end(self):
        assert IMAGE_BEGIN_RE.search("<!-- IMAGE_END -->") is None

    def test_image_end_re_no_match_on_begin(self):
        assert IMAGE_END_RE.search("<!-- IMAGE_BEGIN -->") is None

    def test_image_begin_re_no_match_on_page(self):
        assert IMAGE_BEGIN_RE.search("<!-- PDF_PAGE_BEGIN 1 -->") is None


# ---------------------------------------------------------------------------
# IMAGE_AI_GENERATED_DESCRIPTION markers
# ---------------------------------------------------------------------------


class TestImageAIDescriptionMarkers:
    """Tests for IMAGE_AI_GENERATED_DESCRIPTION_BEGIN/END markers."""

    def test_begin_marker_string(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_MARKER == (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        )

    def test_end_marker_string(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_END_MARKER == (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        )

    def test_begin_re_canonical(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        ) is not None

    def test_end_re_canonical(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_END_RE.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        ) is not None

    def test_begin_re_extra_whitespace(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE.search(
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_BEGIN  -->"
        ) is not None

    def test_end_re_extra_whitespace(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_END_RE.search(
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_END  -->"
        ) is not None

    def test_begin_re_no_match_on_end(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        ) is None

    def test_end_re_no_match_on_begin(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_END_RE.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        ) is None

    def test_begin_re_no_match_on_image_begin(self):
        assert IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE.search(
            "<!-- IMAGE_BEGIN -->"
        ) is None


# ---------------------------------------------------------------------------
# IMAGE_AI_DESCRIPTION_BLOCK_RE (full block regex)
# ---------------------------------------------------------------------------


class TestImageAIDescriptionBlockRe:
    """Tests for the full AI-description block regex."""

    def test_matches_single_line_content(self):
        text = (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
            "> A timing diagram."
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        )
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is not None
        assert "> A timing diagram." in m.group(0)

    def test_matches_multiline_content(self):
        text = (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> The diagram shows two signal lines.\n"
            "> Line A represents the data signal.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        )
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is not None
        assert "signal lines" in m.group(0)

    def test_matches_with_extra_whitespace(self):
        text = (
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_BEGIN  -->\n"
            "> Description content.\n"
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_END  -->"
        )
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is not None

    def test_no_match_without_end(self):
        text = "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n> Orphaned."
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is None

    def test_strips_inside_larger_text(self):
        text = (
            "Some real content before.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> AI generated words that are completely fabricated.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "More real content after."
        )
        stripped = IMAGE_AI_DESCRIPTION_BLOCK_RE.sub("", text)
        assert "real content before" in stripped
        assert "real content after" in stripped
        assert "fabricated" not in stripped

    def test_nested_inside_image_block(self):
        """Full image block structure: IMAGE wrapping AI description."""
        text = (
            "<!-- IMAGE_BEGIN -->\n"
            "**Figure 5 – Timing diagram**\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> The diagram shows a waveform.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "<!-- IMAGE_END -->"
        )
        # IMAGE markers should match.
        assert IMAGE_BEGIN_RE.search(text) is not None
        assert IMAGE_END_RE.search(text) is not None
        # AI description block should match.
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is not None
        assert "waveform" in m.group(0)
