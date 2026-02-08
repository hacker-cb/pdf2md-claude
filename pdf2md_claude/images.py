"""Image extraction and injection for PDF-to-Markdown conversion.

Two-phase workflow:

1. **Claude provides bounding boxes** via ``IMAGE_RECT`` markers in the
   markdown output (normalized 0.0–1.0 coordinates).
2. **Post-processing renders** those regions from the PDF using pymupdf
   and injects ``![caption](path)`` references into the markdown.

This handles raster images, vector diagrams, and mixed content — anything
that pymupdf can render from a PDF page region.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdf2md_claude.markers import (
    IMAGE_BEGIN_RE,
    IMAGE_END_RE,
    IMAGE_FILENAME_FORMAT,
    IMAGE_RECT_RE,
    IMAGE_REF_RE,
    PAGE_BEGIN,
)

_log = logging.getLogger("images")

DEFAULT_IMAGE_DPI = 150
"""Default DPI for rendering image regions from PDF pages."""

# Small padding (fraction of page size) added around each bounding box
# to account for imprecision in Claude's coordinate estimates.
_RECT_PADDING = 0.01


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ImageRect:
    """Parsed ``IMAGE_RECT`` marker data."""

    page_num: int
    """1-indexed PDF page number."""
    x0: float
    """Left edge (0.0–1.0, fraction of page width)."""
    y0: float
    """Top edge (0.0–1.0, fraction of page height)."""
    x1: float
    """Right edge (0.0–1.0, fraction of page width)."""
    y1: float
    """Bottom edge (0.0–1.0, fraction of page height)."""
    caption: str = ""
    """Caption text from the bold line in the IMAGE block.

    Used by :func:`inject_image_refs` to generate the ``![alt](path)``
    reference.
    """


@dataclass
class RenderedImage:
    """An image rendered from a PDF page region."""

    page_num: int
    """1-indexed PDF page number."""
    index: int
    """0-based index among images on the same page."""
    image_bytes: bytes
    """PNG image data."""
    filename: str
    """Filename (e.g. ``img_p005_01.png``)."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")
"""Matches a bold markdown line and captures the inner text."""


def parse_image_rects(markdown: str) -> list[ImageRect]:
    """Extract ``IMAGE_RECT`` markers and associated captions from markdown.

    Scans for ``IMAGE_BEGIN..IMAGE_END`` blocks.  Within each block, the
    ``IMAGE_RECT`` marker provides the bounding box and the ``**...**``
    bold line provides the figure caption (stored in
    :attr:`ImageRect.caption`).

    The page number is derived from the most recent ``PAGE_BEGIN`` marker
    preceding the block (not embedded in ``IMAGE_RECT`` itself).

    Returns a list of :class:`ImageRect` in document order.
    """
    rects: list[ImageRect] = []

    # Split into IMAGE blocks and extract rect + caption from each.
    lines = markdown.split("\n")
    in_block = False
    block_rect_match: re.Match[str] | None = None
    block_caption = ""
    current_page: int | None = None

    for line in lines:
        # Track current page from PAGE_BEGIN markers.
        page_match = PAGE_BEGIN.re.search(line)
        if page_match:
            current_page = int(page_match.group(1))

        if IMAGE_BEGIN_RE.search(line):
            in_block = True
            block_rect_match = None
            block_caption = ""
            continue

        if in_block and IMAGE_END_RE.search(line):
            # Flush block: if we found an IMAGE_RECT, emit it.
            if block_rect_match is not None and current_page is not None:
                m = block_rect_match
                rects.append(ImageRect(
                    page_num=current_page,
                    x0=float(m.group(1)),
                    y0=float(m.group(2)),
                    x1=float(m.group(3)),
                    y1=float(m.group(4)),
                    caption=block_caption,
                ))
            elif block_rect_match is not None and current_page is None:
                _log.warning(
                    "IMAGE_RECT found but no preceding PAGE_BEGIN — skipping"
                )
            in_block = False
            continue

        if in_block:
            # Look for IMAGE_RECT marker.
            rm = IMAGE_RECT_RE.search(line)
            if rm:
                block_rect_match = rm
            # Look for bold caption line.
            cm = _BOLD_LINE_RE.match(line.strip())
            if cm and not block_caption:
                block_caption = cm.group(1)

    return rects


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to [lo, hi]."""
    return max(lo, min(hi, v))


# Minimum image area as fraction of page area to be considered
# "significant" (filters out tiny icons, bullets, decorations).
_MIN_IMAGE_AREA_FRACTION = 0.02


def _rects_overlap_area(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    """Return the area of intersection between two rects (0 if no overlap)."""
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def _get_significant_rasters(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Return bounding rects of significant raster images on *page*.

    Filters out tiny images (icons, bullets, decorations) whose area is
    less than ``_MIN_IMAGE_AREA_FRACTION`` of the page area.

    Called once per page during the collect pass.
    """
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return []

    try:
        images = page.get_images(full=True)
    except Exception:
        return []

    rasters: list[pymupdf.Rect] = []
    for img in images:
        try:
            img_rects = page.get_image_rects(img)
        except Exception:
            continue
        for r in img_rects:
            if r.is_empty or r.is_infinite:
                continue
            img_area = r.width * r.height
            if img_area / page_area >= _MIN_IMAGE_AREA_FRACTION:
                rasters.append(r)

    return rasters


