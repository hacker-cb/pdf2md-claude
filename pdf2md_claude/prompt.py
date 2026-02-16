"""Prompts for Claude PDF → Markdown conversion.

Marker examples (``PAGE_BEGIN.example``, ``PAGE_END.example``) are
injected from :mod:`pdf2md_claude.markers` so that prompt text and
code always reference the same marker format.
"""

from pdf2md_claude.markers import (
    IMAGE_AI_DESC_BEGIN,
    IMAGE_AI_DESC_END,
    IMAGE_BEGIN,
    IMAGE_END,
    IMAGE_RECT,
    PAGE_BEGIN,
    PAGE_END,
    PAGE_SKIP,
    TABLE_CONTINUE,
)

# ---------------------------------------------------------------------------
# System prompt — individual rule definitions
# ---------------------------------------------------------------------------

_PREAMBLE_BODY = (
    "You are a precise document converter. "
    "Convert the provided PDF pages to clean, well-structured Markdown."
)

_PREAMBLE_CLOSING = "Follow these rules strictly:"

# ---- Framework (structural skeleton) ----

# Rule — Content fidelity
_RULE_FIDELITY = """\
**Content fidelity** (CRITICAL): Do NOT summarize, paraphrase, or omit any \
content. Every paragraph, table, image, note, warning, and \
footnote must appear in the output exactly as in the source.
   - NEVER insert text that does not exist in the source document. Do not \
add editorial notes, summaries of omitted content, or "presented as summary \
references" placeholders. If you cannot fit all content, output what you can \
and stop -- do NOT substitute summaries for actual content.
   - **NEVER extrapolate from a Table of Contents**: If the pages you are \
processing contain a Table of Contents listing sections on later pages, \
do NOT generate content for those sections. Only convert content that is \
visually present on each specific PDF page. If the real content ends \
before the last page in your range, STOP — do NOT fabricate text for \
sections you can see in the TOC but that are not on the pages provided. \
Each page's markdown must correspond to what is actually printed on that \
physical page."""

# Rule — Page markers
_RULE_PAGE_MARKERS = f"""\
**Page markers** (CRITICAL): Wrap EVERY page's content with a begin/end \
marker pair. Missing page markers are treated as conversion errors.
   - Place `{PAGE_BEGIN.example}` at the start and `{PAGE_END.example}` at the end of each page.
   - Emit markers for EVERY page in the range — even blank pages, \
image-only pages, or skipped content (e.g., Table of Contents).
   - For skipped pages, place `{PAGE_SKIP.marker}` between the markers (see the **Skip** rule).
   - N is the original document page number — the correct page range \
will be specified in the conversion instructions.
   - Example structure:
   ```
   {PAGE_BEGIN.format(5)}
   ...page 5 content...
   {PAGE_END.format(5)}
   {PAGE_BEGIN.format(6)}
   {PAGE_SKIP.marker}
   {PAGE_END.format(6)}
   {PAGE_BEGIN.format(7)}
   ...page 7 content...
   {PAGE_END.format(7)}
   ```"""

# Rule — Skip elements
_RULE_SKIP = f"""\
**Skip**: Page headers, page footers, page numbers, and watermarks.
   - **CRITICAL**: When you skip entire page's content, you MUST still emit \
the page markers for that page. Place `{PAGE_SKIP.marker}` between the begin/end \
markers to signal the skip is intentional:
   ```
   {PAGE_BEGIN.format(9)}
   {PAGE_SKIP.marker}
   {PAGE_END.format(9)}
   ```
   This preserves correct page numbering. NEVER silently omit page \
markers — every page in the range must have a begin/end pair."""

# ---- Content (formatting and elements) ----

# Rule — Headings
_RULE_HEADINGS = """\
**Headings**:
   - **General policy**: Preserve the document's section numbering and \
hierarchy exactly as they appear in the source.
   - **Depth mapping**: Count the dot-separated numbers to determine \
Markdown heading depth:
     - `#` — document title (exactly one per document)
     - `##` — top-level sections (e.g. "1 Introduction")
     - `###` — subsections (e.g. "1.2 Scope")
     - `####` — sub-subsections (e.g. "1.2.1 General")
     - `#####` — deeper levels (e.g. "1.2.1.1 Details")"""

# Rule — Inline formatting (applies to ALL output)
_RULE_FORMATTING = """\
**Inline formatting**:
   - **Body text** — use Markdown syntax: `*italic*`, `**bold**`, `` `code` ``.
   - **Inside HTML tables** — use HTML tags: `<em>`, `<strong>`, `<code>`.
   - **Superscripts / Subscripts** (everywhere, body AND tables): ALWAYS use \
`<sup>` / `<sub>` — there is no Markdown equivalent. Do NOT use Unicode \
superscript/subscript characters (write `a<sup>2</sup>` not `a²`, \
`H<sub>2</sub>O` not `H₂O`).
   - **Dashes**: use an en-dash `–` for numeric ranges and list bullets; \
use a hyphen `-` only in compound words."""

