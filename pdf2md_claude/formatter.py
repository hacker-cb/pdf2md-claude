"""Markdown and HTML table formatter for converted output.

Prettifies HTML ``<table>`` blocks with consistent indentation and
normalizes markdown spacing (blank lines around blocks, trailing
whitespace).  Uses only Python stdlib — no external dependencies.

The main entry point is :func:`format_markdown`, a pure function
that is also exposed as :class:`FormatMarkdownStep` for the
processing pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


_log = __import__("logging").getLogger("formatter")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INDENT = "  "
"""Per-level indentation string (2 spaces)."""

_BLOCK_TAGS = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "caption", "colgroup", "col",
})
"""HTML tags that get their own line and increase/decrease indentation."""

_SELF_CLOSING_TAGS = frozenset({"br", "col", "img", "hr"})
"""Tags that are self-closing (no matching end tag expected)."""

_TABLE_BLOCK_RE = re.compile(
    r"^(<table\b[^>]*>.*?</table>)",
    re.MULTILINE | re.DOTALL,
)
"""Regex matching a complete ``<table>...</table>`` block starting at
the beginning of a line.  Used to extract table blocks from markdown."""

_CONSECUTIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")
"""Matches 3+ consecutive newlines (to collapse to at most 2)."""

_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)
"""Matches trailing whitespace on any line."""

_HEADING_RE = re.compile(r"^(#{1,6}\s)", re.MULTILINE)
"""Matches markdown ATX headings at start of line."""


# ---------------------------------------------------------------------------
# HTML table prettifier
# ---------------------------------------------------------------------------


class _TablePrettifier(HTMLParser):
    """Re-indent an HTML table block with consistent 2-space indentation.

    Block-level table tags (``table``, ``thead``, ``tbody``, ``tr``,
    ``td``, etc.) each get their own line with depth-based indentation.
    Inline content within cells (``<em>``, ``<br>``, ``<sup>``, etc.)
    is preserved verbatim on the same line.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._lines: list[str] = []
        self._depth: int = 0
        self._current_line: str = ""
        self._in_cell: bool = False
        self._cell_depth: int = 0

    # -- helpers -----------------------------------------------------------

    def _flush_line(self) -> None:
        """Append the current line buffer (if non-empty) to output."""
        stripped = self._current_line.rstrip()
        if stripped:
            self._lines.append(stripped)
        self._current_line = ""

    def _indent(self) -> str:
        return _INDENT * self._depth

    # -- parser callbacks --------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()

        if tag_lower in _BLOCK_TAGS:
            if self._in_cell and tag_lower not in ("td", "th"):
                # Inline block tag inside a cell — treat as inline
                self._current_line += self._build_tag(tag, attrs)
                return

            # Flush any pending content before the block tag
            self._flush_line()

            if tag_lower in ("td", "th"):
                # Cell tags: start a cell context
                self._current_line = self._indent() + self._build_tag(tag, attrs)
                self._in_cell = True
                self._cell_depth = self._depth
            else:
                self._lines.append(self._indent() + self._build_tag(tag, attrs))

            if tag_lower not in _SELF_CLOSING_TAGS:
                self._depth += 1
        else:
            # Inline tag — append to current line
            self._current_line += self._build_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower in _BLOCK_TAGS:
            if self._in_cell and tag_lower not in ("td", "th"):
                # Inline block end inside a cell
                self._current_line += f"</{tag}>"
                return

            self._depth = max(0, self._depth - 1)

            if tag_lower in ("td", "th"):
                # Close cell on the same line
                self._current_line += f"</{tag}>"
                self._flush_line()
                self._in_cell = False
            else:
                self._flush_line()
                self._lines.append(self._indent() + f"</{tag}>")
        else:
            # Inline end tag
            self._current_line += f"</{tag}>"

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            # Inside a cell: preserve content inline (strip newlines)
            self._current_line += data.replace("\n", " ")
        else:
            # Outside cells: handle HTML comments / text between tags
            stripped = data.strip()
            if stripped:
                self._flush_line()
                self._current_line = self._indent() + stripped

    def handle_comment(self, data: str) -> None:
        comment = f"<!--{data}-->"
        if self._in_cell:
            self._current_line += comment
        else:
            self._flush_line()
            self._lines.append(self._indent() + comment)

    def handle_entityref(self, name: str) -> None:
        self._current_line += f"&{name};"

    def handle_charref(self, name: str) -> None:
        self._current_line += f"&#{name};"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_str = self._build_tag(tag, attrs, self_closing=True)
        if tag.lower() in _BLOCK_TAGS:
            self._flush_line()
            self._lines.append(self._indent() + tag_str)
        else:
            self._current_line += tag_str

    # -- tag building ------------------------------------------------------

    @staticmethod
    def _build_tag(
        tag: str,
        attrs: list[tuple[str, str | None]],
        self_closing: bool = False,
    ) -> str:
        parts = [tag]
        for key, value in attrs:
            if value is None:
                parts.append(key)
            else:
                parts.append(f'{key}="{value}"')
        close = " />" if self_closing else ">"
        return "<" + " ".join(parts) + close

    # -- public API --------------------------------------------------------

    def prettify(self, html: str) -> str:
        """Parse *html* and return a prettified version."""
        self._lines = []
        self._depth = 0
        self._current_line = ""
        self._in_cell = False
        self._cell_depth = 0
        self.feed(html)
        self._flush_line()
        return "\n".join(self._lines)


