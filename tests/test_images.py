"""Unit tests for the images module (IMAGE_RECT parsing, rendering, injection)."""

import re

import pytest

from pdf2md_claude.images import (
    ImageRect,
    inject_image_refs,
    parse_image_rects,
)
from pdf2md_claude.markers import (
    IMAGE_FILENAME_EXAMPLE,
    IMAGE_FILENAME_FORMAT,
    IMAGE_FILENAME_RE,
    IMAGE_RECT_EXAMPLE,
    IMAGE_RECT_MARKER_FORMAT,
    IMAGE_RECT_RE,
    IMAGE_REF_RE,
)


# ---------------------------------------------------------------------------
# IMAGE_RECT_RE — regex tests
# ---------------------------------------------------------------------------


class TestImageRectRegex:
    """Tests for IMAGE_RECT_RE matching."""

    def test_matches_example(self):
        m = IMAGE_RECT_RE.search(IMAGE_RECT_EXAMPLE)
        assert m is not None
        assert m.group(1) == "0.02"
        assert m.group(2) == "0.15"
        assert m.group(3) == "0.98"
        assert m.group(4) == "0.65"

    def test_matches_full_page(self):
        marker = "<!-- IMAGE_RECT 0.0,0.0,1.0,1.0 -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is not None
        assert float(m.group(1)) == 0.0
        assert float(m.group(4)) == 1.0

    def test_matches_with_extra_whitespace(self):
        marker = "<!--  IMAGE_RECT  0.1,0.2,0.8,0.9  -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is not None
        assert m.group(1) == "0.1"

    def test_no_match_on_missing_coords(self):
        marker = "<!-- IMAGE_RECT -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is None

    def test_no_match_on_wrong_tag(self):
        marker = "<!-- IMAGE_BEGIN 0.0,0.0,1.0,1.0 -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is None

    def test_captures_integer_coords(self):
        """Coordinates like 0 and 1 (no decimal) should still match."""
        marker = "<!-- IMAGE_RECT 0,0,1,1 -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is not None
        assert float(m.group(1)) == 0.0
        assert float(m.group(4)) == 1.0

    def test_no_match_on_old_format_with_page(self):
        """Old format with page number should NOT match the new regex."""
        marker = "<!-- IMAGE_RECT 5 0.02,0.15,0.98,0.65 -->"
        m = IMAGE_RECT_RE.search(marker)
        assert m is None


# ---------------------------------------------------------------------------
# IMAGE_RECT_MARKER_FORMAT
# ---------------------------------------------------------------------------


class TestImageRectFormat:
    """Tests for IMAGE_RECT_MARKER_FORMAT generation."""

    def test_format_basic(self):
        result = IMAGE_RECT_MARKER_FORMAT.format(
            x0=0.02, y0=0.15, x1=0.98, y1=0.65,
        )
        assert "IMAGE_RECT" in result
        assert "0.02" in result

    def test_format_roundtrips_through_regex(self):
        result = IMAGE_RECT_MARKER_FORMAT.format(
            x0=0.1, y0=0.2, x1=0.9, y1=0.8,
        )
        m = IMAGE_RECT_RE.search(result)
        assert m is not None
        assert float(m.group(1)) == pytest.approx(0.1)
        assert float(m.group(2)) == pytest.approx(0.2)
        assert float(m.group(3)) == pytest.approx(0.9)
        assert float(m.group(4)) == pytest.approx(0.8)

    def test_format_has_no_page_number(self):
        """Verify the format does not accept a 'page' parameter."""
        result = IMAGE_RECT_MARKER_FORMAT.format(
            x0=0.0, y0=0.0, x1=1.0, y1=1.0,
        )
        # Should not contain any bare integers between tag and coords.
        assert result == "<!-- IMAGE_RECT 0.0,0.0,1.0,1.0 -->"


# ---------------------------------------------------------------------------
# IMAGE_FILENAME_FORMAT / IMAGE_FILENAME_RE
# ---------------------------------------------------------------------------


class TestImageFilenamePatterns:
    """Tests for image filename format and regex."""

    def test_format_basic(self):
        assert IMAGE_FILENAME_FORMAT.format(page=1, idx=1) == "img_p001_01.png"

    def test_format_large_numbers(self):
        assert IMAGE_FILENAME_FORMAT.format(page=123, idx=5) == "img_p123_05.png"

    def test_example_matches_regex(self):
        """IMAGE_FILENAME_EXAMPLE must match IMAGE_FILENAME_RE."""
        m = IMAGE_FILENAME_RE.search(IMAGE_FILENAME_EXAMPLE)
        assert m is not None
        assert m.group(3) == "png"

    def test_example_matches_format_output(self):
        """IMAGE_FILENAME_EXAMPLE must equal what FORMAT produces."""
        assert IMAGE_FILENAME_FORMAT.format(page=1, idx=1) == IMAGE_FILENAME_EXAMPLE

    def test_regex_captures_groups(self):
        m = IMAGE_FILENAME_RE.search("img_p042_03.png")
        assert m is not None
        assert m.group(1) == "042"  # page
        assert m.group(2) == "03"   # index
        assert m.group(3) == "png"  # extension

    def test_regex_no_match_on_wrong_prefix(self):
        assert IMAGE_FILENAME_RE.search("image_p001_01.png") is None


