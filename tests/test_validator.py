"""Unit tests for the validation pipeline in validator.py."""

import pytest

from pdf2md_claude.markers import PAGE_SKIP
from pdf2md_claude.validator import (
    ValidationResult,
    check_page_fidelity,
    validate_output,
    _significant_words,
    _extract_page_contents,
)


# ---------------------------------------------------------------------------
# Helpers (shared page-building functions from conftest)
# ---------------------------------------------------------------------------

from tests.conftest import make_pages as _make_pages
from tests.conftest import wrap_pages as _wrap_pages


# ---------------------------------------------------------------------------
# _check_duplicate_headings
# ---------------------------------------------------------------------------

class TestDuplicateHeadings:
    """Tests for duplicate numbered section heading detection."""

    def _dup_warnings(self, result: ValidationResult) -> list[str]:
        """Extract all duplicate-heading warnings."""
        return [w for w in result.warnings if "Section " in w or "Duplicate" in w]

    def test_no_duplicates(self):
        md = _wrap_pages(
            "## 1 Scope\n\n## 2 References\n\n## 3 Definitions\n",
            start=1, end=3,
        )
        r = validate_output(md)
        assert not self._dup_warnings(r)

    def test_detects_duplicate_with_page_numbers(self):
        md = _make_pages({
            16: "## 9 Method of operation\n\n### 9.1 General\n",
            22: "## 9 Method of operation\n\n### 9.1 General\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        # Summary line + 2 detail lines.
        assert len(dups) == 3
        assert "2 sections" in dups[0]
        assert "Section 9 " in dups[1]
        assert "p16" in dups[1] and "p22" in dups[1]
        assert "Section 9.1 " in dups[2]
        assert "p16" in dups[2] and "p22" in dups[2]

    def test_detects_duplicate_subsections(self):
        md = _make_pages({
            16: "### 3.27 Short address\n\n### 3.28 Standby\n",
            21: "### 3.27 Short address\n\n### 3.28 Startup\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        # Summary + 2 detail lines.
        assert len(dups) == 3
        assert "Section 3.27" in dups[1]
        assert "p16" in dups[1] and "p21" in dups[1]

    def test_reports_count(self):
        md = _make_pages({
            17: "## 7 Transmission\n\n### 7.1 General\n\n### 7.2 Encoding\n",
            22: "## 7 Transmission\n\n### 7.1 General\n\n### 7.2 Encoding\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        assert "3 sections" in dups[0]

    def test_deep_subsection_duplicates(self):
        md = _make_pages({
            18: "#### 9.2.2.2 Standby\n",
            23: "#### 9.2.2.2 Standby\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        assert any("Section 9.2.2.2" in w for w in dups)
        assert any("p18" in w and "p23" in w for w in dups)

    def test_sorted_output(self):
        """Detail lines are sorted: numeric sections before lettered."""
        md = _make_pages({
            19: "### 9.3 Dimming\n\n### 3.27 Short\n",
            25: "### 3.27 Short\n\n### 9.3 Dimming\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        detail_lines = [w for w in dups if "Section " in w]
        assert len(detail_lines) == 2
        # 3.27 should appear before 9.3.
        assert "3.27" in detail_lines[0]
        assert "9.3" in detail_lines[1]

    def test_single_heading_no_warning(self):
        md = _wrap_pages("## 1 Scope\n", start=1, end=1)
        r = validate_output(md)
        assert not self._dup_warnings(r)

    def test_no_headings_no_warning(self):
        md = _wrap_pages("Just some text.\n", start=1, end=1)
        r = validate_output(md)
        assert not self._dup_warnings(r)

    def test_detects_annex_duplicates(self):
        md = _make_pages({
            82: "### A.1 Algorithm\n\n### A.2 Example\n",
            85: "### A.1 Algorithm\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        assert any("Section A.1" in w for w in dups)
        assert any("p82" in w and "p85" in w for w in dups)

    def test_mixed_numeric_and_letter_sort(self):
        """Numeric sections sort before lettered annex sections."""
        md = _make_pages({
            17: "## 9 Method\n\n### A.1 Example\n",
            22: "## 9 Method\n\n### A.1 Example\n",
        })
        r = validate_output(md)
        dups = self._dup_warnings(r)
        detail_lines = [w for w in dups if "Section " in w]
        assert len(detail_lines) == 2
        assert "Section 9 " in detail_lines[0]
        assert "Section A.1 " in detail_lines[1]


# ---------------------------------------------------------------------------
# Skipped pages (PDF_PAGE_SKIP)
# ---------------------------------------------------------------------------

class TestSkippedPages:
    """Tests for PAGE_SKIP marker recognition in validator."""

    def test_skipped_page_counted_in_info(self):
        """Skipped pages should appear in the info message."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\n<!-- PDF_PAGE_SKIP -->\n<!-- PDF_PAGE_END 2 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nMore content\n<!-- PDF_PAGE_END 3 -->\n"
        )
        r = validate_output(md)
        info_msgs = [i for i in r.info if "Page markers" in i]
        assert len(info_msgs) == 1
        assert "1 skipped" in info_msgs[0]

    def test_no_skipped_pages_no_suffix(self):
        """Without skips, info message has no 'skipped' suffix."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nMore\n<!-- PDF_PAGE_END 2 -->\n"
        )
        r = validate_output(md)
        info_msgs = [i for i in r.info if "Page markers" in i]
        assert len(info_msgs) == 1
        assert "skipped" not in info_msgs[0]

    def test_skipped_pages_no_gap_error(self):
        """Skipped pages should not cause 'missing page marker' errors."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nA\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\n<!-- PDF_PAGE_SKIP -->\n<!-- PDF_PAGE_END 2 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nB\n<!-- PDF_PAGE_END 3 -->\n"
        )
        r = validate_output(md)
        assert not any("Missing page marker" in e for e in r.errors)

    def test_multiple_skipped_pages(self):
        """Multiple consecutive skipped pages are all counted."""
        pages = []
        for p in range(1, 6):
            if p in (2, 3, 4):
                pages.append(
                    f"<!-- PDF_PAGE_BEGIN {p} -->\n"
                    f"<!-- PDF_PAGE_SKIP -->\n"
                    f"<!-- PDF_PAGE_END {p} -->"
                )
            else:
                pages.append(
                    f"<!-- PDF_PAGE_BEGIN {p} -->\n"
                    f"Content p{p}\n"
                    f"<!-- PDF_PAGE_END {p} -->"
                )
        md = "\n".join(pages)
        r = validate_output(md)
        info_msgs = [i for i in r.info if "Page markers" in i]
        assert "3 skipped" in info_msgs[0]


# ---------------------------------------------------------------------------
# _significant_words helper
# ---------------------------------------------------------------------------

class TestSignificantWords:
    """Tests for _significant_words text extraction."""

    def test_basic_extraction(self):
        words = _significant_words("The quick brown foxes jumped over")
        assert "quick" in words
        assert "brown" in words
        assert "foxes" in words
        assert "jumped" in words
        # "The" and "over" are < 5 chars, excluded.
        assert "the" not in words
        assert "over" not in words

    def test_strips_html_tags(self):
        words = _significant_words("<table><td>electrical specification</td></table>")
        assert "electrical" in words
        assert "specification" in words
        assert "table" not in words  # HTML tag, not content

    def test_strips_html_comments(self):
        words = _significant_words("<!-- PDF_PAGE_BEGIN 5 --> content here")
        # "content" is 7 chars and extracted from outside the comment.
        assert "content" in words
        # The marker words inside the comment should not leak through.
        assert "begin" not in words

    def test_strips_markdown_formatting(self):
        words = _significant_words("**electrical** *specification* `command`")
        assert "electrical" in words
        assert "specification" in words
        # "command" is 7 chars (>= 5), so it IS included after stripping backticks.
        assert "command" in words

    def test_formatting_chars_removed(self):
        """Markdown formatting characters are stripped, not treated as words."""
        words = _significant_words("# Heading with **bold**")
        assert "heading" in words
        # Single-char formatting symbols should never appear.
        assert "#" not in words

    def test_strips_latex(self):
        words = _significant_words("The formula $$x^2 + y^2 = z^2$$ is important")
        assert "formula" in words
        assert "important" in words

    def test_empty_input(self):
        assert _significant_words("") == set()

    def test_min_length_respected(self):
        words = _significant_words("ab cd efgh ijklm", min_length=4)
        assert "efgh" in words
        assert "ijklm" in words
        assert "cd" not in words


# ---------------------------------------------------------------------------
# _extract_page_contents helper
# ---------------------------------------------------------------------------

class TestExtractPageContents:
    """Tests for _extract_page_contents markdown parser."""

    def test_single_page(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 5 -->\n"
            "Some content here\n"
            "<!-- PDF_PAGE_END 5 -->"
        )
        result = _extract_page_contents(md)
        assert 5 in result
        assert "Some content here" in result[5]

    def test_multiple_pages(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nPage one\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nPage two\n<!-- PDF_PAGE_END 2 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nPage three\n<!-- PDF_PAGE_END 3 -->"
        )
        result = _extract_page_contents(md)
        assert len(result) == 3
        assert "Page one" in result[1]
        assert "Page two" in result[2]
        assert "Page three" in result[3]

    def test_skip_page_content_preserved(self):
        """PAGE_SKIP marker is part of the page content."""
        md = (
            "<!-- PDF_PAGE_BEGIN 2 -->\n"
            "<!-- PDF_PAGE_SKIP -->\n"
            "<!-- PDF_PAGE_END 2 -->"
        )
        result = _extract_page_contents(md)
        assert 2 in result
        assert PAGE_SKIP.tag in result[2]

    def test_empty_markdown(self):
        assert _extract_page_contents("") == {}

    def test_no_markers(self):
        assert _extract_page_contents("Just plain text") == {}


# ---------------------------------------------------------------------------
# check_page_fidelity
# ---------------------------------------------------------------------------

class TestPageFidelity:
    """Tests for per-page fidelity checking against PDF source text."""

    @pytest.fixture()
    def sample_pdf(self, tmp_path):
        """Create a minimal 3-page PDF with known text content."""
        import pymupdf

        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page(width=612, height=792)

        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    @pytest.fixture()
    def text_pdf(self, tmp_path):
        """Create a 3-page PDF with actual text content using raw PDF bytes."""
        pdf_path = tmp_path / "text_test.pdf"

        # Build a minimal PDF with text content by hand.
        # This is a minimal valid PDF with 3 pages containing text.
        _write_text_pdf(pdf_path, [
            "INTRODUCTION electrical specification requirements "
            "control gear digital addressable lighting interface "
            "standardization components forward frame encoding",
            "FOREWORD International Electrotechnical Commission "
            "worldwide organization standardization comprising "
            "national committees preparation publication",
            "transmission protocol structure general requirements "
            "method operation control devices dimming curve "
            "physical minimum level addressing system",
        ])
        return pdf_path

    def test_matching_content_no_warnings(self, text_pdf):
        """Pages whose markdown matches PDF text should not trigger warnings."""
        md = _make_pages({
            1: (
                "## INTRODUCTION\n\n"
                "The electrical specification requirements for control gear "
                "in a digital addressable lighting interface system cover "
                "standardization of components and forward frame encoding.\n"
            ),
            2: (
                "## FOREWORD\n\n"
                "The International Electrotechnical Commission is a worldwide "
                "organization for standardization comprising national "
                "committees for preparation and publication.\n"
            ),
            3: (
                "## Transmission protocol structure\n\n"
                "General requirements for the method of operation of "
                "control devices include dimming curve specifications, "
                "physical minimum level, and addressing system.\n"
            ),
        })
        result = ValidationResult()
        check_page_fidelity(text_pdf, md, result)
        fidelity_warnings = [w for w in result.warnings if "fidelity" in w.lower()]
        assert not fidelity_warnings

    def test_fabricated_content_detected(self, text_pdf):
        """Pages with fabricated content should trigger fidelity warnings."""
        md = _make_pages({
            1: (
                # Page 1 markdown talks about something completely different
                # from the PDF text (which is about electrical specifications).
                "## Memory Banks\n\n"
                "Memory banks provide storage locations for configuration "
                "parameters including operating modes manufacturer specific "
                "settings power failure recovery options and scene levels "
                "with protectable memory locations and sequential reading.\n"
            ),
            2: (
                "## FOREWORD\n\n"
                "The International Electrotechnical Commission is a worldwide "
                "organization for standardization comprising national "
                "committees for preparation and publication.\n"
            ),
            3: (
                "## Transmission protocol structure\n\n"
                "General requirements for the method of operation of "
                "control devices include dimming curve specifications, "
                "physical minimum level, and addressing system.\n"
            ),
        })
        result = ValidationResult()
        check_page_fidelity(text_pdf, md, result)
        # Summary line contains "fidelity".
        fidelity_summary = [w for w in result.warnings if "fidelity" in w.lower()]
        assert len(fidelity_summary) >= 1
        # Detail lines contain page numbers (all fidelity warnings together).
        all_fidelity = [w for w in result.warnings
                        if "fidelity" in w.lower() or "markdown words" in w.lower()]
        # Page 1 should be flagged.
        assert any("Page 1" in w for w in all_fidelity)
        # Pages 2 and 3 should NOT be flagged.
        assert not any("Page 2" in w for w in all_fidelity)
        assert not any("Page 3" in w for w in all_fidelity)

    def test_skipped_pages_ignored(self, text_pdf):
        """Pages with PAGE_SKIP should not be checked for fidelity."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\n"
            "<!-- PDF_PAGE_SKIP -->\n"
            "<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\n"
            "## FOREWORD\n\n"
            "The International Electrotechnical Commission is a worldwide "
            "organization for standardization comprising national "
            "committees for preparation and publication.\n"
            "<!-- PDF_PAGE_END 2 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\n"
            "## Transmission protocol structure\n\n"
            "General requirements for the method of operation of "
            "control devices include dimming curve specifications, "
            "physical minimum level, and addressing system.\n"
            "<!-- PDF_PAGE_END 3 -->"
        )
        result = ValidationResult()
        check_page_fidelity(text_pdf, md, result)
        fidelity_warnings = [w for w in result.warnings if "fidelity" in w.lower()]
        assert not fidelity_warnings

    def test_short_pages_ignored(self, text_pdf):
        """Pages with very little content should not trigger warnings."""
        md = _make_pages({
            1: "## Title\n\nShort.\n",  # Too few significant words
            2: "## FOREWORD\n\nBrief text.\n",  # Too few
            3: "## Section\n\nMinimal.\n",  # Too few
        })
        result = ValidationResult()
        check_page_fidelity(text_pdf, md, result)
        fidelity_warnings = [w for w in result.warnings if "fidelity" in w.lower()]
        assert not fidelity_warnings

    def test_missing_pdf_no_crash(self, tmp_path):
        """If the PDF doesn't exist, the check should silently skip."""
        md = _make_pages({1: "Some content with enough words for the "
                             "fidelity checker to actually process this page "
                             "without skipping.\n"})
        result = ValidationResult()
        check_page_fidelity(tmp_path / "nonexistent.pdf", md, result)
        assert not result.warnings
        assert not result.errors

    def test_blank_pdf_pages_ignored(self, sample_pdf):
        """Blank PDF pages (no extractable text) should not trigger warnings."""
        md = _make_pages({
            1: (
                "## Section heading\n\n"
                "Lots of content about electrical specifications and "
                "requirements for control gear in digital addressable "
                "lighting interface systems with standardization.\n"
            ),
        })
        result = ValidationResult()
        check_page_fidelity(sample_pdf, md, result)
        # Blank PDF pages have < 5 significant words, so they're skipped.
        fidelity_warnings = [w for w in result.warnings if "fidelity" in w.lower()]
        assert not fidelity_warnings


def _write_text_pdf(path, page_texts: list[str]):
    """Write a minimal PDF with text content on each page.

    Uses raw PDF syntax to create pages with actual extractable text.
    This avoids needing reportlab or other heavy dependencies.
    """
    # Build raw PDF with text streams.
    objects = []
    page_obj_nums = []

    # Object 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2: Pages (placeholder, written last)
    objects.append(None)  # placeholder

    # Object 3: Font
    objects.append(
        b"3 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
    )

    obj_num = 4
    for text in page_texts:
        # Content stream
        stream = f"BT /F1 10 Tf 72 720 Td ({text}) Tj ET".encode()
        stream_obj = (
            f"{obj_num} 0 obj\n"
            f"<< /Length {len(stream)} >>\n"
            f"stream\n"
        ).encode() + stream + b"\nendstream\nendobj\n"
        objects.append(stream_obj)
        content_num = obj_num
        obj_num += 1

        # Page object
        page_obj = (
            f"{obj_num} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 612 792] "
            f"/Contents {content_num} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>\n"
            f"endobj\n"
        ).encode()
        objects.append(page_obj)
        page_obj_nums.append(obj_num)
        obj_num += 1

    # Now fill in Pages object (object 2)
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects[1] = (
        f"2 0 obj\n"
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>\n"
        f"endobj\n"
    ).encode()

    # Write PDF
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n")
        offsets = []
        for obj_bytes in objects:
            offsets.append(f.tell())
            f.write(obj_bytes)

        xref_offset = f.tell()
        f.write(b"xref\n")
        f.write(f"0 {len(objects) + 1}\n".encode())
        f.write(b"0000000000 65535 f \n")
        for offset in offsets:
            f.write(f"{offset:010d} 00000 n \n".encode())

        f.write(b"trailer\n")
        f.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
        f.write(b"startxref\n")
        f.write(f"{xref_offset}\n".encode())
        f.write(b"%%EOF\n")


# ---------------------------------------------------------------------------
# _check_heading_sequence
# ---------------------------------------------------------------------------

class TestHeadingSequence:
    """Tests for heading gap detection."""

    def test_no_gap(self):
        md = _wrap_pages(
            "## 1 Scope\n\n## 2 References\n\n## 3 Definitions\n",
            start=1, end=3,
        )
        r = validate_output(md)
        assert not any("Section gap" in w for w in r.warnings)

    def test_gap_detected(self):
        md = _wrap_pages(
            "## 1 Scope\n\n## 3 Definitions\n",
            start=1, end=2,
        )
        r = validate_output(md)
        assert any("Section gap" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# _check_page_markers
# ---------------------------------------------------------------------------

class TestPageMarkers:
    """Tests for page marker validation."""

    def test_valid_markers(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nContent\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\nMore\n<!-- PDF_PAGE_END 2 -->\n"
        )
        r = validate_output(md)
        assert not r.errors or not any("page marker" in e.lower() for e in r.errors)

    def test_missing_markers(self):
        md = "Just text, no markers"
        r = validate_output(md)
        assert any("No page markers" in e for e in r.errors)

    def test_gap_in_markers(self):
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\nA\n<!-- PDF_PAGE_END 1 -->\n"
            "<!-- PDF_PAGE_BEGIN 3 -->\nB\n<!-- PDF_PAGE_END 3 -->\n"
        )
        r = validate_output(md)
        assert any("Missing page marker" in e for e in r.errors)


# ---------------------------------------------------------------------------
# _check_fabrication
# ---------------------------------------------------------------------------

class TestFabricationDetection:
    """Tests for fabricated content detection."""

    def test_clean_text(self):
        md = _wrap_pages("## 1 Scope\n\nNormal content.\n", start=1, end=1)
        r = validate_output(md)
        assert not any("fabricat" in e.lower() for e in r.errors)

    def test_summary_substitution(self):
        md = _wrap_pages(
            "presented as summary references for the commands\n",
            start=1, end=1,
        )
        r = validate_output(md)
        assert any("fabricat" in e.lower() for e in r.errors)

    def test_omission_note(self):
        md = _wrap_pages(
            "The table content has been omitted for brevity.\n",
            start=1, end=1,
        )
        r = validate_output(md)
        assert any("fabricat" in e.lower() for e in r.errors)


# ---------------------------------------------------------------------------
# _check_missing_figures
# ---------------------------------------------------------------------------

class TestMissingFigures:
    """Tests for missing figure reference detection."""

    def _figure_warnings(self, result: ValidationResult) -> list[str]:
        """Extract all figure-related warnings."""
        return [w for w in result.warnings if "Figure" in w]

    def test_no_warnings_when_all_defined(self):
        """Referenced figures that have bold captions should not warn."""
        md = _wrap_pages(
            "The setup is shown in Figure 1 below.\n\n"
            "<!-- IMAGE_BEGIN -->\n"
            "**Figure 1 – System overview**\n"
            "<!-- IMAGE_END -->\n",
            start=1, end=1,
        )
        r = validate_output(md)
        assert not self._figure_warnings(r)

    def test_warning_when_figure_not_defined(self):
        """Referencing a figure with no bold caption should warn."""
        md = _wrap_pages(
            "See Figure 3 for the timing diagram.\n",
            start=1, end=1,
        )
        r = validate_output(md)
        warnings = self._figure_warnings(r)
        assert len(warnings) == 1
        assert "Figure 3" in warnings[0]
        assert "not defined" in warnings[0]

    def test_multiple_missing_figures(self):
        """Multiple missing figures are each reported."""
        md = _wrap_pages(
            "See Figure 2 and Figure 5 for details.\n\n"
            "<!-- IMAGE_BEGIN -->\n"
            "**Figure 1 – Overview**\n"
            "<!-- IMAGE_END -->\n",
            start=1, end=1,
        )
        r = validate_output(md)
        warnings = self._figure_warnings(r)
        assert len(warnings) == 2
        assert any("Figure 2" in w for w in warnings)
        assert any("Figure 5" in w for w in warnings)

    def test_annex_figures_not_checked(self):
        """Annex figures (e.g. A.1) are skipped, matching table behavior."""
        md = _wrap_pages(
            "See Figure A.1 for the algorithm.\n",
            start=1, end=1,
        )
        r = validate_output(md)
        assert not self._figure_warnings(r)

    def test_no_false_positive_from_caption(self):
        """The bold caption itself contains 'Figure N' — should not warn."""
        md = _wrap_pages(
            "<!-- IMAGE_BEGIN -->\n"
            "**Figure 7 – Connection diagram**\n"
            "<!-- IMAGE_END -->\n",
            start=1, end=1,
        )
        r = validate_output(md)
        assert not self._figure_warnings(r)


# ---------------------------------------------------------------------------
# AI-generated image description stripping
# ---------------------------------------------------------------------------

class TestAIDescriptionStripping:
    """Tests for IMAGE_AI_GENERATED_DESCRIPTION exclusion in _significant_words."""

    def test_strips_ai_description_block(self):
        """AI description content should be excluded from significant words."""
        text = (
            "Real content about electrical specifications.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Fabricated description of a timing waveform showing\n"
            "> multiple signal transitions and voltage levels.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "More real content about protocol requirements."
        )
        words = _significant_words(text)
        # Words from real content should be present.
        assert "electrical" in words
        assert "specifications" in words
        assert "protocol" in words
        assert "requirements" in words
        # Words from AI description should be absent.
        assert "fabricated" not in words
        assert "waveform" not in words
        assert "transitions" not in words
        assert "voltage" not in words

    def test_strips_multiple_ai_blocks(self):
        """Multiple AI description blocks should all be stripped."""
        text = (
            "First section content.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Description alpha content.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "Middle section content.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Description bravo content.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "Final section content."
        )
        words = _significant_words(text)
        assert "section" in words
        assert "content" in words
        assert "alpha" not in words
        assert "bravo" not in words

    def test_preserves_words_outside_description(self):
        """Image caption (outside AI description markers) is preserved."""
        text = (
            "<!-- IMAGE_BEGIN -->\n"
            "**Figure 5 – Electrical timing diagram**\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Completely fabricated waveform description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "<!-- IMAGE_END -->"
        )
        words = _significant_words(text)
        # Caption words outside AI description should remain.
        assert "electrical" in words
        assert "timing" in words
        assert "diagram" in words
        # AI description words should be gone.
        assert "completely" not in words
        assert "fabricated" not in words
        assert "waveform" not in words


# ---------------------------------------------------------------------------
# _check_image_block_pairing
# ---------------------------------------------------------------------------

class TestImageBlockPairing:
    """Tests for IMAGE_BEGIN/IMAGE_END pairing validation."""

    def _image_errors(self, result: ValidationResult) -> list[str]:
        return [e for e in result.errors if "IMAGE" in e]

    def _image_info(self, result: ValidationResult) -> list[str]:
        return [i for i in result.info if "Image block" in i]

    def test_properly_paired_blocks(self):
        md = _make_pages({
            1: (
                "<!-- IMAGE_BEGIN -->\n"
                "<!-- IMAGE_RECT 0.0,0.0,1.0,0.5 -->\n"
                "**Figure 1**\n"
                "<!-- IMAGE_END -->\n"
            ),
        })
        r = validate_output(md)
        assert not self._image_errors(r)
        info = self._image_info(r)
        assert len(info) == 1
        assert "1 IMAGE_BEGIN" in info[0]
        assert "1 IMAGE_END" in info[0]

    def test_multiple_paired_blocks(self):
        md = _make_pages({
            1: (
                "<!-- IMAGE_BEGIN -->\n**Fig A**\n<!-- IMAGE_END -->\n"
                "<!-- IMAGE_BEGIN -->\n**Fig B**\n<!-- IMAGE_END -->\n"
            ),
        })
        r = validate_output(md)
        assert not self._image_errors(r)
        info = self._image_info(r)
        assert "2 IMAGE_BEGIN" in info[0]

    def test_unclosed_image_begin(self):
        md = _make_pages({
            3: (
                "<!-- IMAGE_BEGIN -->\n"
                "**Figure 1**\n"
            ),
        })
        r = validate_output(md)
        errors = self._image_errors(r)
        assert any("never closed" in e for e in errors)

    def test_image_end_without_begin(self):
        md = _make_pages({
            2: (
                "**Figure 1**\n"
                "<!-- IMAGE_END -->\n"
            ),
        })
        r = validate_output(md)
        errors = self._image_errors(r)
        assert any("without matching IMAGE_BEGIN" in e for e in errors)

    def test_nested_image_begin(self):
        md = _make_pages({
            5: (
                "<!-- IMAGE_BEGIN -->\n"
                "**Figure 1**\n"
                "<!-- IMAGE_BEGIN -->\n"
                "**Figure 2**\n"
                "<!-- IMAGE_END -->\n"
            ),
        })
        r = validate_output(md)
        errors = self._image_errors(r)
        assert any("Nested IMAGE_BEGIN" in e for e in errors)

    def test_no_image_blocks_no_info(self):
        md = _wrap_pages("Just text, no images.\n", start=1, end=1)
        r = validate_output(md)
        assert not self._image_info(r)


class TestPageFidelityWithAIDescriptions(TestPageFidelity):
    """Fidelity check should not flag pages with AI image descriptions."""

    def test_ai_description_excluded_from_fidelity(self, text_pdf):
        """AI descriptions should not cause fidelity warnings.

        Page 1 has matching real content plus a large AI-generated
        image description.  The description words are not in the PDF
        but should be excluded before the overlap check.
        """
        md = _make_pages({
            1: (
                "## INTRODUCTION\n\n"
                "The electrical specification requirements for control gear "
                "in a digital addressable lighting interface system cover "
                "standardization of components and forward frame encoding.\n"
                "<!-- IMAGE_BEGIN -->\n"
                "**Figure 1 – System overview**\n"
                "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
                "> The diagram illustrates a comprehensive architectural "
                "overview showing multiple interconnected subsystems with "
                "bidirectional communication pathways between controllers "
                "and peripheral luminaire management modules.\n"
                "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
                "<!-- IMAGE_END -->\n"
            ),
            2: (
                "## FOREWORD\n\n"
                "The International Electrotechnical Commission is a worldwide "
                "organization for standardization comprising national "
                "committees for preparation and publication.\n"
            ),
            3: (
                "## Transmission protocol structure\n\n"
                "General requirements for the method of operation of "
                "control devices include dimming curve specifications, "
                "physical minimum level, and addressing system.\n"
            ),
        })
        result = ValidationResult()
        check_page_fidelity(text_pdf, md, result)
        fidelity_warnings = [w for w in result.warnings if "fidelity" in w.lower()]
        assert not fidelity_warnings