def _match_rasters_to_blocks(
    rasters: list[pymupdf.Rect],
    clips: list[pymupdf.Rect],
) -> dict[int, pymupdf.Rect | None]:
    """Match PDF raster images to IMAGE blocks using structural rules.

    Decision rules (ordered by priority):

    - **0 rasters** — all blocks get ``None`` (raw bbox).
    - **1 raster** — 100% certain match.  Assign to the
      best-overlapping block; other blocks (if any) get ``None``.
    - **N rasters, 1 block** — composite figure suspicion; block
      gets ``None`` (raw bbox).
    - **N rasters, M blocks (M >= 2)** — greedy 1:1 assignment by
      best overlap area.  Unmatched blocks get ``None``.

    Args:
        rasters: Significant raster rects on the page.
        clips: Claude's padded bounding-box rects (one per IMAGE block).

    Returns:
        ``{block_index: matched_raster_rect_or_None}``.
    """
    n_rasters = len(rasters)
    n_blocks = len(clips)
    result: dict[int, pymupdf.Rect | None] = {i: None for i in range(n_blocks)}

    if n_rasters == 0 or n_blocks == 0:
        return result

    # --- 1 raster: guaranteed match -----------------------------------------
    if n_rasters == 1:
        # Assign to the block with the best overlap.
        best_idx = 0
        best_overlap = 0.0
        for i, clip in enumerate(clips):
            overlap = _rects_overlap_area(rasters[0], clip)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
        result[best_idx] = rasters[0]
        return result

    # --- N rasters, 1 block: composite figure --------------------------------
    if n_blocks == 1:
        # Multiple rasters for a single figure block = composite.
        return result

    # --- N rasters, M blocks (M >= 2): greedy 1:1 ----------------------------
    # Build all (block, raster) pairs sorted by overlap descending.
    pairs: list[tuple[float, int, int]] = []  # (overlap, block_idx, raster_idx)
    for bi, clip in enumerate(clips):
        for ri, raster in enumerate(rasters):
            overlap = _rects_overlap_area(clip, raster)
            if overlap > 0:
                pairs.append((overlap, bi, ri))

    pairs.sort(reverse=True)

    used_blocks: set[int] = set()
    used_rasters: set[int] = set()
    for _overlap, bi, ri in pairs:
        if bi in used_blocks or ri in used_rasters:
            continue
        result[bi] = rasters[ri]
        used_blocks.add(bi)
        used_rasters.add(ri)

    return result


def _compute_padded_clip(
    ir: ImageRect,
    page: pymupdf.Page,
) -> pymupdf.Rect:
    """Convert normalized ``ImageRect`` coords to a padded pymupdf Rect."""
    pw = page.rect.width
    ph = page.rect.height
    x0 = _clamp(ir.x0 - _RECT_PADDING) * pw
    y0 = _clamp(ir.y0 - _RECT_PADDING) * ph
    x1 = _clamp(ir.x1 + _RECT_PADDING) * pw
    y1 = _clamp(ir.y1 + _RECT_PADDING) * ph
    return pymupdf.Rect(x0, y0, x1, y1)


