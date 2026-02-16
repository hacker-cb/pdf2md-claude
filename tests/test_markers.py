"""Unit tests for the MarkerDef helper class in markers.py."""

import re

import pytest

from pdf2md_claude.markers import (
    IMAGE_AI_DESC_BEGIN,
    IMAGE_AI_DESC_END,
    IMAGE_AI_DESCRIPTION_BLOCK_RE,
    IMAGE_BEGIN,
    IMAGE_END,
    IMAGE_RECT,
    PAGE_BEGIN,
    PAGE_END,
    PAGE_SKIP,
    TABLE_CONTINUE,
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
        m = MarkerDef("SECTION", _value_re=r"(\d+)", _value_fmt="{0}",
                       _example_value="N")
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
        m = MarkerDef("FOO_BAR", _value_re=r"(\d+)", _value_fmt="{0}",
                       _example_value="N")
        assert m.example == "<!-- FOO_BAR N -->"

    def test_valueless_example_is_marker(self):
        """Valueless markers return the literal marker as example."""
        m = MarkerDef("SIMPLE")
        assert m.example == "<!-- SIMPLE -->"


# ---------------------------------------------------------------------------
# MarkerDef.re_value (basic regex)
# ---------------------------------------------------------------------------


class TestMarkerDefReValue:
    """Tests for the basic regex (captures value only)."""

    def test_matches_canonical(self):
        """Canonical format should match and capture the page number."""
        m = PAGE_BEGIN.re_value.search("<!-- PDF_PAGE_BEGIN 42 -->")
        assert m is not None
        assert m.group(1) == "42"

    def test_matches_tight_whitespace(self):
        """Minimal whitespace should still match."""
        m = PAGE_BEGIN.re_value.search("<!--PDF_PAGE_BEGIN 7-->")
        assert m is not None
        assert m.group(1) == "7"

    def test_matches_extra_whitespace(self):
        """Extra whitespace should still match."""
        m = PAGE_BEGIN.re_value.search("<!--  PDF_PAGE_BEGIN  99  -->")
        assert m is not None
        assert m.group(1) == "99"

    def test_findall_multiple(self):
        text = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nA\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nB\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nC"
        )
        assert PAGE_BEGIN.re_value.findall(text) == ["1", "2", "3"]

    def test_no_match_on_different_tag(self):
        """PAGE_BEGIN regex should not match PAGE_END markers."""
        m = PAGE_BEGIN.re_value.search("<!-- PDF_PAGE_END 42 -->")
        assert m is None

    def test_end_marker_matches(self):
        m = PAGE_END.re_value.search("<!-- PDF_PAGE_END 10 -->")
        assert m is not None
        assert m.group(1) == "10"


# ---------------------------------------------------------------------------
# MarkerDef.re_value_groups (grouped regex for substitution)
# ---------------------------------------------------------------------------


class TestMarkerDefReValueGroups:
    """Tests for the grouped regex (prefix, value, suffix)."""

    def test_three_groups(self):
        m = PAGE_BEGIN.re_value_groups.search("<!-- PDF_PAGE_BEGIN 42 -->")
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

        result = PAGE_BEGIN.re_value_groups.sub(remap, text)
        assert "<!-- PDF_PAGE_BEGIN 11 -->" in result
        assert "<!-- PDF_PAGE_BEGIN 12 -->" in result

    def test_tight_whitespace_groups(self):
        m = PAGE_BEGIN.re_value_groups.search("<!--PDF_PAGE_BEGIN 5-->")
        assert m is not None
        assert m.group(2) == "5"


# ---------------------------------------------------------------------------
# MarkerDef.re_value_line (line-anchored regex)
# ---------------------------------------------------------------------------


class TestMarkerDefReValueLine:
    """Tests for the line-anchored regex."""

    def test_matches_standalone_line(self):
        text = "before\n<!-- PDF_PAGE_BEGIN 42 -->\nafter"
        m = PAGE_BEGIN.re_value_line.search(text)
        assert m is not None
        assert m.group(1) == "42"

    def test_no_match_inline(self):
        """Marker embedded within other text on the same line should not match."""
        text = "text <!-- PDF_PAGE_BEGIN 42 --> more text"
        m = PAGE_BEGIN.re_value_line.search(text)
        assert m is None

    def test_findall_multiline(self):
        text = (
            "<!-- PDF_PAGE_BEGIN 10 -->\n"
            "content\n"
            "<!-- PDF_PAGE_BEGIN 11 -->\n"
            "more content"
        )
        assert PAGE_BEGIN.re_value_line.findall(text) == ["10", "11"]


