"""Centralized marker definitions for PDF-to-Markdown conversion.

Single source of truth for all HTML comment markers used in converted
markdown output.  Provides format strings, compiled regexes, and prompt
examples so that no module needs to hard-code marker patterns.

Usage::

    from pdf2md_claude.markers import PAGE_BEGIN, PAGE_END

    # Generate a marker
    PAGE_BEGIN.format(42)   # '<!-- PDF_PAGE_BEGIN 42 -->'
    PAGE_END.format(42)     # '<!-- PDF_PAGE_END 42 -->'

    # Match markers in text
    PAGE_BEGIN.re.findall(text)         # ['42', '43', ...]
    PAGE_BEGIN.re_groups.sub(cb, text)  # substitution with groups
    PAGE_BEGIN.re_line.findall(text)    # line-anchored matches

    # Prompt-ready example text
    PAGE_BEGIN.example   # '<!-- PDF_PAGE_BEGIN N -->'
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class MarkerDef:
    """Definition of a single HTML-comment marker type.

    All regex variants are auto-generated from the *tag* name, so adding
    a new marker is a single line::

        SECTION = MarkerDef("SECTION")

    Attributes:
        tag: Upper-case token embedded in the HTML comment,
            e.g. ``"PDF_PAGE_BEGIN"``.
    """

    tag: str

    # -- Formatting --------------------------------------------------------

    def format(self, value: int) -> str:
        """Generate a marker string.

        >>> PAGE_BEGIN.format(42)
        '<!-- PDF_PAGE_BEGIN 42 -->'
        """
        return f"<!-- {self.tag} {value} -->"

    @property
    def example(self) -> str:
        """Human-readable example for use in prompts.

        >>> PAGE_BEGIN.example
        '<!-- PDF_PAGE_BEGIN N -->'
        """
        return f"<!-- {self.tag} N -->"

    # -- Compiled regexes (lazy) -------------------------------------------

    @cached_property
    def re(self) -> re.Pattern[str]:
        """Basic regex — captures ``(value)``.

        Use for scanning / validation where you only need the numeric
        value inside the marker.
        """
        return re.compile(rf"<!--\s*{re.escape(self.tag)}\s+(\d+)\s*-->")

    @cached_property
    def re_groups(self) -> re.Pattern[str]:
        """Grouped regex — captures ``(prefix)(value)(suffix)``.

        Use for substitution / remapping where you need to replace only
        the numeric value while preserving surrounding whitespace.
        """
        return re.compile(
            rf"(<!--\s*{re.escape(self.tag)}\s+)(\d+)(\s*-->)"
        )

    @cached_property
    def re_line(self) -> re.Pattern[str]:
        """Line-anchored regex — captures ``(value)``.

        Matches only when the marker is the sole content on its line.
        Uses ``re.MULTILINE``.
        """
        return re.compile(
            rf"^<!--\s*{re.escape(self.tag)}\s+(\d+)\s*-->$",
            re.MULTILINE,
        )


# ---------------------------------------------------------------------------
# Marker instances (single source of truth)
# ---------------------------------------------------------------------------

PAGE_BEGIN = MarkerDef("PDF_PAGE_BEGIN")
"""Marks the start of a PDF page's content in the converted markdown."""

PAGE_END = MarkerDef("PDF_PAGE_END")
"""Marks the end of a PDF page's content in the converted markdown."""

# ---------------------------------------------------------------------------
# Valueless markers (no numeric payload)
# ---------------------------------------------------------------------------

TABLE_CONTINUE_TAG = "TABLE_CONTINUE"
"""Tag name for the table-continuation marker."""

TABLE_CONTINUE_MARKER = f"<!-- {TABLE_CONTINUE_TAG} -->"
"""Literal marker string: ``<!-- TABLE_CONTINUE -->``."""

TABLE_CONTINUE_RE = re.compile(r"<!--\s*TABLE_CONTINUE\s*-->")
"""Regex matching a ``TABLE_CONTINUE`` marker (no capture groups)."""

TABLE_BLOCK_RE = re.compile(
    r"<table\b[^>]*>.*?</table>",
    re.DOTALL | re.IGNORECASE,
)
"""Regex matching a full ``<table>...</table>`` HTML block (no capture groups)."""

PAGE_SKIP_TAG = "PDF_PAGE_SKIP"
"""Tag name for the page-skip marker.

Placed between ``PAGE_BEGIN`` and ``PAGE_END`` when a page's content is
intentionally omitted (e.g., Table of Contents, copyright pages).
Preserves correct page numbering while signalling that the empty content
is deliberate, not an error.
"""

PAGE_SKIP_MARKER = f"<!-- {PAGE_SKIP_TAG} -->"
"""Literal marker string: ``<!-- PDF_PAGE_SKIP -->``."""