def render_image_rects(
    pdf_path: Path,
    rects: list[ImageRect],
    dpi: int = DEFAULT_IMAGE_DPI,
) -> list[RenderedImage]:
    """Render image regions from a PDF using pymupdf.

    Uses a two-pass approach:

    **Pass 1 — Collect**: For each page that has IMAGE blocks, fetch all
    significant raster images (once per page) and compute padded clip rects.

    **Pass 2 — Match & render**: Use :func:`_match_rasters_to_blocks` to
    structurally assign rasters to blocks.  Matched blocks snap to the
    raster's exact boundaries; unmatched blocks use Claude's raw bbox.

    Args:
        pdf_path: Path to the source PDF.
        rects: Parsed bounding boxes from ``IMAGE_RECT`` markers.
        dpi: Resolution for rendering (default 150).

    Returns:
        List of :class:`RenderedImage` with PNG data and filenames.
    """
    if not rects:
        return []

    doc = pymupdf.open(str(pdf_path))
    rendered: list[RenderedImage] = []

    try:
        # ---------------------------------------------------------------
        # Pass 1: Collect per-page rasters and block clips.
        # ---------------------------------------------------------------
        # page_num -> (rasters, [(rect_index, ImageRect, clip)])
        page_data: dict[
            int,
            tuple[list[pymupdf.Rect], list[tuple[int, ImageRect, pymupdf.Rect]]],
        ] = {}

        for idx, ir in enumerate(rects):
            page_idx = ir.page_num - 1
            if page_idx < 0 or page_idx >= len(doc):
                _log.warning(
                    "IMAGE_RECT references page %d but PDF has %d pages — skipping",
                    ir.page_num, len(doc),
                )
                continue

            page = doc[page_idx]

            if ir.page_num not in page_data:
                rasters = _get_significant_rasters(page)
                page_data[ir.page_num] = (rasters, [])

            clip = _compute_padded_clip(ir, page)
            if clip.is_empty or clip.is_infinite:
                _log.warning(
                    "IMAGE_RECT on page %d produced invalid clip rect — skipping",
                    ir.page_num,
                )
                continue

            page_data[ir.page_num][1].append((idx, ir, clip))

        # ---------------------------------------------------------------
        # Pass 2: Match rasters to blocks, then render.
        # ---------------------------------------------------------------
        page_counters: dict[int, int] = {}

        for page_num, (rasters, blocks) in page_data.items():
            if not blocks:
                continue

            clips = [clip for _, _, clip in blocks]
            matches = _match_rasters_to_blocks(rasters, clips)

            _log.debug(
                "  page %d: %d raster(s), %d block(s)",
                page_num, len(rasters), len(blocks),
            )

            for i, (idx, ir, clip) in enumerate(blocks):
                matched_raster = matches.get(i)

                if matched_raster is not None:
                    final_clip = matched_raster
                    _log.debug(
                        "    %s: raster snap "
                        "(%.0f,%.0f,%.0f,%.0f)",
                        IMAGE_FILENAME_FORMAT.format(
                            page=page_num,
                            idx=page_counters.get(page_num, 0) + 1,
                        ),
                        final_clip.x0, final_clip.y0,
                        final_clip.x1, final_clip.y1,
                    )
                else:
                    final_clip = clip
                    reason = (
                        "no rasters on page"
                        if len(rasters) == 0
                        else "composite figure"
                        if len(blocks) == 1
                        else "unmatched"
                    )
                    _log.warning(
                        "    %s: raw bbox — %s, may include extra content",
                        IMAGE_FILENAME_FORMAT.format(
                            page=page_num,
                            idx=page_counters.get(page_num, 0) + 1,
                        ),
                        reason,
                    )

                if final_clip.is_empty or final_clip.is_infinite:
                    _log.warning(
                        "IMAGE_RECT on page %d: final rect is invalid — skipping",
                        page_num,
                    )
                    continue

                page = doc[page_num - 1]
                pix = page.get_pixmap(clip=final_clip, dpi=dpi)

                # CMYK → RGB conversion if needed.
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)

                img_bytes = pix.tobytes("png")

                img_idx = page_counters.get(page_num, 0)
                page_counters[page_num] = img_idx + 1

                filename = IMAGE_FILENAME_FORMAT.format(
                    page=page_num, idx=img_idx + 1,
                )
                rendered.append(RenderedImage(
                    page_num=page_num,
                    index=img_idx,
                    image_bytes=img_bytes,
                    filename=filename,
                ))

                _log.debug(
                    "  Rendered %s (page %d, %.0f×%.0f px)",
                    filename, page_num, pix.width, pix.height,
                )
    finally:
        doc.close()

    return rendered


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def save_images(
    rendered: list[RenderedImage],
    output_dir: Path,
) -> dict[int, list[str]]:
    """Save rendered images to disk.

    Creates ``output_dir`` if it does not exist.

    Args:
        rendered: Images from :func:`render_image_rects`.
        output_dir: Directory to save image files into.

    Returns:
        Mapping of 1-indexed page number to list of filenames saved
        for that page.
    """
    if not rendered:
        return {}

    output_dir.mkdir(parents=True, exist_ok=True)

    page_filenames: dict[int, list[str]] = {}
    for ri in rendered:
        path = output_dir / ri.filename
        path.write_bytes(ri.image_bytes)
        page_filenames.setdefault(ri.page_num, []).append(ri.filename)

    total = sum(len(v) for v in page_filenames.values())
    _log.info("  Saved %d image(s) to %s", total, output_dir)
    return page_filenames


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


