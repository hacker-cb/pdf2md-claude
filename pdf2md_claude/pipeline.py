"""Single-document conversion pipeline.

Orchestrates the full flow for one PDF: work directory management,
chunked conversion, deterministic merge, pluggable processing steps,
and output writing.

The pipeline uses a step-based architecture: after merging chunks,
a configurable list of :class:`ProcessingStep` objects is executed
in order.  Each step receives a shared :class:`ProcessingContext`
and can modify the markdown content and/or append validation messages.

Built-in steps:

- :class:`MergeContinuedTablesStep` — merges split tables.
- :class:`ExtractImagesStep` — renders and injects images.
- :class:`StripAIDescriptionsStep` — removes AI-generated image descriptions.
- :class:`FormatMarkdownStep` — prettifies HTML tables and normalizes spacing.
- :class:`ValidateStep` — runs quality checks.

Also provides :meth:`ConversionPipeline.remerge` for re-running merge +
steps + write from cached chunks without any API calls.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md_claude.converter import ConversionResult, PdfConverter
from pdf2md_claude.formatter import FormatMarkdownStep
from pdf2md_claude.images import ImageExtractor, ImageMode
from pdf2md_claude.markers import IMAGE_AI_DESCRIPTION_BLOCK_RE
from pdf2md_claude.merger import merge_chunks, merge_continued_tables
from pdf2md_claude.models import DocumentUsageStats
from pdf2md_claude.validator import ValidationResult, check_page_fidelity, validate_output
from pdf2md_claude.workdir import WorkDir

_log = logging.getLogger("pipeline")

_IMAGE_DIR_SUFFIX = ".images"
"""Suffix appended to the output stem for the extracted-images directory."""


# ---------------------------------------------------------------------------
# Pipeline-level helpers (no API context needed)
# ---------------------------------------------------------------------------


def resolve_output(pdf_path: Path, output_dir: Path | None) -> Path:
    """Resolve output file path for a given PDF.

    Default: Markdown file is placed next to the source PDF.
    With *output_dir*: all output goes to the specified directory.
    """
    base = output_dir if output_dir else pdf_path.parent
    return base / f"{pdf_path.stem}.md"


# ---------------------------------------------------------------------------
# Processing context and step protocol
# ---------------------------------------------------------------------------


@dataclass
class ProcessingContext:
    """Shared mutable state passed through all processing steps.

    Steps may modify :attr:`markdown` (content transforms) and/or
    append to :attr:`validation` (quality checks).  The context is
    created once per document and flows through all steps in order.

    Extensible: add fields here when new steps need shared state
    (e.g. ``client`` for AI-based steps, ``metadata`` for inter-step
    communication).
    """

    markdown: str
    """Current markdown content (mutable — steps may replace it)."""

    pdf_path: Path | None
    """Path to the source PDF (``None`` when unavailable)."""

    output_file: Path
    """Target path for the output Markdown file."""

    validation: ValidationResult = field(default_factory=ValidationResult)
    """Accumulated validation errors, warnings, and info messages."""


@runtime_checkable
class ProcessingStep(Protocol):
    """Protocol for a single processing step in the pipeline.

    Any class with a :attr:`name` property and a :meth:`run` method
    that accepts a :class:`ProcessingContext` qualifies.

    Steps may:

    - Modify ``ctx.markdown`` (content transforms).
    - Append to ``ctx.validation`` (quality checks).
    - Perform side effects (e.g. write image files to disk).
    """

    @property
    def name(self) -> str:
        """Human-readable step name for logging."""
        ...

    def run(self, ctx: ProcessingContext) -> None:
        """Execute this processing step."""
        ...


# ---------------------------------------------------------------------------
# Built-in steps
# ---------------------------------------------------------------------------


@dataclass
class MergeContinuedTablesStep:
    """Merge continuation tables into their preceding tables.

    Wraps :func:`~pdf2md_claude.merger.merge_continued_tables`.
    Detects ``TABLE_CONTINUE`` markers and splices continuation
    ``<tbody>`` rows into the preceding table.
    """

    @property
    def name(self) -> str:
        return "merge continued tables"

    def run(self, ctx: ProcessingContext) -> None:
        ctx.markdown = merge_continued_tables(ctx.markdown)


@dataclass
class ExtractImagesStep:
    """Extract and inject images from ``IMAGE_RECT`` markers.

    Wraps :class:`~pdf2md_claude.images.ImageExtractor`.  Renders
    bounding-box regions from the source PDF and injects
    ``![caption](path)`` references into the markdown.

    Skipped when ``ctx.pdf_path`` is ``None``.
    """

    image_mode: ImageMode = ImageMode.AUTO
    render_dpi: int | None = None

    @property
    def name(self) -> str:
        return "extract images"

    def run(self, ctx: ProcessingContext) -> None:
        if ctx.pdf_path is None:
            return
        images_dir = ctx.output_file.with_name(
            ctx.output_file.stem + _IMAGE_DIR_SUFFIX,
        )
        extractor = ImageExtractor(
            ctx.pdf_path, images_dir,
            image_mode=self.image_mode,
            render_dpi=self.render_dpi,
        )
        ctx.markdown = extractor.extract_and_inject(ctx.markdown)


_CONSECUTIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")
"""Regex matching 3+ consecutive newlines (used to collapse blanks after stripping)."""


@dataclass
class StripAIDescriptionsStep:
    """Strip AI-generated image description blocks from the markdown.

    Removes content between ``IMAGE_AI_GENERATED_DESCRIPTION_BEGIN``
    and ``IMAGE_AI_GENERATED_DESCRIPTION_END`` markers (inclusive).
    Collapses any orphaned blank lines left by the removal.
    """

    @property
    def name(self) -> str:
        return "strip AI descriptions"

    def run(self, ctx: ProcessingContext) -> None:
        ctx.markdown = IMAGE_AI_DESCRIPTION_BLOCK_RE.sub("", ctx.markdown)
        ctx.markdown = _CONSECUTIVE_BLANK_LINES_RE.sub("\n\n", ctx.markdown)


@dataclass
class ValidateStep:
    """Run validation checks on the converted markdown.

    Wraps :func:`~pdf2md_claude.validator.validate_output` and
    :func:`~pdf2md_claude.validator.check_page_fidelity`.  Populates
    ``ctx.validation`` and logs the results.
    """

    @property
    def name(self) -> str:
        return "validate"

    def run(self, ctx: ProcessingContext) -> None:
        ctx.validation = validate_output(ctx.markdown)
        if ctx.pdf_path is not None:
            check_page_fidelity(ctx.pdf_path, ctx.markdown, ctx.validation)
        ctx.validation.log_all()
        if not ctx.validation.ok:
            _log.warning(
                "  ⚠ Validation found %d error(s), %d warning(s)",
                len(ctx.validation.errors), len(ctx.validation.warnings),
            )
        elif ctx.validation.info:
            _log.info(
                "  Validation: %d info message(s)", len(ctx.validation.info),
            )


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Result of the full single-document conversion pipeline.

    Returned by :meth:`ConversionPipeline.convert` and
    :meth:`ConversionPipeline.remerge` for the CLI to consume.
    Does **not** keep the merged markdown in memory (it is on disk).
    """

    stats: DocumentUsageStats
    output_file: Path
    validation: ValidationResult
    cached_chunks: int
    fresh_chunks: int
    step_timings: dict[str, float] = field(default_factory=dict)
    """Per-step execution time in seconds (step name -> elapsed)."""


