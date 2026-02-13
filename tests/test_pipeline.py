"""Unit tests for the step-based pipeline architecture in pipeline.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pdf2md_claude.pipeline import (
    ConversionPipeline,
    ExtractImagesStep,
    MergeContinuedTablesStep,
    ProcessingContext,
    ProcessingStep,
    StripAIDescriptionsStep,
    ValidateStep,
)
from pdf2md_claude.validator import ValidationResult
from pdf2md_claude.workdir import Manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class RecordingStep:
    """A test step that records its invocation and optionally transforms."""

    label: str
    suffix: str = ""
    """If non-empty, appended to ctx.markdown on each run."""

    calls: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.label

    def run(self, ctx: ProcessingContext) -> None:
        self.calls.append(self.label)
        if self.suffix:
            ctx.markdown += self.suffix


@dataclass
class FailingStep:
    """A test step that raises an exception."""

    @property
    def name(self) -> str:
        return "failing"

    def run(self, ctx: ProcessingContext) -> None:
        raise RuntimeError("step failed")


_DUMMY_PDF = Path("/tmp/dummy.pdf")
_DUMMY_OUTPUT = Path("/tmp/test_output.md")


def _make_ctx(markdown: str = "", pdf_path: Path | None = None) -> ProcessingContext:
    """Create a minimal ProcessingContext for testing."""
    return ProcessingContext(
        markdown=markdown,
        pdf_path=pdf_path,
        output_file=_DUMMY_OUTPUT,
    )


def _make_pipeline(
    steps: list | None = None,
    pdf_path: Path = _DUMMY_PDF,
    output_file: Path = _DUMMY_OUTPUT,
) -> ConversionPipeline:
    """Create a ConversionPipeline with dummy paths for testing."""
    return ConversionPipeline(
        steps=steps or [],
        pdf_path=pdf_path,
        output_file=output_file,
    )


# ---------------------------------------------------------------------------
# ProcessingContext tests
# ---------------------------------------------------------------------------


class TestProcessingContext:
    """Tests for ProcessingContext dataclass."""

    def test_defaults(self):
        ctx = _make_ctx("hello")
        assert ctx.markdown == "hello"
        assert ctx.pdf_path is None
        assert ctx.output_file == Path("/tmp/test_output.md")
        assert isinstance(ctx.validation, ValidationResult)
        assert ctx.validation.ok

    def test_mutable_markdown(self):
        ctx = _make_ctx("before")
        ctx.markdown = "after"
        assert ctx.markdown == "after"

    def test_validation_independent(self):
        """Each context gets its own ValidationResult instance."""
        ctx1 = _make_ctx()
        ctx2 = _make_ctx()
        ctx1.validation.errors.append("err")
        assert ctx2.validation.ok


# ---------------------------------------------------------------------------
# ProcessingStep protocol tests
# ---------------------------------------------------------------------------


class TestProcessingStepProtocol:
    """Tests that the ProcessingStep protocol works with custom classes."""

    def test_recording_step_is_processing_step(self):
        step = RecordingStep(label="test")
        assert isinstance(step, ProcessingStep)

    def test_builtin_steps_are_processing_steps(self):
        assert isinstance(MergeContinuedTablesStep(), ProcessingStep)
        assert isinstance(ExtractImagesStep(), ProcessingStep)
        assert isinstance(ValidateStep(), ProcessingStep)

    def test_step_name(self):
        assert MergeContinuedTablesStep().name == "merge continued tables"
        assert ExtractImagesStep().name == "extract images"
        assert ValidateStep().name == "validate"


# ---------------------------------------------------------------------------
# _merge() tests
# ---------------------------------------------------------------------------


class TestPipelineMerge:
    """Tests for ConversionPipeline._merge()."""

    def test_empty_list(self):
        pipeline = _make_pipeline()
        assert pipeline._merge([]) == ""

    def test_single_chunk(self):
        pipeline = _make_pipeline()
        assert pipeline._merge(["hello world"]) == "hello world"

    def test_single_empty_chunk(self):
        pipeline = _make_pipeline()
        assert pipeline._merge([""]) == ""

    def test_multiple_chunks_with_page_markers(self):
        """Multiple chunks with page markers are merged by page number."""
        pipeline = _make_pipeline()
        chunk1 = "<!-- PDF_PAGE_BEGIN 1 -->\nPage 1 content\n<!-- PDF_PAGE_END 1 -->"
        chunk2 = "<!-- PDF_PAGE_BEGIN 2 -->\nPage 2 content\n<!-- PDF_PAGE_END 2 -->"
        result = pipeline._merge([chunk1, chunk2])
        assert "Page 1 content" in result
        assert "Page 2 content" in result
        # Page 1 should come before page 2.
        assert result.index("Page 1") < result.index("Page 2")

    def test_multiple_chunks_without_markers_fallback(self):
        """Multiple chunks without page markers fall back to simple join."""
        pipeline = _make_pipeline()
        result = pipeline._merge(["chunk A", "chunk B"])
        assert "chunk A" in result
        assert "chunk B" in result


# ---------------------------------------------------------------------------
# _run_steps() tests
# ---------------------------------------------------------------------------


class TestRunSteps:
    """Tests for ConversionPipeline._run_steps()."""

    def test_empty_steps(self):
        pipeline = _make_pipeline()
        ctx = _make_ctx("content")
        pipeline._run_steps(ctx)
        assert ctx.markdown == "content"

    def test_steps_execute_in_order(self):
        step_a = RecordingStep(label="A", suffix="_A")
        step_b = RecordingStep(label="B", suffix="_B")
        pipeline = _make_pipeline(steps=[step_a, step_b])
        ctx = _make_ctx("start")
        pipeline._run_steps(ctx)
        assert ctx.markdown == "start_A_B"
        assert step_a.calls == ["A"]
        assert step_b.calls == ["B"]

    def test_step_can_modify_validation(self):
        @dataclass
        class WarnStep:
            @property
            def name(self) -> str:
                return "warn"

            def run(self, ctx: ProcessingContext) -> None:
                ctx.validation.warnings.append("test warning")

        pipeline = _make_pipeline(steps=[WarnStep()])
        ctx = _make_ctx()
        pipeline._run_steps(ctx)
        assert "test warning" in ctx.validation.warnings

    def test_step_exception_propagates(self):
        pipeline = _make_pipeline(steps=[FailingStep()])
        ctx = _make_ctx()
        with pytest.raises(RuntimeError, match="step failed"):
            pipeline._run_steps(ctx)


# ---------------------------------------------------------------------------
# _write() tests
# ---------------------------------------------------------------------------


class TestWrite:
    """Tests for ConversionPipeline._write()."""

    def test_write_creates_file(self, tmp_path):
        output_file = tmp_path / "output.md"
        ctx = ProcessingContext(
            markdown="# Hello\n\nWorld",
            pdf_path=None,
            output_file=output_file,
        )
        pipeline = _make_pipeline(output_file=output_file)
        pipeline._write(ctx)
        assert output_file.exists()
        assert output_file.read_text(encoding="utf-8") == "# Hello\n\nWorld"

    def test_write_creates_parent_dirs(self, tmp_path):
        output_file = tmp_path / "sub" / "dir" / "output.md"
        ctx = ProcessingContext(
            markdown="content",
            pdf_path=None,
            output_file=output_file,
        )
        pipeline = _make_pipeline(output_file=output_file)
        pipeline._write(ctx)
        assert output_file.exists()
        assert output_file.read_text(encoding="utf-8") == "content"


# ---------------------------------------------------------------------------
# Integration: _process() end-to-end
# ---------------------------------------------------------------------------


class TestProcess:
    """Integration tests for the full _process() flow."""

    def test_process_merges_runs_steps_and_writes(self, tmp_path):
        output_file = tmp_path / "result.md"
        step = RecordingStep(label="transform", suffix="\n## Added by step")
        pipeline = _make_pipeline(steps=[step], output_file=output_file)

        ctx, step_timings = pipeline._process(
            parts=["# Title\n\nSome content"],
        )

        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "# Title" in content
        assert "## Added by step" in content
        assert step.calls == ["transform"]
        assert ctx.markdown == content
        assert "transform" in step_timings
        assert step_timings["transform"] >= 0


# ---------------------------------------------------------------------------
# StripAIDescriptionsStep tests
# ---------------------------------------------------------------------------


class TestStripAIDescriptionsStep:
    """Tests for StripAIDescriptionsStep."""

    def test_is_processing_step(self):
        assert isinstance(StripAIDescriptionsStep(), ProcessingStep)

    def test_step_name(self):
        assert StripAIDescriptionsStep().name == "strip AI descriptions"

    def test_strips_single_description_block(self):
        md = (
            "Real content before.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> AI description of a diagram.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "Real content after."
        )
        ctx = _make_ctx(md)
        StripAIDescriptionsStep().run(ctx)
        assert "AI description" not in ctx.markdown
        assert "Real content before." in ctx.markdown
        assert "Real content after." in ctx.markdown

    def test_strips_multiple_description_blocks(self):
        md = (
            "Intro.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> First AI description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "Middle.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Second AI description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "End."
        )
        ctx = _make_ctx(md)
        StripAIDescriptionsStep().run(ctx)
        assert "First AI description" not in ctx.markdown
        assert "Second AI description" not in ctx.markdown
        assert "Intro." in ctx.markdown
        assert "Middle." in ctx.markdown
        assert "End." in ctx.markdown

    def test_collapses_orphaned_blank_lines(self):
        md = (
            "Before.\n\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> Description.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n\n"
            "After."
        )
        ctx = _make_ctx(md)
        StripAIDescriptionsStep().run(ctx)
        # Should not have more than one blank line between Before/After.
        assert "\n\n\n" not in ctx.markdown
        assert "Before." in ctx.markdown
        assert "After." in ctx.markdown

    def test_no_op_without_descriptions(self):
        md = "# Title\n\nPlain content with no AI descriptions."
        ctx = _make_ctx(md)
        StripAIDescriptionsStep().run(ctx)
        assert ctx.markdown == md

    def test_preserves_image_block_structure(self):
        """IMAGE_BEGIN/END markers and image refs are preserved."""
        md = (
            "<!-- IMAGE_BEGIN -->\n"
            "<!-- IMAGE_RECT 0.1,0.2,0.9,0.8 -->\n"
            "![Figure 1](images/img_p001_01.png)\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_BEGIN -->\n"
            "> AI description of figure 1.\n"
            "<!-- IMAGE_AI_GENERATED_DESCRIPTION_END -->\n"
            "<!-- IMAGE_END -->"
        )
        ctx = _make_ctx(md)
        StripAIDescriptionsStep().run(ctx)
        assert "IMAGE_BEGIN" in ctx.markdown
        assert "IMAGE_END" in ctx.markdown
        assert "IMAGE_RECT" in ctx.markdown
        assert "img_p001_01.png" in ctx.markdown
        assert "AI description" not in ctx.markdown


# ---------------------------------------------------------------------------
# resolve_pages_per_chunk() tests
# ---------------------------------------------------------------------------


def _write_manifest(chunks_dir: Path, pages_per_chunk: int = 20) -> None:
    """Write a minimal manifest.json into a .chunks/ directory."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        pdf_mtime=1707321600.0,
        pdf_size=4096,
        total_pages=40,
        pages_per_chunk=pages_per_chunk,
        max_pages=None,
        model_id="claude-test-1",
        num_chunks=2,
    )
    from dataclasses import asdict
    (chunks_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2) + "\n",
        encoding="utf-8",
    )