def inject_image_refs(
    markdown: str,
    image_map: dict[int, list[str]],
    rel_prefix: str,
) -> str:
    """Inject ``![caption](path)`` references into ``IMAGE_BEGIN`` blocks.

    For each ``IMAGE_BEGIN`` block, determines the enclosing page number
    from ``PDF_PAGE_BEGIN N`` markers and the caption from the bold line.
    Then inserts an image reference after the caption, using the next
    available filename for that page from *image_map*.

    Blocks that already contain an image reference (detected via
    ``IMAGE_REF_RE``) are skipped (idempotent).

    Args:
        markdown: Full markdown text with ``IMAGE_BEGIN`` blocks.
        image_map: ``{page_num: [filename, ...]}`` from :func:`save_images`.
        rel_prefix: Relative path prefix (e.g. ``"docling.images"``).

    Returns:
        Updated markdown with image references injected.
    """
    if not image_map:
        return markdown

    lines = markdown.split("\n")
    result: list[str] = []

    # Track current page from PAGE_BEGIN markers.
    current_page: int | None = None

    # Track how many images we've consumed per page.
    page_consumed: dict[int, int] = {}

    # Buffer for lines inside an IMAGE block — we collect the whole block
    # before deciding whether to inject, so that the idempotency check
    # can see existing refs even if they appear after the caption.
    block_buffer: list[str] | None = None

    for line in lines:
        # Track page number.
        page_match = PAGE_BEGIN.re.search(line)
        if page_match:
            current_page = int(page_match.group(1))

        # Detect IMAGE_BEGIN — start buffering.
        if IMAGE_BEGIN_RE.search(line):
            block_buffer = [line]
            continue

        # Detect IMAGE_END — flush the buffered block.
        if block_buffer is not None and IMAGE_END_RE.search(line):
            block_buffer.append(line)
            result.extend(
                _process_image_block(
                    block_buffer, current_page, image_map,
                    page_consumed, rel_prefix,
                )
            )
            block_buffer = None
            continue

        # Inside an IMAGE block — keep buffering.
        if block_buffer is not None:
            block_buffer.append(line)
            continue

        result.append(line)

    # Flush any unclosed block (shouldn't happen in valid markdown).
    if block_buffer is not None:
        result.extend(block_buffer)

    return "\n".join(result)


def _process_image_block(
    block_lines: list[str],
    current_page: int | None,
    image_map: dict[int, list[str]],
    page_consumed: dict[int, int],
    rel_prefix: str,
) -> list[str]:
    """Process a single IMAGE_BEGIN..IMAGE_END block.

    If the block already contains an image reference, returns it unchanged.
    Otherwise, finds the bold caption line and inserts a reference after it.
    """
    # Check idempotency: if block already has an image ref, return as-is.
    for line in block_lines:
        if IMAGE_REF_RE.search(line):
            return block_lines

    if current_page is None:
        return block_lines

    filenames = image_map.get(current_page, [])
    consumed = page_consumed.get(current_page, 0)
    if consumed >= len(filenames):
        _log.warning(
            "  No image file for IMAGE block on page %d "
            "(consumed %d of %d available)",
            current_page, consumed, len(filenames),
        )
        return block_lines

    # Find the bold caption line and inject after it.
    output: list[str] = []
    injected = False
    for line in block_lines:
        output.append(line)
        if not injected:
            bold_match = _BOLD_LINE_RE.match(line.strip())
            if bold_match:
                fname = filenames[consumed]
                page_consumed[current_page] = consumed + 1

                caption_text = bold_match.group(1)
                output.append("")
                output.append(f"![{caption_text}]({rel_prefix}/{fname})")
                injected = True

    return output


# ---------------------------------------------------------------------------
# Convenience: full pipeline step
# ---------------------------------------------------------------------------


def extract_and_inject_images(
    pdf_path: Path,
    markdown: str,
    output_dir: Path,
    dpi: int = DEFAULT_IMAGE_DPI,
) -> str:
    """Parse IMAGE_RECT markers, render regions, save files, inject refs.

    This is the single entry point called from the pipeline.

    Args:
        pdf_path: Path to the source PDF.
        markdown: Merged markdown containing ``IMAGE_RECT`` markers.
        output_dir: Directory for saving rendered image files.
        dpi: Resolution for rendering (default 150).

    Returns:
        Updated markdown with ``![caption](path)`` references injected.
    """
    rects = parse_image_rects(markdown)
    if not rects:
        _log.info("  No IMAGE_RECT markers found — skipping image extraction")
        return markdown

    _log.info("  Found %d IMAGE_RECT marker(s), rendering...", len(rects))

    rendered = render_image_rects(pdf_path, rects, dpi=dpi)
    if not rendered:
        _log.warning("  All IMAGE_RECT markers failed to render")
        return markdown

    image_map = save_images(rendered, output_dir)
    rel_prefix = output_dir.name

    return inject_image_refs(markdown, image_map, rel_prefix)