# ---------------------------------------------------------------------------
# IMAGE_REF_RE — markdown image reference regex
# ---------------------------------------------------------------------------


class TestImageRefRegex:
    """Tests for IMAGE_REF_RE matching."""

    def test_matches_basic_ref(self):
        line = "![Figure 1](docling.images/img_p001_01.png)"
        m = IMAGE_REF_RE.search(line)
        assert m is not None
        assert m.group(1) == "Figure 1"
        assert "img_p001_01.png" in m.group(2)

    def test_matches_empty_alt(self):
        line = "![](some.images/img_p005_02.png)"
        m = IMAGE_REF_RE.search(line)
        assert m is not None
        assert m.group(1) == ""

    def test_no_match_on_non_image_ref(self):
        line = "![Figure 1](https://example.com/photo.png)"
        m = IMAGE_REF_RE.search(line)
        assert m is None

    def test_no_match_on_plain_text(self):
        assert IMAGE_REF_RE.search("just some text") is None


# ---------------------------------------------------------------------------
# parse_image_rects()
# ---------------------------------------------------------------------------


class TestParseImageRects:
    """Tests for parse_image_rects() function."""

    def test_empty_input(self):
        assert parse_image_rects("") == []

    def test_no_markers(self):
        md = "# Hello\n\nSome text with no image markers."
        assert parse_image_rects(md) == []

    def test_single_rect_with_caption(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 3 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.05,0.10,0.95,0.50 -->\n"
            "**Figure 1: Architecture diagram**\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 3 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 1
        r = rects[0]
        assert r.page_num == 3
        assert r.x0 == pytest.approx(0.05)
        assert r.y0 == pytest.approx(0.10)
        assert r.x1 == pytest.approx(0.95)
        assert r.y1 == pytest.approx(0.50)
        assert r.caption == "Figure 1: Architecture diagram"

    def test_multiple_rects_with_captions(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
            "**Figure A**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 1 -->\n"
            "Some text\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.1,0.2,0.9,0.8 -->\n"
            "**Figure B**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 2 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 2
        assert rects[0].page_num == 1
        assert rects[0].caption == "Figure A"
        assert rects[1].page_num == 2
        assert rects[1].caption == "Figure B"

    def test_preserves_document_order(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 5 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
            "**Fig 1**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 5 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.5,1.0,1.0 -->\n"
            "**Fig 2**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 3 -->\n"
            "<!-- PDF_PAGE_BEGIN 5 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.5,1.0,1.0 -->\n"
            "**Fig 3**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 5 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 3
        assert rects[0].page_num == 5
        assert rects[1].page_num == 3
        assert rects[2].page_num == 5

    def test_rect_without_caption(self):
        """IMAGE_RECT with no bold line gets empty caption."""
        md = (
            "<!-- PDF_PAGE_BEGIN 7 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Just a description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 7 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 1
        assert rects[0].page_num == 7
        assert rects[0].caption == ""

    def test_rect_outside_image_block_ignored(self):
        """IMAGE_RECT not inside IMAGE_BEGIN..END is ignored."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
            "Loose rect, not in a block.\n"
            "<!-- PDF_PAGE_END 1 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 0

    def test_rect_without_page_begin_skipped(self):
        """IMAGE_RECT with no preceding PAGE_BEGIN is skipped."""
        md = (
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
            "**Figure 1**\n"
            "<!-- IMAGE_END -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 0

    def test_page_number_from_page_begin(self):
        """Page number comes from PAGE_BEGIN, not IMAGE_RECT."""
        md = (
            "<!-- PDF_PAGE_BEGIN 42 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.1,0.2,0.9,0.8 -->\n"
            "**Figure 10**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 42 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 1
        assert rects[0].page_num == 42

    def test_two_images_same_page(self):
        """Two IMAGE blocks within the same PAGE_BEGIN share the page number."""
        md = (
            "<!-- PDF_PAGE_BEGIN 10 -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.0,1.0,0.4 -->\n"
            "**Figure A**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.0,0.5,1.0,0.9 -->\n"
            "**Figure B**\n"
            "<!-- IMAGE_END -->\n"
            "<!-- PDF_PAGE_END 10 -->\n"
        )
        rects = parse_image_rects(md)
        assert len(rects) == 2
        assert rects[0].page_num == 10
        assert rects[1].page_num == 10


# ---------------------------------------------------------------------------
# inject_image_refs()
# ---------------------------------------------------------------------------


_SAMPLE_MD_SINGLE = """\
<!-- PDF_PAGE_BEGIN 1 -->
# Title

<!-- IMAGE_BEGIN -->
<!-- IMAGE_RECT 0.05,0.10,0.95,0.50 -->
**Figure 1: Architecture diagram**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> The diagram shows components A and B connected by arrows.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->

Some text after the image.
<!-- PDF_PAGE_END 1 -->"""

_SAMPLE_MD_MULTI_PAGE = """\
<!-- PDF_PAGE_BEGIN 3 -->
Some text on page 3.

<!-- IMAGE_BEGIN -->
<!-- IMAGE_RECT 0.0,0.1,1.0,0.5 -->
**Figure 2: Data flow**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> Description of data flow.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->
<!-- PDF_PAGE_END 3 -->
<!-- PDF_PAGE_BEGIN 4 -->
<!-- IMAGE_BEGIN -->
<!-- IMAGE_RECT 0.0,0.2,1.0,0.7 -->
**Figure 3: Results chart**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> Chart showing results.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->
<!-- PDF_PAGE_END 4 -->"""


class TestInjectImageRefs:
    """Tests for inject_image_refs() function."""

    def test_single_image_injection(self):
        image_map = {1: ["img_p001_01.png"]}
        result = inject_image_refs(_SAMPLE_MD_SINGLE, image_map, "test.images")
        assert "![Figure 1: Architecture diagram](test.images/img_p001_01.png)" in result

    def test_ref_placed_after_caption(self):
        """Image ref should appear after a blank line following the caption."""
        image_map = {1: ["img_p001_01.png"]}
        result = inject_image_refs(_SAMPLE_MD_SINGLE, image_map, "test.images")
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if "**Figure 1: Architecture diagram**" in line:
                assert lines[i + 1] == "", "Expected blank line after caption"
                assert "![" in lines[i + 2]
                break
        else:
            pytest.fail("Caption line not found")

    def test_multi_page_injection(self):
        image_map = {
            3: ["img_p003_01.png"],
            4: ["img_p004_01.png"],
        }
        result = inject_image_refs(_SAMPLE_MD_MULTI_PAGE, image_map, "out.images")
        assert "![Figure 2: Data flow](out.images/img_p003_01.png)" in result
        assert "![Figure 3: Results chart](out.images/img_p004_01.png)" in result

    def test_no_image_map_returns_unchanged(self):
        result = inject_image_refs(_SAMPLE_MD_SINGLE, {}, "test.images")
        assert result == _SAMPLE_MD_SINGLE

    def test_missing_page_in_map_skips_gracefully(self):
        """If the page has no images in the map, no ref is injected."""
        image_map = {99: ["img_p099_01.png"]}
        result = inject_image_refs(_SAMPLE_MD_SINGLE, image_map, "test.images")
        assert "![" not in result

    def test_idempotent_no_double_injection(self):
        """Running inject twice should not duplicate the image reference."""
        image_map = {1: ["img_p001_01.png"]}
        first = inject_image_refs(_SAMPLE_MD_SINGLE, image_map, "test.images")
        second = inject_image_refs(first, image_map, "test.images")
        count = second.count("![Figure 1: Architecture diagram]")
        assert count == 1

    def test_multiple_images_same_page(self):
        md = """\
<!-- PDF_PAGE_BEGIN 5 -->
<!-- IMAGE_BEGIN -->
<!-- IMAGE_RECT 0.0,0.0,1.0,0.4 -->
**Figure A**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> Description A.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->

<!-- IMAGE_BEGIN -->
<!-- IMAGE_RECT 0.0,0.5,1.0,0.9 -->
**Figure B**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> Description B.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->
<!-- PDF_PAGE_END 5 -->"""

        image_map = {5: ["img_p005_01.png", "img_p005_02.png"]}
        result = inject_image_refs(md, image_map, "dir.images")
        assert "![Figure A](dir.images/img_p005_01.png)" in result
        assert "![Figure B](dir.images/img_p005_02.png)" in result

    def test_image_block_without_rect_no_crash(self):
        """IMAGE_BEGIN without IMAGE_RECT should still work (no injection)."""
        md = """\
<!-- PDF_PAGE_BEGIN 1 -->
<!-- IMAGE_BEGIN -->
**Figure 1: No rect**
<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->
> Just a description.
<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->
<!-- IMAGE_END -->
<!-- PDF_PAGE_END 1 -->"""

        # Empty map — no images to inject.
        result = inject_image_refs(md, {}, "test.images")
        assert "![" not in result
        # With map but no rect — still no crash.
        result2 = inject_image_refs(md, {1: ["img_p001_01.png"]}, "test.images")
        # Caption exists on page 1 so it should inject.
        assert "![Figure 1: No rect](test.images/img_p001_01.png)" in result2