class TestResolvePagesPerChunk:
    """Tests for ConversionPipeline.resolve_pages_per_chunk()."""

    def test_no_workdir_returns_requested(self, tmp_path: Path):
        """When no workdir exists, returns the requested value."""
        output_file = tmp_path / "doc.md"
        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        assert pipeline.resolve_pages_per_chunk(10) == 10

    def test_manifest_matches_returns_silently(self, tmp_path: Path):
        """When manifest matches requested value, returns it (no warning)."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.chunks", pages_per_chunk=15)

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        assert pipeline.resolve_pages_per_chunk(15) == 15

    def test_manifest_mismatch_returns_manifest_value(self, tmp_path: Path):
        """When manifest differs, returns the manifest value."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.chunks", pages_per_chunk=20)

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        assert pipeline.resolve_pages_per_chunk(10) == 20

    def test_manifest_mismatch_logs_warning(self, tmp_path: Path, caplog):
        """When manifest differs, a warning is logged."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.chunks", pages_per_chunk=20)

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        import logging
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            pipeline.resolve_pages_per_chunk(10)

        assert any(
            "pages_per_chunk=20" in msg and "requested: 10" in msg
            for msg in caplog.messages
        )

    def test_corrupt_manifest_returns_requested(self, tmp_path: Path):
        """Corrupt manifest is treated as missing; returns requested."""
        output_file = tmp_path / "doc.md"
        chunks_dir = tmp_path / "doc.chunks"
        chunks_dir.mkdir()
        (chunks_dir / "manifest.json").write_text("bad json", encoding="utf-8")

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        assert pipeline.resolve_pages_per_chunk(10) == 10

    def test_force_bypasses_manifest(self, tmp_path: Path):
        """With force=True, returns requested value even if manifest differs."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.chunks", pages_per_chunk=20)

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        assert pipeline.resolve_pages_per_chunk(10, force=True) == 10

    def test_force_no_warning(self, tmp_path: Path, caplog):
        """With force=True, no warning is logged even on mismatch."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.chunks", pages_per_chunk=20)

        pipeline = ConversionPipeline([], _DUMMY_PDF, output_file)
        import logging
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            pipeline.resolve_pages_per_chunk(10, force=True)

        assert not any(
            "pages_per_chunk" in msg for msg in caplog.messages
        )