PAGE_SKIP_RE = re.compile(r"<!--\s*PDF_PAGE_SKIP\s*-->")
"""Regex matching a ``PDF_PAGE_SKIP`` marker (no capture groups)."""

# ---------------------------------------------------------------------------
# Image block markers (valueless)
# ---------------------------------------------------------------------------

IMAGE_BEGIN_TAG = "IMAGE_BEGIN"
"""Tag name for the image-block start marker."""

IMAGE_BEGIN_MARKER = f"<!-- {IMAGE_BEGIN_TAG} -->"
"""Literal marker string: ``<!-- IMAGE_BEGIN -->``."""

IMAGE_BEGIN_RE = re.compile(r"<!--\s*IMAGE_BEGIN\s*-->")
"""Regex matching an ``IMAGE_BEGIN`` marker (no capture groups)."""

IMAGE_END_TAG = "IMAGE_END"
"""Tag name for the image-block end marker."""

IMAGE_END_MARKER = f"<!-- {IMAGE_END_TAG} -->"
"""Literal marker string: ``<!-- IMAGE_END -->``."""

IMAGE_END_RE = re.compile(r"<!--\s*IMAGE_END\s*-->")
"""Regex matching an ``IMAGE_END`` marker (no capture groups)."""

# ---------------------------------------------------------------------------
# AI-generated image description markers (nested inside IMAGE block)
# ---------------------------------------------------------------------------

IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_TAG = "IMAGE_AI_GENERATED_DESCRIPTION_BEGIN"
"""Tag name for the AI-generated image description start marker.

Content between this marker and the corresponding END marker is an
AI-generated textual description of the image — it does NOT come from the
PDF source text and should be excluded from fidelity checks.
"""

IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_MARKER = (
    f"<!-- {IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_TAG} -->"
)
"""Literal marker string: ``<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->``."""

IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_RE = re.compile(
    r"<!--\s*IMAGE_AI_GENERATED_DESCRIPTION_BEGIN\s*-->"
)
"""Regex matching an ``IMAGE_AI_GENERATED_DESCRIPTION_BEGIN`` marker."""

IMAGE_AI_GENERATED_DESCRIPTION_END_TAG = "IMAGE_AI_GENERATED_DESCRIPTION_END"
"""Tag name for the AI-generated image description end marker."""

IMAGE_AI_GENERATED_DESCRIPTION_END_MARKER = (
    f"<!-- {IMAGE_AI_GENERATED_DESCRIPTION_END_TAG} -->"
)
"""Literal marker string: ``<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->``."""

IMAGE_AI_GENERATED_DESCRIPTION_END_RE = re.compile(
    r"<!--\s*IMAGE_AI_GENERATED_DESCRIPTION_END\s*-->"
)
"""Regex matching an ``IMAGE_AI_GENERATED_DESCRIPTION_END`` marker."""

IMAGE_AI_DESCRIPTION_BLOCK_RE = re.compile(
    r"<!--\s*IMAGE_AI_GENERATED_DESCRIPTION_BEGIN\s*-->"
    r".*?"
    r"<!--\s*IMAGE_AI_GENERATED_DESCRIPTION_END\s*-->",
    re.DOTALL,
)
"""Regex matching a full AI-generated description block (begin through end).

Use for stripping AI-generated content before fidelity checks.
"""

# ---------------------------------------------------------------------------
# Image bounding-box marker (inside IMAGE block, carries coordinates)
# ---------------------------------------------------------------------------

IMAGE_RECT_TAG = "IMAGE_RECT"
"""Tag name for the image bounding-box marker.

Placed inside an ``IMAGE_BEGIN`` / ``IMAGE_END`` block.  Carries
**normalized** bounding-box coordinates (0.0–1.0, origin at top-left).
The page number is derived from the enclosing ``PAGE_BEGIN`` marker,
so it is NOT repeated here.
"""

IMAGE_RECT_MARKER_FORMAT = "<!-- IMAGE_RECT {x0},{y0},{x1},{y1} -->"
"""Python format string for generating an ``IMAGE_RECT`` marker.

>>> IMAGE_RECT_MARKER_FORMAT.format(x0=0.02, y0=0.15, x1=0.98, y1=0.65)
'<!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->'
"""

IMAGE_RECT_EXAMPLE = "<!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->"
"""Human-readable example for use in prompts."""

IMAGE_RECT_RE = re.compile(
    r"<!--\s*IMAGE_RECT\s+"
    r"([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\s*-->"
)
"""Regex matching an ``IMAGE_RECT`` marker.

Captures four groups: ``(x0, y0, x1, y1)`` where coordinates
are normalized floats (0.0–1.0).  The page number comes from
the enclosing ``PAGE_BEGIN`` marker.
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
