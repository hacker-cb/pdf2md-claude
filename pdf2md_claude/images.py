"""Image extraction and injection for PDF-to-Markdown conversion.

Three-phase workflow:

1. **Claude provides bounding boxes** via ``IMAGE_RECT`` markers in the
   markdown output (normalized 0.0–1.0 coordinates).
2. **Post-processing extracts or renders** those regions from the PDF
   using pymupdf — native raster extraction when possible, page-region
   rendering for vector/composite content.
3. **Injection** inserts ``![caption](path)`` references into the markdown.

Handles raster images (native extraction preserving original format),
vector diagrams, composite/tiled figures, and mixed content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
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

_RENDER_DPI = 600
"""DPI for page-region renders (vector diagrams, composites, fallbacks)."""

# Small padding (fraction of page size) added around each bounding box
# to account for imprecision in Claude's coordinate estimates.
_RECT_PADDING = 0.01


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ImageMode(Enum):
    """Image extraction strategy.

    Controls whether images are extracted natively from the PDF or
    rendered as page-region screenshots.
    """

    AUTO = "auto"
    """Try native raster extraction first; fall back to page rendering."""

    SNAP = "snap"
    """Render page regions snapped to PDF raster bounds, never extract native."""

    BBOX = "bbox"
    """Render AI-based bounding box directly, skip raster matching."""


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
    """An image extracted or rendered from a PDF page region."""

    page_num: int
    """1-indexed PDF page number."""
    index: int
    """0-based index among images on the same page."""
    image_bytes: bytes
    """Image data (PNG, JPEG, etc.)."""
    filename: str
    """Filename (e.g. ``img_p005_01.png`` or ``img_p005_01.jpeg``)."""


@dataclass
class PageRaster:
    """A significant raster image embedded in a PDF page.

    Produced by :func:`_index_page_rasters` and used internally for
    matching and native extraction.  Not part of the public API.
    """

    xref: int
    """Image object cross-reference number for ``doc.extract_image()``."""
    smask: int
    """Soft-mask xref (0 = no transparency mask)."""
    width: int
    """Native pixel width of the embedded image."""
    height: int
    """Native pixel height of the embedded image."""
    rect: pymupdf.Rect
    """Placement rectangle on the page (absolute points)."""


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


def _index_page_rasters(page: pymupdf.Page) -> list[PageRaster]:
    """Return significant raster images embedded on *page*.

    Calls ``page.get_images(full=True)`` and ``page.get_image_rects()``
    to collect placement metadata for each image.  Filters out tiny images
    (icons, bullets, decorations) whose area is less than
    ``_MIN_IMAGE_AREA_FRACTION`` of the page area.

    One image xref can have multiple placement rects on a page (same
    image placed at different positions); each placement produces a
    separate :class:`PageRaster` entry.
    """
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return []

    try:
        images = page.get_images(full=True)
    except Exception:
        return []

    # get_images(full=True) returns tuples:
    # (xref, smask, width, height, bpc, colorspace, ...)
    rasters: list[PageRaster] = []
    for img in images:
        xref = img[0]
        smask = img[1]
        img_width = img[2]
        img_height = img[3]

        try:
            img_rects = page.get_image_rects(img)
        except Exception:
            continue
        for r in img_rects:
            if r.is_empty or r.is_infinite:
                continue
            img_area = r.width * r.height
            if img_area / page_area >= _MIN_IMAGE_AREA_FRACTION:
                rasters.append(PageRaster(
                    xref=xref,
                    smask=smask,
                    width=img_width,
                    height=img_height,
                    rect=r,
                ))

    return rasters


def _match_rasters_to_blocks(
    rasters: list[PageRaster],
    clips: list[pymupdf.Rect],
) -> dict[int, list[PageRaster]]:
    """Match PDF raster images to IMAGE blocks by overlap.

    For each block clip, collects every raster whose placement rect has
    nonzero overlap with the clip.  The three-way rendering decision
    (single raster / composite / vector) is made by the caller based on
    ``len(matched)``.

    Args:
        rasters: Significant rasters on the page from
            :func:`_index_page_rasters`.
        clips: Claude's padded bounding-box rects (one per IMAGE block).

    Returns:
        ``{block_index: [overlapping PageRasters]}`` — empty list means
        no rasters overlap the block (pure vector / no rasters on page).
    """
    result: dict[int, list[PageRaster]] = {
        i: [] for i in range(len(clips))
    }

    for bi, clip in enumerate(clips):
        for raster in rasters:
            if _rects_overlap_area(clip, raster.rect) > 0:
                result[bi].append(raster)

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


def _compute_render_dpi(override: int | None = None) -> int:
    """Return the DPI for page-region rendering.

    Args:
        override: Explicit DPI from CLI ``--image-dpi``.  When ``None``,
            falls back to the module-level ``_RENDER_DPI`` constant.
    """
    return override if override is not None else _RENDER_DPI


def _pixmap_to_png(pix: pymupdf.Pixmap) -> bytes:
    """Convert a pymupdf Pixmap to PNG bytes, handling CMYK→RGB.

    If the pixmap has more than 3 color channels (excluding alpha),
    it is converted to sRGB before encoding.
    """
    if pix.n - pix.alpha > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("png")


def _render_region(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    dpi: int,
) -> tuple[bytes, str]:
    """Render a page region to PNG at the given DPI.

    Handles CMYK→RGB conversion via :func:`_pixmap_to_png`.

    Returns:
        ``(png_bytes, "png")``.
    """
    pix = page.get_pixmap(clip=clip, dpi=dpi)
    return _pixmap_to_png(pix), "png"


def _extract_native(
    doc: pymupdf.Document,
    raster: PageRaster,
) -> tuple[bytes, str] | None:
    """Try to extract a raster image natively from the PDF.

    Native extraction preserves the original image format (JPEG, PNG,
    etc.) and resolution, avoiding re-encoding losses.  The image is
    returned at its original resolution regardless of DPI — native bytes
    are already compressed and optimal.

    Falls back to ``None`` (caller should render the raster rect) when:

    - ``doc.extract_image()`` fails
    - The image has a soft mask (transparency) requiring compositing

    Returns:
        ``(image_bytes, extension)`` on success, or ``None``.
    """
    img_data = doc.extract_image(raster.xref)
    if img_data is None:
        return None

    # Soft mask requires compositing via Pixmap → always PNG.
    if raster.smask != 0:
        try:
            base_pix = pymupdf.Pixmap(doc, raster.xref)
            mask_pix = pymupdf.Pixmap(doc, raster.smask)
            combined = pymupdf.Pixmap(base_pix, mask_pix)
        except Exception:
            _log.debug("      smask compositing failed for xref %d", raster.xref)
            return None
        return _pixmap_to_png(combined), "png"

    # Native extraction — return original bytes and format.
    # No DPI cap: native bytes are already compressed (JPEG/PNG) and
    # optimal.  Capping would require re-rendering, losing quality.
    return img_data["image"], img_data["ext"]


def render_image_rects(
    doc: pymupdf.Document,
    rects: list[ImageRect],
    image_mode: ImageMode = ImageMode.AUTO,
    render_dpi: int | None = None,
) -> list[RenderedImage]:
    """Extract or render image regions from a PDF, page by page.

    For each page that has IMAGE blocks:

    1. **Index** embedded rasters via :func:`_index_page_rasters`.
    2. **Match** rasters to blocks by overlap.
    3. **Extract or render** based on match count and *image_mode*:

       - ``ImageMode.AUTO``: try native extraction for single-raster
         matches; fall back to page rendering.
       - ``ImageMode.SNAP``: always render page regions snapped to
         PDF raster bounds, never extract native raster bytes.
       - ``ImageMode.BBOX``: render AI-based bounding box directly,
         skip raster matching entirely.

    Args:
        doc: An open pymupdf Document.
        rects: Parsed bounding boxes from ``IMAGE_RECT`` markers.
        image_mode: Extraction strategy (default ``AUTO``).
        render_dpi: Explicit DPI override for page-region renders.
            When ``None``, uses the module-level ``_RENDER_DPI``.

    Returns:
        List of :class:`RenderedImage` with image data and filenames.
    """
    if not rects:
        return []

    rendered: list[RenderedImage] = []

    # Group rects by page number.
    page_groups: dict[int, list[tuple[int, ImageRect]]] = {}
    for idx, ir in enumerate(rects):
        page_groups.setdefault(ir.page_num, []).append((idx, ir))

    page_counters: dict[int, int] = {}

    for page_num in sorted(page_groups):
        blocks = page_groups[page_num]
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= len(doc):
            _log.warning(
                "IMAGE_RECT references page %d but PDF has %d pages — skipping",
                page_num, len(doc),
            )
            continue

        page = doc[page_idx]
        clips = [_compute_padded_clip(ir, page) for _, ir in blocks]

        # In BBOX mode, skip raster indexing/matching entirely.
        if image_mode is not ImageMode.BBOX:
            page_rasters = _index_page_rasters(page)
            matches = _match_rasters_to_blocks(page_rasters, clips)
            _log.debug(
                "    page %d: %d raster(s), %d block(s)",
                page_num, len(page_rasters), len(blocks),
            )
        else:
            page_rasters = []
            matches = {i: [] for i in range(len(clips))}
            _log.debug(
                "    page %d: %d block(s) [bbox mode]",
                page_num, len(blocks),
            )

        for i, (idx, ir) in enumerate(blocks):
            clip = clips[i]
            if clip.is_empty or clip.is_infinite:
                _log.warning(
                    "IMAGE_RECT on page %d produced invalid clip rect — skipping",
                    page_num,
                )
                continue

            dpi = _compute_render_dpi(render_dpi)

            # BBOX mode: render raw AI bounding box, skip matching.
            if image_mode is ImageMode.BBOX:
                img_bytes, ext = _render_region(page, clip, dpi)
                _log.debug(
                    "      bbox → render at %d DPI", dpi,
                )

            elif len(matches[i]) == 1:
                matched = matches[i]
                if image_mode is ImageMode.AUTO:
                    # Try native extraction; fall back to rendering
                    # the raster's placement rect.
                    result = _extract_native(doc, matched[0])
                    if result is not None:
                        img_bytes, ext = result
                        _log.debug(
                            "      native extract: xref=%d, format=%s, "
                            "%dx%d px",
                            matched[0].xref, ext,
                            matched[0].width, matched[0].height,
                        )
                    else:
                        snap = matched[0].rect
                        img_bytes, ext = _render_region(page, snap, dpi)
                        _log.debug(
                            "      native fallback → render raster rect "
                            "at %d DPI",
                            dpi,
                        )
                else:
                    # SNAP mode: always render the raster's placement
                    # rect (precise bounds from PDF structure).
                    snap = matched[0].rect
                    img_bytes, ext = _render_region(page, snap, dpi)
                    _log.debug(
                        "      snap raster rect at %d DPI", dpi,
                    )

            elif len(matches[i]) > 1:
                # Composite: union of all matched rasters.
                matched = matches[i]
                union = pymupdf.Rect(matched[0].rect)
                for r in matched[1:]:
                    union |= r.rect
                if union.is_empty or union.is_infinite:
                    union = clip
                img_bytes, ext = _render_region(page, union, dpi)
                _log.debug(
                    "      composite (%d rasters) → render union at %d DPI",
                    len(matched), dpi,
                )

            else:
                # No rasters matched: render Claude's bbox.
                img_bytes, ext = _render_region(page, clip, dpi)
                reason = "no rasters on page" if not page_rasters else "unmatched"
                _log.debug(
                    "      %s → render bbox at %d DPI", reason, dpi,
                )

            img_idx = page_counters.get(page_num, 0)
            page_counters[page_num] = img_idx + 1

            filename = IMAGE_FILENAME_FORMAT.format(
                page=page_num, idx=img_idx + 1, ext=ext,
            )
            rendered.append(RenderedImage(
                page_num=page_num,
                index=img_idx,
                image_bytes=img_bytes,
                filename=filename,
            ))

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
    image_mode: ImageMode = ImageMode.AUTO,
    render_dpi: int | None = None,
) -> str:
    """Parse IMAGE_RECT markers, extract/render images, save, inject refs.

    This is the single entry point called from the pipeline.  Opens the
    PDF once and passes the document to :func:`render_image_rects` for
    page-by-page processing.

    Args:
        pdf_path: Path to the source PDF.
        markdown: Merged markdown containing ``IMAGE_RECT`` markers.
        output_dir: Directory for saving rendered image files.
        image_mode: Extraction strategy (default ``AUTO``).
        render_dpi: Explicit DPI override for page-region renders.

    Returns:
        Updated markdown with ``![caption](path)`` references injected.
    """
    rects = parse_image_rects(markdown)
    if not rects:
        _log.info("  No IMAGE_RECT markers found — skipping image extraction")
        return markdown

    _log.info("  Found %d IMAGE_RECT marker(s), rendering...", len(rects))

    doc = pymupdf.open(str(pdf_path))
    try:
        rendered = render_image_rects(
            doc, rects, image_mode=image_mode, render_dpi=render_dpi,
        )
    finally:
        doc.close()

    if not rendered:
        _log.warning("  All IMAGE_RECT markers failed to render")
        return markdown

    image_map = save_images(rendered, output_dir)
    rel_prefix = output_dir.name

    return inject_image_refs(markdown, image_map, rel_prefix)