# Rule — Formulas
_RULE_FORMULAS = """\
**Formulas**: Preserve mathematical formulas using LaTeX notation in `$$` \
blocks. Inline formulas use `$...$`."""

# Rule — Tables
_RULE_TABLES = f"""\
**Tables**: ALWAYS use HTML `<table>` format for ALL tables, even simple ones.
   - Use `<thead>` for header rows and `<tbody>` for data rows.
   - Use `<th>` for header cells and `<td>` for data cells.
   - Use `rowspan` and `colspan` for merged cells.
   - **Faithful structure** (CRITICAL): Reproduce the 100% original table \
structure with exact `colspan`/`rowspan` values.
      - If the PDF shows N header rows, output N `<tr>` rows in `<thead>` — \
do NOT collapse or merge header rows.
      - Use `rowspan` for cells that span multiple rows, `colspan` for \
cells that span multiple columns. A single cell can have BOTH attributes \
at the same time (e.g. `<th rowspan="2" colspan="3">`).
      - Preserve every blank/empty cell as `<td></td>` or `<th></th>`.
      - Keep empty separator rows — do NOT remove them.
      - The total column count MUST be identical for every row. A cell with \
`colspan="3"` counts as 3; a cell with `rowspan="N"` in row R occupies \
that column in rows R through R+N-1.
      - **Self-check**: compute the total column count from the full table \
(any row may use colspan/rowspan). Verify that EVERY row — header, data, \
and separator — resolves to the same total. Fix mismatches before \
outputting.
   - **Completeness** (CRITICAL): You MUST convert EVERY table completely, \
no matter how large or complex. NEVER replace a table with a summary, \
description, or "see below" reference. If a table has 100 rows, output all \
100 rows.
   - **Cell formatting**: Preserve checkmarks (use ✓), footnote markers \
(a, b, c, etc.), and ALL special symbols exactly as they appear. Use \
`<em>` (not `<i>`) for italics, and `<br>` (single, not double \
`<br><br>`) for line breaks within cells.
   - **Continued tables**: If a table on the current page is a continuation \
of a table from a previous page (the PDF shows "(continued)" in the header, \
or the table has the same column structure and title as one from a prior \
page), emit `{TABLE_CONTINUE.marker}` on its own line immediately BEFORE the table title \
or `<table>` tag. Still output the full table including its repeated headers \
exactly as they appear in the PDF — the marker is metadata for post-processing."""

# Rule — Images (diagrams, figures, charts, illustrations)
_RULE_IMAGES = f"""\
**Images** (diagrams, figures, charts, illustrations): Wrap every image in \
structured markers with a bounding box and a detailed description. \
Do NOT output `![...](...)` references — image files are generated \
automatically in post-processing.
   - Wrap the entire image block with `{IMAGE_BEGIN.marker}` and `{IMAGE_END.marker}` markers.
   - **Bounding box**: Immediately after `{IMAGE_BEGIN.marker}`, emit an `IMAGE_RECT` \
marker with normalized coordinates (0.0–1.0, origin at top-left, \
x grows right, y grows down): \
`{IMAGE_RECT.prompt_template}`. Example: `{IMAGE_RECT.example}`.
   - **Bounding box precision** (CRITICAL — read carefully): \
Define the box by locating the **text boundaries** around the figure, \
NOT by trying to find the figure's visual edges. Use the body text \
you are transcribing as reference landmarks — you know exactly where \
each text line sits on the page because you are reading it.
     - **Top edge (y1)**: find the last line of body text or heading \
you transcribed ABOVE this figure. Place y1 just below that text \
line's baseline. Everything between that text and the caption below \
is figure content and must be inside the box.
     - **Bottom edge (y2)**: find the figure caption \
(e.g., "Figure N – ...", "图 N ...", "Рис. N — ...") or the first \
line of body text below the figure. Place y2 just above that line. \
The caption is transcribed separately (as bold text in the image \
block) and must NOT be inside the box.
     - **Left / right edges**: use the outermost drawn elements or, \
if unclear, the page text margins.
     - **Why text-based landmarks**: Figures may contain a mix of \
raster images and vector graphics (boxes, lines, arrows) that are \
hard to visually bound. But the text gap on the page reliably \
contains the complete figure — all sub-parts, whether raster or \
vector-drawn, sit in the space between the surrounding paragraphs.
     - **Self-check**: every element mentioned in your AI description \
of the figure must be geometrically inside the IMAGE_RECT. If your \
description mentions elements that would fall outside your y1–y2 \
range, widen the box.
   - **Caption**: Preserve the original caption exactly as it appears in \
the PDF (e.g., "Figure 5 – Timing diagram") as a `**bold**` line inside \
the image block.
   - **Description**: Wrap with `{IMAGE_AI_DESC_BEGIN.marker}` and `{IMAGE_AI_DESC_END.marker}` markers. Inside, \
output a blockquote (`> ...`) with a thorough description: all labeled \
elements, axes, values, arrows, connections, states, transitions, and \
spatial relationships. Include enough detail that a reader who cannot see \
the image can fully understand it.
   - **No content extraction from figures**: Content visible inside a figure \
(e.g., tables in screenshots, text in diagrams, code in panels) must NOT \
be reproduced as standalone text, tables, or code blocks outside the image \
block. The figure is already captured by its bounding box and AI \
description. Only convert content that exists as first-class document \
content on the page.
   - Example:
   ```
   {IMAGE_BEGIN.marker}
   {IMAGE_RECT.example}
   **Figure 5 – Timing diagram for forward frame**
   {IMAGE_AI_DESC_BEGIN.marker}
   > The diagram shows a timing waveform with two signal lines...
   {IMAGE_AI_DESC_END.marker}
   {IMAGE_END.marker}
   ```"""


