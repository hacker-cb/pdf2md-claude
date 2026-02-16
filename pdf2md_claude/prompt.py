"""Prompts for Claude PDF → Markdown conversion.

Marker examples (``PAGE_BEGIN.example``, ``PAGE_END.example``) are
injected from :mod:`pdf2md_claude.markers` so that prompt text and
code always reference the same marker format.
"""

from pdf2md_claude.markers import (
    IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_MARKER,
    IMAGE_AI_GENERATED_DESCRIPTION_END_MARKER,
    IMAGE_BEGIN_MARKER,
    IMAGE_END_MARKER,
    IMAGE_RECT_EXAMPLE,
    PAGE_BEGIN,
    PAGE_END,
    PAGE_SKIP_MARKER,
    TABLE_CONTINUE_MARKER,
)

# Short aliases for readability inside prompt strings.
_PB = PAGE_BEGIN.example  # <!-- PDF_PAGE_BEGIN N -->
_PE = PAGE_END.example  # <!-- PDF_PAGE_END N -->
_PS = PAGE_SKIP_MARKER  # <!-- PDF_PAGE_SKIP -->
_TC = TABLE_CONTINUE_MARKER  # <!-- TABLE_CONTINUE -->
_IB = IMAGE_BEGIN_MARKER  # <!-- IMAGE_BEGIN -->
_IE = IMAGE_END_MARKER  # <!-- IMAGE_END -->
_IDB = IMAGE_AI_GENERATED_DESCRIPTION_BEGIN_MARKER
_IDE = IMAGE_AI_GENERATED_DESCRIPTION_END_MARKER
_IR = IMAGE_RECT_EXAMPLE  # <!-- IMAGE_RECT 0.02,0.15,0.98,0.65 -->

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
   - Place `{_PB}` at the start and `{_PE}` at the end of each page.
   - Emit markers for EVERY page in the range — even blank pages, \
image-only pages, or skipped content (e.g., Table of Contents).
   - For skipped pages, place `{_PS}` between the markers (see the **Skip** rule).
   - N is the original document page number — the correct page range \
will be specified in the conversion instructions.
   - Example structure:
   ```
   {PAGE_BEGIN.format(5)}
   ...page 5 content...
   {PAGE_END.format(5)}
   {PAGE_BEGIN.format(6)}
   {_PS}
   {PAGE_END.format(6)}
   {PAGE_BEGIN.format(7)}
   ...page 7 content...
   {PAGE_END.format(7)}
   ```"""

# Rule — Skip elements
_RULE_SKIP = f"""\
**Skip**: Page headers, page footers, page numbers, watermarks, and \
copyright/license lines. Do NOT include the Table of Contents \
(it references printable page numbers which are meaningless in markdown).
   - **CRITICAL**: When you skip a page's content, you MUST still emit \
the page markers for that page. Place `{_PS}` between the begin/end \
markers to signal the skip is intentional:
   ```
   {PAGE_BEGIN.format(9)}
   {_PS}
   {PAGE_END.format(9)}
   ```
   This preserves correct page numbering. NEVER silently omit page \
markers — every page in the range must have a begin/end pair."""

# ---- Content (formatting and elements) ----

# Rule — Headings
_RULE_HEADINGS = """\
**Headings**: Preserve the document's section numbering and hierarchy. \
Count the dot-separated numbers to determine Markdown heading depth:
   - `#` — document title
   - `##` — top-level sections (e.g. "11 Definition of commands")
   - `###` — subsections (e.g. "11.2 Overview sheets")
   - `####` — sub-subsections (e.g. "11.2.1 General")
   - `#####` — deeper levels (e.g. "9.2.2.2 Standby")"""

# Rule — Inline formatting (applies to ALL output)
_RULE_FORMATTING = """\
**Inline formatting** (applies everywhere — body text AND tables):
   - Superscripts: ALWAYS use `<sup>`, not Unicode superscript characters \
(e.g., write `a<sup>2</sup>` not `a²`). This ensures full character \
coverage and consistent rendering.
   - Subscripts: ALWAYS use `<sub>`, not Unicode subscript characters \
(e.g., write `H<sub>2</sub>O` not `H₂O`).
   - Dashes: use an en-dash `–` for numeric ranges and list bullets; \
use a hyphen `-` only in compound words.
   - Italics in body text: use Markdown `*text*`. Inside HTML tables: \
