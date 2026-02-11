"""Single-document conversion pipeline.

Orchestrates the full flow for one PDF: work directory management,
chunked conversion, deterministic merge, validation, and output writing.

Also provides :meth:`ConversionPipeline.remerge` for re-running merge +
validate + write from cached chunks without any API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pdf2md_claude.converter import ConversionResult, PdfConverter
from pdf2md_claude.images import ImageExtractor, ImageMode
from pdf2md_claude.merger import merge_chunks, merge_continued_tables
from pdf2md_claude.models import DocumentUsageStats
from pdf2md_claude.validator import ValidationResult, check_page_fidelity, validate_output
from pdf2md_claude.workdir import WorkDir

_log = logging.getLogger("pipeline")

_IMAGE_DIR_SUFFIX = ".images"
"""Suffix appended to the output stem for the extracted-images directory."""


@dataclass
class PipelineResult:
    """Result of the full single-document conversion pipeline.

    Returned by :meth:`ConversionPipeline.convert` for the CLI to consume.
    Does **not** keep the merged markdown in memory (it is on disk).
    """

    stats: DocumentUsageStats
    output_file: Path
    validation: ValidationResult
    cached_chunks: int
    fresh_chunks: int


# ---------------------------------------------------------------------------
# ConversionPipeline class
# ---------------------------------------------------------------------------


class ConversionPipeline:
    """Orchestrates the full single-document conversion pipeline.

    Holds image-related configuration so it does not need to be threaded
    through every call.  Provides :meth:`convert` (full API-based
    conversion) and :meth:`remerge` (re-merge from cached chunks, no
    API calls).

    Usage::

        pipeline = ConversionPipeline(extract_images=True, image_mode=ImageMode.AUTO)
        result = pipeline.convert(converter, pdf_path, output_file, pages_per_chunk=10)
    """

    def __init__(
        self,
        extract_images: bool = True,
        image_mode: ImageMode = ImageMode.AUTO,
        image_dpi: int | None = None,
    ) -> None:
        self._extract_images = extract_images
        self._image_mode = image_mode
        self._image_dpi = image_dpi

    # -- public API --------------------------------------------------------

    def convert(
        self,
        converter: PdfConverter,
        pdf_path: Path,
        output_file: Path,
        pages_per_chunk: int,
        max_pages: int | None = None,
        force: bool = False,
    ) -> PipelineResult:
        """Run the full conversion pipeline for a single PDF.

        Steps:

        1. Create ``WorkDir`` from ``output_file.with_suffix(".chunks")``.
        2. If ``force``: invalidate all cached chunks.
        3. Convert via :meth:`PdfConverter.convert` (chunked, with disk resume).
        4. Merge chunks, extract images, validate, write (via
           :meth:`_post_process`).

        Args:
            converter: Configured PDF converter.
            pdf_path: Path to the source PDF.
            output_file: Path for the output Markdown file.
            pages_per_chunk: Pages per conversion chunk.
            max_pages: Optional page cap for debugging.
            force: If True, discard cached chunks and reconvert.

        Returns:
            :class:`PipelineResult` with stats, validation, and output path.
        """
        # 1. Create work directory.
        work_dir = WorkDir(output_file.with_suffix(".chunks"))

        # 2. Force invalidation if requested.
        if force:
            work_dir.invalidate()

        # 3. Convert (chunked, with disk resume).
        result: ConversionResult = converter.convert(
            pdf_path, work_dir, pages_per_chunk, max_pages=max_pages,
        )

        # 4. Merge chunks.
        parts = [cr.markdown for cr in result.chunks]
        if len(parts) > 1:
            markdown = merge_chunks(parts)
        else:
            markdown = parts[0] if parts else ""

        # 5. Post-process: merge tables, extract images, validate, write.
        markdown, validation = self._post_process(
            markdown, pdf_path, output_file,
        )

        return PipelineResult(
            stats=result.stats,
            output_file=output_file,
            validation=validation,
            cached_chunks=result.cached_chunks,
            fresh_chunks=result.fresh_chunks,
        )

    def remerge(
        self,
        output_file: Path,
        pdf_path: Path | None = None,
    ) -> PipelineResult:
        """Re-run merge + validate + write from cached chunks on disk.

        No API calls are made.  Useful for iterating on merge/post-processing
        logic after a conversion has already populated the ``.chunks/`` dir.

        Args:
            output_file: Path for the output Markdown file.  The ``.chunks/``
                directory is derived from this path.
            pdf_path: Optional path to the source PDF.  When provided, enables
                per-page fidelity checking and image extraction.

        Returns:
            :class:`PipelineResult` with validation results and output path.

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

        # 4. Post-process: merge tables, extract images, validate, write.
        markdown, validation = self._post_process(
            markdown, pdf_path, output_file,
        )

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

    # -- internal methods --------------------------------------------------

    def _post_process(
        self,
        markdown: str,
        pdf_path: Path | None,
        output_file: Path,
    ) -> tuple[str, ValidationResult]:
        """Shared post-processing: merge tables, extract images, validate, write.

        This method deduplicates the logic shared between :meth:`convert`
        and :meth:`remerge`.

        Args:
            markdown: Merged markdown from chunks.
            pdf_path: Path to the source PDF (``None`` disables image
                extraction and page fidelity checks).
            output_file: Path for the output Markdown file.

        Returns:
            Tuple of (final_markdown, validation_result).
        """
        # Merge continued tables (deterministic, no LLM).
        markdown = merge_continued_tables(markdown)

        # Extract and inject images from IMAGE_RECT markers.
        if self._extract_images and pdf_path is not None:
            images_dir = output_file.with_name(
                output_file.stem + _IMAGE_DIR_SUFFIX,
            )
            extractor = ImageExtractor(
                pdf_path, images_dir,
                image_mode=self._image_mode,
                render_dpi=self._image_dpi,
            )
            markdown = extractor.extract_and_inject(markdown)

        # Validate output.
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

        # Write output file.
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(markdown, encoding="utf-8")
        _log.info("  Saved: %s (%d lines)", output_file, markdown.count("\n") + 1)

        return markdown, validation