# ---------------------------------------------------------------------------
# MarkerDef is frozen
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PAGE_SKIP valueless marker
# ---------------------------------------------------------------------------


class TestPageSkipMarker:
    """Tests for the PAGE_SKIP valueless marker."""

    def test_marker_string(self):
        assert PAGE_SKIP.marker == "<!-- PDF_PAGE_SKIP -->"

    def test_regex_matches_canonical(self):
        assert PAGE_SKIP.re.search("<!-- PDF_PAGE_SKIP -->") is not None

    def test_regex_matches_extra_whitespace(self):
        assert PAGE_SKIP.re.search("<!--  PDF_PAGE_SKIP  -->") is not None

    def test_regex_no_match_on_other(self):
        assert PAGE_SKIP.re.search("<!-- PDF_PAGE_BEGIN 1 -->") is None

    def test_inside_page_markers(self):
        """PAGE_SKIP sits between BEGIN and END for a skipped page."""
        text = (
            "<!-- PDF_PAGE_BEGIN 9 -->\n"
            "<!-- PDF_PAGE_SKIP -->\n"
            "<!-- PDF_PAGE_END 9 -->"
        )
        assert PAGE_BEGIN.re_value.findall(text) == ["9"]
        assert PAGE_END.re_value.findall(text) == ["9"]
        assert PAGE_SKIP.re.search(text) is not None


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
        assert IMAGE_BEGIN.marker == "<!-- IMAGE_BEGIN -->"

    def test_image_end_marker_string(self):
        assert IMAGE_END.marker == "<!-- IMAGE_END -->"

    def test_image_begin_re_canonical(self):
        assert IMAGE_BEGIN.re.search("<!-- IMAGE_BEGIN -->") is not None

    def test_image_end_re_canonical(self):
        assert IMAGE_END.re.search("<!-- IMAGE_END -->") is not None

    def test_image_begin_re_extra_whitespace(self):
        assert IMAGE_BEGIN.re.search("<!--  IMAGE_BEGIN  -->") is not None

    def test_image_end_re_extra_whitespace(self):
        assert IMAGE_END.re.search("<!--  IMAGE_END  -->") is not None

    def test_image_begin_re_no_match_on_end(self):
        assert IMAGE_BEGIN.re.search("<!-- IMAGE_END -->") is None

    def test_image_end_re_no_match_on_begin(self):
        assert IMAGE_END.re.search("<!-- IMAGE_BEGIN -->") is None

    def test_image_begin_re_no_match_on_page(self):
        assert IMAGE_BEGIN.re.search("<!-- PDF_PAGE_BEGIN 1 -->") is None


# ---------------------------------------------------------------------------
# IMAGE_AI_GENERATED_DESCRIPTION markers
# ---------------------------------------------------------------------------