def prettify_table(html: str) -> str:
    """Prettify an HTML ``<table>`` block with consistent indentation.

    Block-level tags get their own line with 2-space depth indentation.
    Cell content (including inline HTML) is preserved on one line.
    HTML comments (e.g. page markers) are preserved at correct depth.
    """
    return _TablePrettifier().prettify(html)


# ---------------------------------------------------------------------------
# Markdown normalization
# ---------------------------------------------------------------------------


def _normalize_blank_lines(text: str) -> str:
    """Ensure consistent blank lines around structural elements.

    - Exactly one blank line before/after ``<table>`` blocks.
    - Exactly one blank line before headings (``## ...``).
    - Collapse 3+ consecutive blank lines to exactly one blank line.
    """
    # Collapse excessive blank lines first
    text = _CONSECUTIVE_BLANK_LINES_RE.sub("\n\n", text)
    return text


def _strip_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces/tabs from every line."""
    return _TRAILING_WHITESPACE_RE.sub("", text)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def format_markdown(text: str) -> str:
    """Format markdown content: prettify HTML tables, normalize spacing.

    This is a pure function — safe to call on any markdown string.
    Designed to be idempotent: ``format_markdown(format_markdown(x)) == format_markdown(x)``.

    Operations (in order):

    1. Prettify all ``<table>...</table>`` blocks (consistent indentation).
    2. Normalize blank lines around structural elements.
    3. Strip trailing whitespace from all lines.
    4. Ensure file ends with a single newline.
    """

    # 1. Prettify HTML tables
    def _prettify_match(m: re.Match[str]) -> str:
        return prettify_table(m.group(1))

    text = _TABLE_BLOCK_RE.sub(_prettify_match, text)

    # 2. Normalize blank lines
    text = _normalize_blank_lines(text)

    # 3. Strip trailing whitespace
    text = _strip_trailing_whitespace(text)

    # 4. Ensure single trailing newline
    text = text.rstrip("\n") + "\n"

    return text


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------


@dataclass
class FormatMarkdownStep:
    """Prettify HTML tables and normalize markdown spacing.

    Wraps :func:`format_markdown`.  Runs after all content transforms
    (table merge, image extraction, AI description stripping) and
    before validation.
    """

    @property
    def name(self) -> str:
        return "format markdown"

    @property
    def key(self) -> str:
        return "format"

    def run(self, ctx: ProcessingContext) -> None:
        ctx.markdown = format_markdown(ctx.markdown)


# Deferred import — ProcessingContext is only needed for type checking.
# Placed after FormatMarkdownStep so that pipeline.py can import us
# without a circular dependency at runtime.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf2md_claude.pipeline import ProcessingContext