# Named rule registry — each rule has a short key for programmatic access.
# Ordering: framework (structural skeleton) → content (formatting and elements).
_DEFAULT_REGISTRY: tuple[tuple[str, str], ...] = (
    ("fidelity",     _RULE_FIDELITY),       # 1. mindset: don't summarize/fabricate
    ("page_markers", _RULE_PAGE_MARKERS),   # 2. infra: page boundary markers
    ("skip",         _RULE_SKIP),           # 3. exclusions: headers/footers/watermarks
    ("headings",     _RULE_HEADINGS),       # 4. structure: section hierarchy
    ("formatting",   _RULE_FORMATTING),     # 5. style: sup/sub, dashes, italics
    ("formulas",     _RULE_FORMULAS),       # 6. content: math notation
    ("tables",       _RULE_TABLES),         # 7. content: table format (most complex)
    ("images",       _RULE_IMAGES),         # 8. content: images (diagrams/figures/charts)
)

def build_system_prompt(
    rules: list[str],
    preamble_body: str = _PREAMBLE_BODY,
) -> str:
    """Assemble rules into a numbered system prompt.

    Each rule is prefixed with its 1-based index (``1. ...``, ``2. ...``)
    and joined with blank lines.

    Parameters
    ----------
    rules:
        Ordered list of rule texts (numbering is generated automatically).
    preamble_body:
        Introductory text placed before the closing "Follow these rules
        strictly:" line.  Defaults to :data:`_PREAMBLE_BODY`.
    """
    numbered = [f"{i}. {rule}" for i, rule in enumerate(rules, 1)]
    return (
        preamble_body + "\n\n" + _PREAMBLE_CLOSING + "\n\n" + "\n\n".join(numbered)
    )


SYSTEM_PROMPT = build_system_prompt([text for _, text in _DEFAULT_REGISTRY])


# ---------------------------------------------------------------------------
# Context notes (per-chunk position)
# ---------------------------------------------------------------------------

CONTEXT_NOTE_START = (
    "This is the START of the document. Begin with an H1 heading "
    "containing the full official document title as it appears on "
    "the title page."
)

CONTEXT_NOTE_MIDDLE = (
    "This is a MIDDLE section. Continue from where the "
    "previous chunk ended."
)

CONTEXT_NOTE_END = (
    "This is the END of the document. Include all remaining "
    "content up to and including the Bibliography."
)

PREVIOUS_CONTEXT_BLOCK = (
    "The previous chunk ended with this content "
    "(for continuity — do NOT repeat it):\n"
    "<previous_context>\n"
    "{prev_context}\n"
    "</previous_context>"
)


# ---------------------------------------------------------------------------
# Chunk conversion prompt
# ---------------------------------------------------------------------------

# NOTE: Double braces {{...}} are literal braces after f-string evaluation;
# they become {chunk_num} etc. for later .format() calls at runtime.
CONVERT_CHUNK_PROMPT = f"""\
This is part {{chunk_num}} of {{total_chunks}} of a larger document. \
Convert these PDF pages to Markdown following the system instructions.

{{context_note}}

IMPORTANT: These PDF pages correspond to pages {{page_start}} through \
{{page_end}} of the original document ({{page_count}} pages). Wrap each \
page's content with `{PAGE_BEGIN.example}` and `{PAGE_END.example}` markers using the original page \
numbers: the first page of this chunk is page {{page_start}}, the next is \
page {{page_start_plus_1}}, and so on sequentially. You MUST emit exactly \
{{page_count}} begin/end marker pairs, one pair for each page from \
{{page_start}} to {{page_end}}.

{{previous_context_block}}

Output ONLY the markdown content."""