class TestImageAIDescriptionMarkers:
    """Tests for IMAGE_AI_GENERATED_DESCRIPTION_BEGIN/END markers."""

    def test_begin_marker_string(self):
        assert IMAGE_AI_DESC_BEGIN.marker == (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        )

    def test_end_marker_string(self):
        assert IMAGE_AI_DESC_END.marker == (
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        )

    def test_begin_re_canonical(self):
        assert IMAGE_AI_DESC_BEGIN.re.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        ) is not None

    def test_end_re_canonical(self):
        assert IMAGE_AI_DESC_END.re.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        ) is not None

    def test_begin_re_extra_whitespace(self):
        assert IMAGE_AI_DESC_BEGIN.re.search(
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_BEGIN  -->"
        ) is not None

    def test_end_re_extra_whitespace(self):
        assert IMAGE_AI_DESC_END.re.search(
            "<!--  IMAGE_AI_GENERATED_DESCRIPTION_END  -->"
        ) is not None

    def test_begin_re_no_match_on_end(self):
        assert IMAGE_AI_DESC_BEGIN.re.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->"
        ) is None

    def test_end_re_no_match_on_begin(self):
        assert IMAGE_AI_DESC_END.re.search(
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->"
        ) is None

    def test_begin_re_no_match_on_image_begin(self):
        assert IMAGE_AI_DESC_BEGIN.re.search(
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
        assert IMAGE_BEGIN.re.search(text) is not None
        assert IMAGE_END.re.search(text) is not None
        # AI description block should match.
        m = IMAGE_AI_DESCRIPTION_BLOCK_RE.search(text)
        assert m is not None
        assert "waveform" in m.group(0)


# ---------------------------------------------------------------------------
# has_value property
# ---------------------------------------------------------------------------


class TestMarkerDefHasValue:
    """Tests for the has_value property."""

    def test_valued_markers(self):
        assert PAGE_BEGIN.has_value is True
        assert PAGE_END.has_value is True
        assert IMAGE_RECT.has_value is True

    def test_valueless_markers(self):
        assert TABLE_CONTINUE.has_value is False
        assert PAGE_SKIP.has_value is False
        assert IMAGE_BEGIN.has_value is False
        assert IMAGE_END.has_value is False
        assert IMAGE_AI_DESC_BEGIN.has_value is False
        assert IMAGE_AI_DESC_END.has_value is False


# ---------------------------------------------------------------------------
# prompt_template property
# ---------------------------------------------------------------------------


class TestMarkerDefPromptTemplate:
    """Tests for the prompt_template property."""

    def test_uses_prompt_value_when_set(self):
        assert IMAGE_RECT.prompt_template == (
            "<!-- IMAGE_RECT <x0>,<y0>,<x1>,<y1> -->"
        )

    def test_falls_back_to_example(self):
        assert PAGE_BEGIN.prompt_template == PAGE_BEGIN.example

    def test_valueless_falls_back_to_marker(self):
        assert TABLE_CONTINUE.prompt_template == TABLE_CONTINUE.marker


# ---------------------------------------------------------------------------
# TypeError guards (valueless markers reject valued operations)
# ---------------------------------------------------------------------------


class TestMarkerDefValuelessGuards:
    """Valueless markers raise TypeError for valued-only operations."""

    def test_format_raises_on_valueless(self):
        with pytest.raises(TypeError, match="valueless"):
            TABLE_CONTINUE.format(1)

    def test_re_value_raises_on_valueless(self):
        with pytest.raises(TypeError, match="valueless"):
            _ = PAGE_SKIP.re_value

    def test_re_value_groups_raises_on_valueless(self):
        with pytest.raises(TypeError, match="valueless"):
            _ = IMAGE_BEGIN.re_value_groups

    def test_re_value_line_raises_on_valueless(self):
        with pytest.raises(TypeError, match="valueless"):
            _ = IMAGE_END.re_value_line


# ---------------------------------------------------------------------------
# format() error handling (invalid args)
# ---------------------------------------------------------------------------


class TestMarkerDefFormatErrors:
    """format() wraps IndexError/KeyError with marker context."""

    def test_missing_positional_arg(self):
        with pytest.raises(TypeError, match="PDF_PAGE_BEGIN"):
            PAGE_BEGIN.format()  # needs 1 positional arg

    def test_missing_keyword_arg(self):
        with pytest.raises(TypeError, match="IMAGE_RECT"):
            IMAGE_RECT.format(x0=0.1)  # missing y0, x1, y1

    def test_positional_when_named_expected(self):
        with pytest.raises(TypeError, match="IMAGE_RECT"):
            IMAGE_RECT.format(0.1, 0.2, 0.3, 0.4)  # positional, not named


# ---------------------------------------------------------------------------
# IMAGE_RECT.re_value_groups (non-capturing conversion)
# ---------------------------------------------------------------------------


class TestImageRectReValueGroups:
    """re_value_groups on IMAGE_RECT should capture (prefix)(coords)(suffix)."""

    def test_three_groups(self):
        m = IMAGE_RECT.re_value_groups.search(
            "<!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->"
        )
        assert m is not None
        assert m.group(1) == "<!-- IMAGE_RECT "
        assert m.group(2) == "0.02,0.15,0.98,0.65"
        assert m.group(3) == " -->"

    def test_substitution(self):
        """Grouped regex can replace the full coordinate string."""
        text = "<!-- IMAGE_RECT 0.1,0.2,0.9,0.8 -->"

        def scale(match: re.Match) -> str:
            return f"{match.group(1)}REPLACED{match.group(3)}"

        result = IMAGE_RECT.re_value_groups.sub(scale, text)
        assert result == "<!-- IMAGE_RECT REPLACED -->"
