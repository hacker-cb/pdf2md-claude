"""Unit tests for format_markdown() and prettify_table() in formatter.py."""

from __future__ import annotations

import pytest

from pdf2md_claude.formatter import FormatMarkdownStep, format_markdown, prettify_table


# ---------------------------------------------------------------------------
# prettify_table: indentation normalization
# ---------------------------------------------------------------------------


class TestPrettifyTable:
    """Tests for HTML table prettification."""

    def test_no_indent_becomes_two_space(self) -> None:
        """Tables with zero indentation get 2-space indentation."""
        html = (
            "<table>\n"
            "<thead>\n"
            "<tr>\n"
            "<th>A</th>\n"
            "<th>B</th>\n"
            "</tr>\n"
            "</thead>\n"
            "<tbody>\n"
            "<tr>\n"
            "<td>1</td>\n"
            "<td>2</td>\n"
            "</tr>\n"
            "</tbody>\n"
            "</table>"
        )
        expected = (
            "<table>\n"
            "  <thead>\n"
            "    <tr>\n"
            "      <th>A</th>\n"
            "      <th>B</th>\n"
            "    </tr>\n"
            "  </thead>\n"
            "  <tbody>\n"
            "    <tr>\n"
            "      <td>1</td>\n"
            "      <td>2</td>\n"
            "    </tr>\n"
            "  </tbody>\n"
            "</table>"
        )
        assert prettify_table(html) == expected

    def test_already_indented_is_unchanged(self) -> None:
        """Tables already with 2-space indentation are not altered."""
        html = (
            "<table>\n"
            "  <thead>\n"
            "    <tr>\n"
            "      <th>X</th>\n"
            "    </tr>\n"
            "  </thead>\n"
            "</table>"
        )
        assert prettify_table(html) == html

    def test_compact_single_line_expanded(self) -> None:
        """Single-line compact rows get expanded to indented form."""
        html = "<table>\n<tr><th>A</th><th>B</th></tr>\n</table>"
        result = prettify_table(html)
        assert "  <tr>" in result
        assert "    <th>A</th>" in result
        assert "    <th>B</th>" in result

    def test_inline_html_preserved_in_cells(self) -> None:
        """Inline tags (<em>, <br>, <sup>) remain on the same line as the cell."""
        html = (
            "<table>\n"
            "<tr>\n"
            '<td><em>italic</em> text</td>\n'
            "<td>line1<br>line2</td>\n"
            "<td><sup>1</sup></td>\n"
            "</tr>\n"
            "</table>"
        )
        result = prettify_table(html)
        assert "<td><em>italic</em> text</td>" in result
        assert "<td>line1<br>line2</td>" in result
        assert "<td><sup>1</sup></td>" in result

    def test_colspan_rowspan_preserved(self) -> None:
        """Attributes like colspan and rowspan are preserved."""
        html = (
            "<table>\n"
            '<tr><td colspan="3">Wide cell</td></tr>\n'
            '<tr><td rowspan="2">Tall</td><td>A</td></tr>\n'
            "</table>"
        )
        result = prettify_table(html)
        assert 'colspan="3"' in result
        assert 'rowspan="2"' in result

    def test_html_comment_preserved(self) -> None:
        """HTML comments (e.g. page markers) are preserved."""
        html = (
            "<table>\n"
            "  <tbody>\n"
            "    <tr>\n"
            "      <td>before</td>\n"
            "    </tr>\n"
            "<!-- PDF_PAGE_END 5 -->\n"
            "<!-- PDF_PAGE_BEGIN 6 -->\n"
            "    <tr>\n"
            "      <td>after</td>\n"
            "    </tr>\n"
            "  </tbody>\n"
            "</table>"
        )
        result = prettify_table(html)
        assert "<!-- PDF_PAGE_END 5 -->" in result
        assert "<!-- PDF_PAGE_BEGIN 6 -->" in result

    def test_idempotent(self) -> None:
        """Running prettify_table twice produces the same result."""
        html = (
            "<table>\n"
            "<tr><td>A</td><td><em>B</em></td></tr>\n"
            '<tr><td colspan="2">C</td></tr>\n'
            "</table>"
        )
        once = prettify_table(html)
        twice = prettify_table(once)
        assert once == twice

    def test_img_tag_inline(self) -> None:
        """<img> tags inside cells stay inline."""
        html = (
            "<table>\n"
            '<tr><td><img src="foo.png"></td><td>caption</td></tr>\n'
            "</table>"
        )
        result = prettify_table(html)
        assert '<img src="foo.png">' in result
        # img and caption should be on separate <td> lines
        assert "    <td><img" in result

    def test_small_tag_inline(self) -> None:
        """<small> tags inside cells stay inline."""
        html = (
            "<table>\n"
            "<tr><td><small>tiny text</small></td></tr>\n"
            "</table>"
        )
        result = prettify_table(html)
        assert "<td><small>tiny text</small></td>" in result

    def test_entity_refs_preserved(self) -> None:
        """HTML entity references (&amp; etc.) are preserved."""
        html = (
            "<table>\n"
            "<tr><td>A &amp; B</td><td>1 &lt; 2</td></tr>\n"
            "</table>"
        )
        result = prettify_table(html)
        assert "A &amp; B" in result
        assert "1 &lt; 2" in result


