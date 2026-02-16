"""Single-document conversion pipeline.

Orchestrates the full flow for one PDF: work directory management,
chunked conversion, deterministic merge, validation, and output writing.

Also provides :func:`remerge_document` for re-running merge + validate +
write from cached chunks without any API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

from pdf2md_claude.converter import ConversionResult, convert_pdf
from pdf2md_claude.images import ImageMode, extract_and_inject_images
from pdf2md_claude.merger import merge_chunks, merge_continued_tables
from pdf2md_claude.models import DocumentUsageStats, ModelConfig
from pdf2md_claude.validator import ValidationResult, check_page_fidelity, validate_output
from pdf2md_claude.workdir import WorkDir

_log = logging.getLogger("pipeline")

_IMAGE_DIR_SUFFIX = ".images"
"""Suffix appended to the output stem for the extracted-images directory."""


@dataclass
class PipelineResult:
    """Result of the full single-document conversion pipeline.

    Returned by :func:`convert_document` for the CLI to consume.
    Does **not** keep the merged markdown in memory (it is on disk).
    """

    stats: DocumentUsageStats
    output_file: Path
    validation: ValidationResult
    cached_chunks: int
    fresh_chunks: int


def convert_document(
    client: anthropic.Anthropic,
    model: ModelConfig,
    pdf_path: Path,
    output_file: Path,
    pages_per_chunk: int,
    max_pages: int | None = None,
    use_cache: bool = False,
    force: bool = False,
    extract_images: bool = True,
    image_mode: ImageMode = ImageMode.AUTO,
    image_dpi: int | None = None,
    system_prompt: str | None = None,
) -> PipelineResult:
    """Run the full conversion pipeline for a single PDF.

    Steps:

    1. Create ``WorkDir`` from ``output_file.with_suffix(".chunks")``.
    2. If ``force``: invalidate all cached chunks.
    3. Convert via :func:`convert_pdf` (chunked, with disk resume).
    4. Merge chunks by page markers (deterministic, no LLM).
    4b. Merge continued tables (deterministic, no LLM).
    4c. Extract and inject images from ``IMAGE_RECT`` markers.
    5. Validate the merged output.
    6. Write the final ``.md`` file.
    7. Return :class:`PipelineResult`.

    Args:
        client: Configured Anthropic client.
        model: Model configuration.
        pdf_path: Path to the source PDF.
        output_file: Path for the output Markdown file.
        pages_per_chunk: Pages per conversion chunk.
        max_pages: Optional page cap for debugging.
        use_cache: Enable prompt caching (1h TTL).
        force: If True, discard cached chunks and reconvert.
        extract_images: If True, render IMAGE_RECT regions and inject refs.
        system_prompt: Optional override for the built-in system prompt.
            When ``None`` (default), uses ``SYSTEM_PROMPT``.

    Returns:
        :class:`PipelineResult` with stats, validation, and output path.
    """
    # 1. Create work directory.
    work_dir = WorkDir(output_file.with_suffix(".chunks"))

    # 2. Force invalidation if requested.
    if force:
        work_dir.invalidate()

    # 3. Convert (chunked, with disk resume).
    result: ConversionResult = convert_pdf(
        client, model, pdf_path, work_dir,
        max_pages=max_pages,
        use_cache=use_cache,
        pages_per_chunk=pages_per_chunk,
        system_prompt=system_prompt,
    )

    # 4. Merge chunks by page markers (deterministic, no LLM).
    parts = [cr.markdown for cr in result.chunks]
    if len(parts) > 1:
        markdown = merge_chunks(parts)
    else:
        markdown = parts[0] if parts else ""

    # 4b. Merge continued tables (deterministic, no LLM).
    markdown = merge_continued_tables(markdown)

    # 4c. Extract and inject images from IMAGE_RECT markers.
    if extract_images:
        images_dir = output_file.with_name(output_file.stem + _IMAGE_DIR_SUFFIX)
        markdown = extract_and_inject_images(
            pdf_path, markdown, images_dir,
            image_mode=image_mode, render_dpi=image_dpi,
        )

    # 5. Validate output.
    _log.info("  Validating output...")
    validation = validate_output(markdown)
    check_page_fidelity(pdf_path, markdown, validation)
    validation.log_all()
    if not validation.ok:
        _log.warning(
            "  ⚠ Validation found %d error(s), %d warning(s)",
            len(validation.errors), len(validation.warnings),
        )
    elif validation.info:
        _log.info(
            "  Validation: %d info message(s)", len(validation.info),
        )

    # 6. Write output file.
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(markdown, encoding="utf-8")
    _log.info("  Saved: %s (%d lines)", output_file, markdown.count("\n") + 1)

    # 7. Return result (no markdown kept in memory).
    return PipelineResult(
        stats=result.stats,
        output_file=output_file,
        validation=validation,
        cached_chunks=result.cached_chunks,
        fresh_chunks=result.fresh_chunks,
    )


def remerge_document(
    output_file: Path,
    pdf_path: Path | None = None,
    extract_images: bool = True,
    image_mode: ImageMode = ImageMode.AUTO,
    image_dpi: int | None = None,
) -> PipelineResult:
    """Re-run merge + validate + write from cached chunks on disk.

    No API calls are made.  Useful for iterating on merge/post-processing
    logic after a conversion has already populated the ``.chunks/`` dir.

    Steps:

    1. Open ``WorkDir`` and verify cached chunks exist.
    2. Load all chunk markdown from disk.
    3. Merge chunks by page markers.
    4. Merge continued tables.
    4b. Extract and inject images from ``IMAGE_RECT`` markers (if enabled).
    5. Validate the merged output (including page fidelity if ``pdf_path``
       is provided).
    6. Write the final ``.md`` file.

    Args:
        output_file: Path for the output Markdown file.  The ``.chunks/``
            directory is derived from this path.
        pdf_path: Optional path to the source PDF.  When provided, enables
            per-page fidelity checking (cross-referencing markdown content
            against PDF raw text to detect fabrication) and image extraction.
        extract_images: If True and *pdf_path* is provided, render
            ``IMAGE_RECT`` regions and inject image references.

    Returns:
        :class:`PipelineResult` with validation results and output path.
        Usage stats are loaded from the cached ``stats.json`` if available.

    Raises:
        RuntimeError: If the ``.chunks/`` directory or manifest is missing.
    """
    work_dir = WorkDir(output_file.with_suffix(".chunks"))

    if not work_dir.path.exists():
        raise RuntimeError(
            f"Chunks directory not found: {work_dir.path}\n"
            f"Run a full conversion first before using --remerge."
        )

    # 1. Discover chunk count from manifest.
    num_chunks = work_dir.chunk_count()
    _log.info("  Re-merging from %d cached chunks...", num_chunks)

    # 2. Verify all chunks exist and load markdown.
    missing = [i for i in range(num_chunks) if not work_dir.has_chunk(i)]
    if missing:
        raise RuntimeError(
            f"Missing chunks: {', '.join(str(i + 1) for i in missing)}. "
            f"Run a full conversion first (without --remerge) to generate them."
        )

    parts = [work_dir.load_chunk_markdown(i) for i in range(num_chunks)]

    # 3. Merge chunks by page markers.
    if len(parts) > 1:
        markdown = merge_chunks(parts)
    else:
        markdown = parts[0] if parts else ""

    # 4. Merge continued tables.
    markdown = merge_continued_tables(markdown)

    # 4b. Extract and inject images from IMAGE_RECT markers.
    if extract_images and pdf_path is not None:
        images_dir = output_file.with_name(output_file.stem + _IMAGE_DIR_SUFFIX)
        markdown = extract_and_inject_images(
            pdf_path, markdown, images_dir,
            image_mode=image_mode, render_dpi=image_dpi,
        )

    # 5. Validate output.
    _log.info("  Validating output...")
    validation = validate_output(markdown)
    if pdf_path is not None:
        check_page_fidelity(pdf_path, markdown, validation)
    validation.log_all()
    if not validation.ok:
        _log.warning(
            "  ⚠ Validation found %d error(s), %d warning(s)",
            len(validation.errors), len(validation.warnings),
        )
    elif validation.info:
        _log.info(
            "  Validation: %d info message(s)", len(validation.info),
        )

    # 6. Write output file.
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(markdown, encoding="utf-8")
    _log.info("  Saved: %s (%d lines)", output_file, markdown.count("\n") + 1)

    # Load stats from cache if available (for display purposes).
    stats = work_dir.load_stats()
    if stats is None:
        # Minimal stats when stats.json is missing.
        stats = DocumentUsageStats(
            doc_name=output_file.stem,
            pages=0, chunks=num_chunks,
            input_tokens=0, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost=0.0, elapsed_seconds=0.0,
        )

    return PipelineResult(
        stats=stats,
        output_file=output_file,
        validation=validation,
        cached_chunks=num_chunks,
        fresh_chunks=0,
    )