# ---------------------------------------------------------------------------
# ConversionPipeline class
# ---------------------------------------------------------------------------


class ConversionPipeline:
    """Orchestrates the full single-document conversion pipeline.

    Created per document with the source PDF path and target output file.
    Holds an ordered list of :class:`ProcessingStep` objects that are
    executed after chunk merging.  Provides :meth:`convert` (full
    API-based conversion) and :meth:`remerge` (re-merge from cached
    chunks, no API calls).

    Usage::

        steps = [
            MergeContinuedTablesStep(),
            ExtractImagesStep(image_mode=ImageMode.AUTO, render_dpi=600),
            ValidateStep(),
        ]
        pipeline = ConversionPipeline(steps, pdf_path, output_file)
        result = pipeline.convert(converter, pages_per_chunk=10)
    """

    def __init__(
        self,
        steps: list[ProcessingStep],
        pdf_path: Path,
        output_file: Path,
    ) -> None:
        self._steps = steps
        self._pdf_path = pdf_path
        self._output_file = output_file
        self._work_dir = WorkDir(output_file.with_suffix(".staging"))

    # -- public API --------------------------------------------------------

    def resolve_pages_per_chunk(
        self,
        requested: int,
        force: bool = False,
    ) -> int:
        """Resolve effective ``pages_per_chunk`` from an existing workdir manifest.

        If a manifest exists (and *force* is ``False``), always uses its
        ``pages_per_chunk`` so that cached chunks remain valid.  Logs a
        warning when the requested value differs from the manifest.

        When no manifest exists (new job) or *force* is ``True``,
        returns *requested* unchanged.

        Args:
            requested: The ``pages_per_chunk`` value from CLI args
                (explicit or default).
            force: When ``True``, skip manifest lookup and use
                *requested* as-is (the user wants a fresh start).

        Returns:
            Effective ``pages_per_chunk`` to use for conversion.
        """
        if force:
            return requested
        manifest = self._work_dir.load_manifest()
        if manifest is None:
            return requested
        if manifest.pages_per_chunk != requested:
            _log.warning(
                "  Using pages_per_chunk=%d from existing workdir "
                "(requested: %d). Use --force to override.",
                manifest.pages_per_chunk, requested,
            )
        return manifest.pages_per_chunk

    def load_cached_stats(self) -> DocumentUsageStats | None:
        """Load previously saved usage stats from the work directory.

        Returns:
            ``DocumentUsageStats`` if ``stats.json`` exists and is valid,
            ``None`` otherwise.
        """
        if not self._work_dir.path.exists():
            return None
        return self._work_dir.load_stats()

    def needs_conversion(
        self,
        force: bool = False,
        model_id: str | None = None,
    ) -> bool:
        """Check if the PDF needs to be converted.

        Args:
            force: If True, always reconvert.
            model_id: If provided, also check the cached manifest for model
                staleness.  When the output file exists but was produced by a
                different model, return ``True`` (needs reconversion).
        """
        if force or not self._output_file.exists():
            return True
        # Output exists -- check manifest for model staleness.
        if model_id is not None:
            manifest = self._work_dir.load_manifest()
            if manifest is not None and manifest.model_id != model_id:
                return True
            # Missing/corrupt manifest: output file exists, no reason
            # to force reconversion (user may have deleted .staging/).
        return False

    def convert(
        self,
        converter: PdfConverter,
        pages_per_chunk: int,
        max_pages: int | None = None,
        force: bool = False,
    ) -> PipelineResult:
        """Run the full conversion pipeline for a single PDF.

        Steps:

        1. Create ``WorkDir`` from the stored output path.
        2. If ``force``: invalidate all cached chunks.
        3. Convert via :meth:`PdfConverter.convert` (chunked, with disk resume).
        4. Merge chunks by page markers.
        5. Run all processing steps (transforms, validation).
        6. Write output file.

        Args:
            converter: Configured PDF converter.
            pages_per_chunk: Pages per conversion chunk.
            max_pages: Optional page cap for debugging.
            force: If True, discard cached chunks and reconvert.

        Returns:
            :class:`PipelineResult` with stats, validation, and output path.
        """
        # 1. Force invalidation if requested.
        if force:
            self._work_dir.invalidate()

        # 2. Convert (chunked, with disk resume).
        result: ConversionResult = converter.convert(
            self._pdf_path, self._work_dir, pages_per_chunk, max_pages=max_pages,
        )

        # 3–5. Merge, run steps, write.
        parts = [cr.markdown for cr in result.chunks]
        ctx, step_timings = self._process(parts)

        return PipelineResult(
            stats=result.stats,
            output_file=self._output_file,
            validation=ctx.validation,
            cached_chunks=result.cached_chunks,
            fresh_chunks=result.fresh_chunks,
            step_timings=step_timings,
        )

    def remerge(self) -> PipelineResult:
        """Re-run merge + steps + write from cached chunks on disk.

        No API calls are made.  Useful for iterating on merge/post-processing
        logic after a conversion has already populated the ``.staging/`` dir.

        Returns:
            :class:`PipelineResult` with validation results and output path.

        Raises:
            RuntimeError: If the ``.staging/`` directory or manifest is missing.
        """
        if not self._work_dir.path.exists():
            raise RuntimeError(
                f"Staging directory not found: {self._work_dir.path}\n"
                f"Run a full conversion first before using --remerge."
            )

        # 1. Discover chunk count and total pages from manifest.
        num_chunks = self._work_dir.chunk_count()
        total_pages = self._work_dir.total_pages()
        _log.info(
            "  Re-merging from %d cached chunks (%d pages)...",
            num_chunks, total_pages,
        )

        # 2. Verify all chunks exist and load markdown.
        missing = [i for i in range(num_chunks) if not self._work_dir.has_chunk(i)]
        if missing:
            raise RuntimeError(
                f"Missing chunks: {', '.join(str(i + 1) for i in missing)}. "
                f"Run a full conversion first (without --remerge) to generate them."
            )

        parts = [self._work_dir.load_chunk_markdown(i) for i in range(num_chunks)]

        # 3–5. Merge, run steps, write.
        ctx, step_timings = self._process(parts)

        # Load stats from cache if available (for display purposes).
        stats = self._work_dir.load_stats()
        if stats is None:
            # Minimal stats when stats.json is missing.
            stats = DocumentUsageStats(
                doc_name=self._output_file.stem,
                pages=0, chunks=num_chunks,
                input_tokens=0, output_tokens=0,
                cache_creation_tokens=0, cache_read_tokens=0,
                cost=0.0, elapsed_seconds=0.0,
            )

        return PipelineResult(
            stats=stats,
            output_file=self._output_file,
            validation=ctx.validation,
            cached_chunks=num_chunks,
            fresh_chunks=0,
            step_timings=step_timings,
        )

    # -- internal methods --------------------------------------------------

    def _merge(self, parts: list[str]) -> str:
        """Merge chunk markdown parts into a single document.

        With a single chunk (or none), returns the content directly.
        With multiple chunks, delegates to
        :func:`~pdf2md_claude.merger.merge_chunks` for page-marker
        based concatenation.
        """
        if len(parts) > 1:
            return merge_chunks(parts)
        return parts[0] if parts else ""

    def _run_steps(self, ctx: ProcessingContext) -> dict[str, float]:
        """Execute all processing steps in order.

        Returns:
            Dict mapping step name to elapsed time in seconds.
        """
        timings: dict[str, float] = {}
        for step in self._steps:
            _log.info("  Step: %s...", step.name)
            t0 = time.monotonic()
            step.run(ctx)
            elapsed = time.monotonic() - t0
            timings[step.name] = elapsed
            _log.debug("  Step: %s done (%.2fs)", step.name, elapsed)
        return timings

    def _write(self, ctx: ProcessingContext) -> None:
        """Write the final markdown to disk."""
        ctx.output_file.parent.mkdir(parents=True, exist_ok=True)
        ctx.output_file.write_text(ctx.markdown, encoding="utf-8")
        _log.info(
            "  Saved: %s (%d lines)",
            ctx.output_file, ctx.markdown.count("\n") + 1,
        )

    def _process(
        self,
        parts: list[str],
    ) -> tuple[ProcessingContext, dict[str, float]]:
        """Merge chunks, run all steps, and write the output file.

        Shared logic between :meth:`convert` and :meth:`remerge`.

        Args:
            parts: List of markdown strings from chunks.

        Returns:
            Tuple of (context after all steps, per-step timings dict).
        """
        markdown = self._merge(parts)
        self._work_dir.save_output(markdown)
        ctx = ProcessingContext(
            markdown=markdown,
            pdf_path=self._pdf_path,
            output_file=self._output_file,
        )
        step_timings = self._run_steps(ctx)
        self._write(ctx)
        return ctx, step_timings