use `<em>text</em>`."""

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
   - **Faithful structure** (CRITICAL): Reproduce the EXACT row and cell layout \
of the original table. If the PDF shows 3 header rows, output 3 `<tr>` rows in \
`<thead>` — do NOT collapse multiple header rows into one row. Preserve every \
blank/empty cell as an empty `<td></td>` or `<th></th>`. Keep empty separator \
rows as `<tr><td colspan="..."></td></tr>` — do NOT remove them. Use `rowspan` \
for cells that visually span multiple rows in the original.
   - Preserve checkmarks (use ✓), footnote markers (a, b, c, etc.), and ALL \
special symbols exactly as they appear.
   - Column-count consistency: ensure the total column count is IDENTICAL for \
every row. A cell with colspan="3" counts as 3. A cell with rowspan="N" \
in row R occupies that column in rows R through R+N-1. Verify your column \
math before outputting the table.
   - **Completeness** (CRITICAL): You MUST convert EVERY table completely, \
no matter how large or complex. NEVER replace a table with a summary, \
description, or "see below" reference. If a table has 100 rows, output all \
100 rows.
   - Inside tables use `<em>` (not `<i>`) for italics, and `<br>` (single, \
not double `<br><br>`) for line breaks within cells.
   - **Continued tables**: If a table on the current page is a continuation \
of a table from a previous page (the PDF shows "(continued)" in the header, \
or the table has the same column structure and title as one from a prior \
page), emit `{_TC}` on its own line immediately BEFORE the table title \
or `<table>` tag. Still output the full table including its repeated headers \
exactly as they appear in the PDF — the marker is metadata for post-processing."""

# Rule — Images (diagrams, figures, charts, illustrations)
_RULE_IMAGES = f"""\
**Images** (diagrams, figures, charts, illustrations): Wrap every image in \
structured markers with a bounding box and a detailed description. \
Do NOT output `![...](...)` references — image files are generated \
automatically in post-processing.
   - Wrap the entire image block with `{_IB}` and `{_IE}` markers.
   - **Bounding box**: Immediately after `{_IB}`, emit an `IMAGE_RECT` \
marker with normalized coordinates (0.0–1.0, origin at top-left, \
x grows right, y grows down): \
`<!-- IMAGE_RECT <x0>,<y0>,<x1>,<y1> -->`. Example: `{_IR}`.
   - **Bounding box precision** (CRITICAL — read carefully): \
Each edge must align with the outermost **drawn graphical primitive** \
of the figure (lines, shapes, axes, arrows, data points). \
Axis tick labels and data labels that are visually part of the graphic \
are included. Post-processing adds a small padding margin, so aim for \
the tightest box that still contains all graphical content — \
err on the side of slightly tight rather than loose.
     - **Top edge**: the topmost drawn element (e.g., top of a box, \
highest axis tick, tallest bar). Do NOT extend upward into headings, \
body text, page headers, or page numbers above the figure.
     - **Bottom edge**: the bottommost drawn element (e.g., X-axis line, \
lowest label of the graphic). Do NOT extend downward into the figure \
caption or body text below.
     - **Left / right edges**: the outermost drawn elements on each side.
   - **Bounding box exclusions**: The box must NEVER include any of \
the following — these are NOT part of the graphical content: \
page numbers or running headers/footers; \
figure captions in any language \
(e.g., "Figure 3 – Dimming curve", "图 3. 调光曲线", "Рис. 3 — Кривая"); \
figure number labels; section headings; \
body text paragraphs above or below the figure.
   - **Caption**: Preserve the original caption exactly as it appears in \
the PDF (e.g., "Figure 5 – Timing diagram") as a `**bold**` line inside \
the image block.
   - **Description**: Wrap with `{_IDB}` and `{_IDE}` markers. Inside, \
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
   {_IB}
   {_IR}
   **Figure 5 – Timing diagram for forward frame**
   {_IDB}
   > The diagram shows a timing waveform with two signal lines...
   {_IDE}
   {_IE}
   ```"""


# Named rule registry — each rule has a short key for programmatic access.
# Ordering: framework (structural skeleton) → content (formatting and elements).
_DEFAULT_REGISTRY: tuple[tuple[str, str], ...] = (
    ("fidelity",     _RULE_FIDELITY),       # 1. mindset: don't summarize/fabricate
    ("page_markers", _RULE_PAGE_MARKERS),   # 2. infra: page boundary markers
    ("skip",         _RULE_SKIP),           # 3. exclusions: headers/footers/TOC
    ("headings",     _RULE_HEADINGS),       # 4. structure: section hierarchy
    ("formatting",   _RULE_FORMATTING),     # 5. style: sup/sub, dashes, italics
    ("formulas",     _RULE_FORMULAS),       # 6. content: math notation
    ("tables",       _RULE_TABLES),         # 7. content: table format (most complex)
    ("images",       _RULE_IMAGES),         # 8. content: images (diagrams/figures/charts)
)

# Backward-compatible unnamed rule list, derived from the registry.
_RULES: list[str] = [text for _, text in _DEFAULT_REGISTRY]


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


SYSTEM_PROMPT = build_system_prompt(_RULES)


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
page's content with `{_PB}` and `{_PE}` markers using the original page \
numbers: the first page of this chunk is page {{page_start}}, the next is \
page {{page_start_plus_1}}, and so on sequentially. You MUST emit exactly \
{{page_count}} begin/end marker pairs, one pair for each page from \
{{page_start}} to {{page_end}}.

{{previous_context_block}}

Output ONLY the markdown content."""
