"""Unit tests for the step-based pipeline architecture in pipeline.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pdf2md_claude.pipeline import (
    ConversionPipeline,
    ExtractImagesStep,
    MergeContinuedTablesStep,
    ProcessingContext,
    ProcessingStep,
    ValidateStep,
)
from pdf2md_claude.validator import ValidationResult


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


def _make_ctx(markdown: str = "", pdf_path: Path | None = None) -> ProcessingContext:
    """Create a minimal ProcessingContext for testing."""
    return ProcessingContext(
        markdown=markdown,
        pdf_path=pdf_path,
        output_file=Path("/tmp/test_output.md"),
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
        pipeline = ConversionPipeline(steps=[])
        assert pipeline._merge([]) == ""

    def test_single_chunk(self):
        pipeline = ConversionPipeline(steps=[])
        assert pipeline._merge(["hello world"]) == "hello world"

    def test_single_empty_chunk(self):
        pipeline = ConversionPipeline(steps=[])
        assert pipeline._merge([""]) == ""

    def test_multiple_chunks_with_page_markers(self):
        """Multiple chunks with page markers are merged by page number."""
        pipeline = ConversionPipeline(steps=[])
        chunk1 = "<!-- PDF_PAGE_BEGIN 1 -->\nPage 1 content\n<!-- PDF_PAGE_END 1 -->"
        chunk2 = "<!-- PDF_PAGE_BEGIN 2 -->\nPage 2 content\n<!-- PDF_PAGE_END 2 -->"
        result = pipeline._merge([chunk1, chunk2])
        assert "Page 1 content" in result
        assert "Page 2 content" in result
        # Page 1 should come before page 2.
        assert result.index("Page 1") < result.index("Page 2")

    def test_multiple_chunks_without_markers_fallback(self):
        """Multiple chunks without page markers fall back to simple join."""
        pipeline = ConversionPipeline(steps=[])
        result = pipeline._merge(["chunk A", "chunk B"])
        assert "chunk A" in result
        assert "chunk B" in result


# ---------------------------------------------------------------------------
# _run_steps() tests
# ---------------------------------------------------------------------------


class TestRunSteps:
    """Tests for ConversionPipeline._run_steps()."""

    def test_empty_steps(self):
        pipeline = ConversionPipeline(steps=[])
        ctx = _make_ctx("content")
        pipeline._run_steps(ctx)
        assert ctx.markdown == "content"

    def test_steps_execute_in_order(self):
        step_a = RecordingStep(label="A", suffix="_A")
        step_b = RecordingStep(label="B", suffix="_B")
        pipeline = ConversionPipeline(steps=[step_a, step_b])
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

        pipeline = ConversionPipeline(steps=[WarnStep()])
        ctx = _make_ctx()
        pipeline._run_steps(ctx)
        assert "test warning" in ctx.validation.warnings

    def test_step_exception_propagates(self):
        pipeline = ConversionPipeline(steps=[FailingStep()])
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
        pipeline = ConversionPipeline(steps=[])
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
        pipeline = ConversionPipeline(steps=[])
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
        pipeline = ConversionPipeline(steps=[step])

        ctx = pipeline._process(
            parts=["# Title\n\nSome content"],
            pdf_path=None,
            output_file=output_file,
        )

        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "# Title" in content
        assert "## Added by step" in content
        assert step.calls == ["transform"]
        assert ctx.markdown == content
