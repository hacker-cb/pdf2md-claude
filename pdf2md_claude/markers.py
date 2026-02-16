"""Centralized marker definitions for PDF-to-Markdown conversion.

Single source of truth for all HTML comment markers used in converted
markdown output.  Provides format strings, compiled regexes, and prompt
examples so that no module needs to hard-code marker patterns.

Every marker — valueless, integer-valued, or multi-valued — is a
:class:`MarkerDef` instance.  The class auto-generates literal strings,
compiled regexes, and prompt helpers from the tag name and an optional
value specification.

Usage::

    from pdf2md_claude.markers import PAGE_BEGIN, PAGE_END, IMAGE_RECT

    # Valueless marker
    PAGE_SKIP.marker            # '<!-- PDF_PAGE_SKIP -->'
    PAGE_SKIP.re.search(text)   # match valueless form

    # Integer-valued marker
    PAGE_BEGIN.format(42)       # '<!-- PDF_PAGE_BEGIN 42 -->'
    PAGE_BEGIN.example          # '<!-- PDF_PAGE_BEGIN N -->'
    PAGE_BEGIN.re_value.findall(text)          # ['42', '43', ...]
    PAGE_BEGIN.re_value_groups.sub(cb, text)   # substitution with groups
    PAGE_BEGIN.re_value_line.findall(text)     # line-anchored matches

    # Coordinate-valued marker
    IMAGE_RECT.format(x0=0.02, y0=0.15, x1=0.98, y1=0.65)
    IMAGE_RECT.example          # '<!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->'
    IMAGE_RECT.prompt_template  # '<!-- IMAGE_RECT <x0>,<y0>,<x1>,<y1> -->'
    IMAGE_RECT.re_value.search(text)  # captures (x0, y0, x1, y1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property


def _to_non_capturing(pattern: str) -> str:
    """Convert all capturing groups in *pattern* to non-capturing.

    Replaces ``(`` that starts a capturing group with ``(?:``.
    Already non-capturing groups (``(?:``), lookaheads (``(?=``),
    and other special groups (``(?...``) are left untouched.
    """
    return re.sub(r"\((?!\?)", "(?:", pattern)


@dataclass(frozen=True)
class MarkerDef:
    """Unified HTML-comment marker definition.

    Handles valueless, integer-valued, and multi-valued markers from a
    single class.  All regex variants are auto-generated from *tag* and
    the optional value specification.

    Parameters
    ----------
    tag:
        Upper-case token embedded in the HTML comment,
        e.g. ``"PDF_PAGE_BEGIN"``, ``"TABLE_CONTINUE"``.
    _value_re:
        Regex pattern for the value payload (with capture groups).
        Empty string means the marker is valueless.
    _value_fmt:
        Python ``.format()`` template for generating the value part.
        Supports both positional (``"{0}"``) and named
        (``"{x0},{y0},{x1},{y1}"``) placeholders.
    _example_value:
        Example value string for prompt helpers (e.g. ``"N"``).
    _prompt_value:
        Alternative template value for prompts (e.g.
        ``"<x0>,<y0>,<x1>,<y1>"``).  Falls back to *_example_value*
        when empty.
    """

    tag: str
    _value_re: str = ""
    _value_fmt: str = ""
    _example_value: str = ""
    _prompt_value: str = ""

    # -- Valueless form (always available) ---------------------------------

    @property
    def marker(self) -> str:
        """Literal valueless marker string.

        >>> TABLE_CONTINUE.marker
        '<!-- TABLE_CONTINUE -->'
        """
        return f"<!-- {self.tag} -->"

    @cached_property
    def re(self) -> re.Pattern[str]:
        """Regex matching the valueless form (no capture groups).

        >>> TABLE_CONTINUE.re.search('<!-- TABLE_CONTINUE -->') is not None
        True
        """
        return re.compile(rf"<!--\s*{re.escape(self.tag)}\s*-->")

    # -- Valued form -------------------------------------------------------

    @property
    def has_value(self) -> bool:
        """Whether this marker carries a value payload."""
        return bool(self._value_re)

    def format(self, *args: object, **kwargs: object) -> str:
        """Generate a marker string with a formatted value.

        Delegates to ``self._value_fmt.format(*args, **kwargs)``.

        >>> PAGE_BEGIN.format(42)
        '<!-- PDF_PAGE_BEGIN 42 -->'
        >>> IMAGE_RECT.format(x0=0.02, y0=0.15, x1=0.98, y1=0.65)
        '<!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->'
        """
        if not self._value_fmt:
            raise TypeError(
                f"Marker {self.tag!r} is valueless — "
                f"use .marker instead of .format()"
            )
        try:
            value = self._value_fmt.format(*args, **kwargs)
        except (IndexError, KeyError) as exc:
            raise TypeError(
                f"Marker {self.tag!r} format {self._value_fmt!r} "
                f"called with args={args}, kwargs={kwargs}"
            ) from exc
        return f"<!-- {self.tag} {value} -->"

    @property
    def example(self) -> str:
        """Human-readable example for use in prompts.

        Returns the valueless form when no example value is configured.

        >>> PAGE_BEGIN.example
        '<!-- PDF_PAGE_BEGIN N -->'
        >>> TABLE_CONTINUE.example
        '<!-- TABLE_CONTINUE -->'
        """
        if self._example_value:
            return f"<!-- {self.tag} {self._example_value} -->"
        return self.marker

    @property
    def prompt_template(self) -> str:
        """Prompt-ready template showing the value format.

        Uses ``_prompt_value`` when set, otherwise falls back to
        :attr:`example`.

        >>> IMAGE_RECT.prompt_template
        '<!-- IMAGE_RECT <x0>,<y0>,<x1>,<y1> -->'
        >>> PAGE_BEGIN.prompt_template
        '<!-- PDF_PAGE_BEGIN N -->'
        """
        if self._prompt_value:
            return f"<!-- {self.tag} {self._prompt_value} -->"
        return self.example

    @cached_property
    def re_value(self) -> re.Pattern[str]:
        """Regex matching the valued form — captures value group(s).

        For integer markers captures ``(value)``.  For coordinate
        markers captures ``(x0, y0, x1, y1)``.

        Raises ``TypeError`` if the marker is valueless.
        """
        if not self._value_re:
            raise TypeError(
                f"Marker {self.tag!r} is valueless — use .re instead"
            )
        return re.compile(
            rf"<!--\s*{re.escape(self.tag)}\s+{self._value_re}\s*-->"
        )

    @cached_property
    def re_value_groups(self) -> re.Pattern[str]:
        """Grouped regex — captures ``(prefix)(raw_value)(suffix)``.

        All capture groups within the value pattern are converted to
        non-capturing so that group(2) always contains the full raw
        value string.  Use for substitution / remapping.
        """
        if not self._value_re:
            raise TypeError(
                f"Marker {self.tag!r} is valueless — use .re instead"
            )
        nc = _to_non_capturing(self._value_re)
        return re.compile(
            rf"(<!--\s*{re.escape(self.tag)}\s+)({nc})(\s*-->)"
        )

    @cached_property
    def re_value_line(self) -> re.Pattern[str]:
        """Line-anchored regex — captures value group(s).

        Matches only when the marker is the sole content on its line.
        Uses ``re.MULTILINE``.
        """
        if not self._value_re:
            raise TypeError(
                f"Marker {self.tag!r} is valueless — use .re instead"
            )
        return re.compile(
            rf"^<!--\s*{re.escape(self.tag)}\s+{self._value_re}\s*-->$",
            re.MULTILINE,
        )


# ---------------------------------------------------------------------------
# Marker instances (single source of truth)
# ---------------------------------------------------------------------------

# -- Valued markers (integer payload) --------------------------------------

PAGE_BEGIN = MarkerDef(
    "PDF_PAGE_BEGIN",
    _value_re=r"(\d+)",
    _value_fmt="{0}",
    _example_value="N",
)
"""Marks the start of a PDF page's content in the converted markdown."""

PAGE_END = MarkerDef(
    "PDF_PAGE_END",
    _value_re=r"(\d+)",
    _value_fmt="{0}",
    _example_value="N",
)
"""Marks the end of a PDF page's content in the converted markdown."""

# -- Valueless markers (no payload) ----------------------------------------

TABLE_CONTINUE = MarkerDef("TABLE_CONTINUE")
"""Table-continuation marker.

Placed before a ``<table>`` that continues a table from a previous page.
"""

PAGE_SKIP = MarkerDef("PDF_PAGE_SKIP")
"""Page-skip marker.

Placed between ``PAGE_BEGIN`` and ``PAGE_END`` when a page's content is
intentionally omitted (e.g., Table of Contents, copyright pages).
Preserves correct page numbering while signalling that the empty content
is deliberate, not an error.
"""

IMAGE_BEGIN = MarkerDef("IMAGE_BEGIN")
"""Image-block start marker."""

IMAGE_END = MarkerDef("IMAGE_END")
"""Image-block end marker."""

IMAGE_AI_DESC_BEGIN = MarkerDef("IMAGE_AI_GENERATED_DESCRIPTION_BEGIN")
"""AI-generated image description start marker.

Content between this marker and the corresponding END marker is an
AI-generated textual description of the image — it does NOT come from the
PDF source text and should be excluded from fidelity checks.
"""

IMAGE_AI_DESC_END = MarkerDef("IMAGE_AI_GENERATED_DESCRIPTION_END")
"""AI-generated image description end marker."""

# -- Coordinate-valued marker (4-float payload) ----------------------------

IMAGE_RECT = MarkerDef(
    "IMAGE_RECT",
    _value_re=r"([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)",
    _value_fmt="{x0},{y0},{x1},{y1}",
    _example_value="0.02,0.15,0.98,0.65",
    _prompt_value="<x0>,<y0>,<x1>,<y1>",
)
"""Image bounding-box marker.

Placed inside an ``IMAGE_BEGIN`` / ``IMAGE_END`` block.  Carries
**normalized** bounding-box coordinates (0.0–1.0, origin at top-left).
The page number is derived from the enclosing ``PAGE_BEGIN`` marker,
so it is NOT repeated here.
"""

# ---------------------------------------------------------------------------
# Composite / utility regexes (not single-marker patterns)
# ---------------------------------------------------------------------------

TABLE_BLOCK_RE = re.compile(
    r"<table\b[^>]*>.*?</table>",
    re.DOTALL | re.IGNORECASE,
)
"""Regex matching a full ``<table>...</table>`` HTML block (no capture groups)."""

IMAGE_AI_DESCRIPTION_BLOCK_RE = re.compile(
    rf"<!--\s*{re.escape(IMAGE_AI_DESC_BEGIN.tag)}\s*-->"
    r".*?"
    rf"<!--\s*{re.escape(IMAGE_AI_DESC_END.tag)}\s*-->",
    re.DOTALL,
)
"""Regex matching a full AI-generated description block (begin through end).

Use for stripping AI-generated content before fidelity checks.
"""

# ---------------------------------------------------------------------------
# Extracted-image file naming
# ---------------------------------------------------------------------------

IMAGE_FILENAME_FORMAT = "img_p{page:03d}_{idx:02d}.{ext}"
"""Format string for extracted image filenames.

>>> IMAGE_FILENAME_FORMAT.format(page=1, idx=1, ext="png")
'img_p001_01.png'
"""

IMAGE_FILENAME_EXAMPLE = "img_p001_01.png"
"""Human-readable example for documentation."""

IMAGE_FILENAME_RE = re.compile(r"img_p(\d{3})_(\d{2})\.(\w+)")
"""Regex matching an extracted-image filename.

Captures ``(page, index, extension)``.
"""

IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*?/img_p\d{3}_\d{2}\.[\w.]+)\)")
"""Regex matching a markdown image reference to an extracted image.

Captures ``(alt_text, full_path)``.  Matches both single-extension
filenames (``img_p001_01.png``) and sub-extension filenames used by
debug mode (``img_p001_01.auto.png``).  Used for idempotent injection
(skip blocks that already contain a reference).
"""