# ---------------------------------------------------------------------------
# format_markdown: full pipeline
# ---------------------------------------------------------------------------


class TestFormatMarkdown:
    """Tests for the full format_markdown() function."""

    def test_table_in_markdown_context(self) -> None:
        """Tables embedded in markdown get prettified."""
        md = (
            "# Title\n\n"
            "Some text.\n\n"
            "<table>\n"
            "<tr><td>A</td></tr>\n"
            "</table>\n\n"
            "More text.\n"
        )
        result = format_markdown(md)
        assert "  <tr>" in result
        assert "    <td>A</td>" in result

    def test_multiple_tables(self) -> None:
        """Multiple tables in the same document are all prettified."""
        md = (
            "<table>\n<tr><td>1</td></tr>\n</table>\n\n"
            "Between.\n\n"
            "<table>\n<tr><td>2</td></tr>\n</table>\n"
        )
        result = format_markdown(md)
        assert result.count("  <tr>") == 2

    def test_trailing_whitespace_stripped(self) -> None:
        """Trailing spaces on lines are removed."""
        md = "Hello   \nWorld  \n"
        result = format_markdown(md)
        assert "Hello\n" in result
        assert "World\n" in result

    def test_excessive_blank_lines_collapsed(self) -> None:
        """3+ consecutive blank lines are collapsed to 1 blank line."""
        md = "A\n\n\n\n\nB\n"
        result = format_markdown(md)
        assert "A\n\nB\n" == result

    def test_single_trailing_newline(self) -> None:
        """Output always ends with exactly one newline."""
        assert format_markdown("text") == "text\n"
        assert format_markdown("text\n\n\n") == "text\n"

    def test_page_markers_preserved(self) -> None:
        """PDF page markers pass through unchanged."""
        md = (
            "<!-- PDF_PAGE_BEGIN 1 -->\n"
            "Content here.\n"
            "<!-- PDF_PAGE_END 1 -->\n\n"
            "<!-- PDF_PAGE_BEGIN 2 -->\n"
            "More content.\n"
            "<!-- PDF_PAGE_END 2 -->\n"
        )
        result = format_markdown(md)
        assert "<!-- PDF_PAGE_BEGIN 1 -->" in result
        assert "<!-- PDF_PAGE_END 1 -->" in result
        assert "<!-- PDF_PAGE_BEGIN 2 -->" in result
        assert "<!-- PDF_PAGE_END 2 -->" in result

    def test_image_markers_preserved(self) -> None:
        """IMAGE_BEGIN / IMAGE_RECT / IMAGE_END markers pass through."""
        md = (
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.1,0.2,0.8,0.9 -->\n"
            "![Figure 1](img.png)\n"
            "<!-- IMAGE_END -->\n"
        )
        result = format_markdown(md)
        assert "<!-- IMAGE_BEGIN -->" in result
        assert "<!-- IMAGE_RECT 0.1,0.2,0.8,0.9 -->" in result
        assert "<!-- IMAGE_END -->" in result

    def test_idempotent(self) -> None:
        """Running format_markdown twice gives the same result."""
        md = (
            "# Heading\n\n"
            "Text.\n\n"
            "<table>\n"
            "<tr><td>A</td><td><em>B</em></td></tr>\n"
            "</table>\n\n"
            "<!-- PDF_PAGE_END 1 -->\n"
        )
        once = format_markdown(md)
        twice = format_markdown(once)
        assert once == twice

    def test_blockquote_trailing_space_stripped(self) -> None:
        """Trailing spaces after > in blockquotes are stripped."""
        md = "> Some text   \n> \n> More text   \n"
        result = format_markdown(md)
        lines = result.splitlines()
        for line in lines:
            assert line == line.rstrip(), f"Trailing whitespace found: {line!r}"

    def test_empty_input(self) -> None:
        """Empty input returns single newline."""
        assert format_markdown("") == "\n"

    def test_no_tables_passthrough(self) -> None:
        """Content without tables is only lightly normalized."""
        md = "# Title\n\nParagraph.\n\n- item 1\n- item 2\n"
        result = format_markdown(md)
        assert "# Title" in result
        assert "- item 1" in result


# ---------------------------------------------------------------------------
# FormatMarkdownStep
# ---------------------------------------------------------------------------


class TestFormatMarkdownStep:
    """Tests for the pipeline step wrapper."""

    def test_step_name(self) -> None:
        step = FormatMarkdownStep()
        assert step.name == "format markdown"

    def test_step_modifies_context_markdown(self) -> None:
        """Step replaces ctx.markdown with formatted version."""
        from dataclasses import dataclass, field
        from pathlib import Path
        from pdf2md_claude.validator import ValidationResult

        @dataclass
        class FakeContext:
            markdown: str
            pdf_path: Path | None = None
            output_file: Path = Path("out.md")
            validation: ValidationResult = field(
                default_factory=ValidationResult,
            )

        ctx = FakeContext(
            markdown="<table>\n<tr><td>X</td></tr>\n</table>\n"
        )
        step = FormatMarkdownStep()
        step.run(ctx)  # type: ignore[arg-type]
        assert "  <tr>" in ctx.markdown
        assert "    <td>X</td>" in ctx.markdown
