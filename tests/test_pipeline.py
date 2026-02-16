"""Unit tests for the step-based pipeline architecture in pipeline.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pdf2md_claude.formatter import FormatMarkdownStep
from pdf2md_claude.models import MODELS
from pdf2md_claude.pipeline import (
    ConversionPipeline,
    ExtractImagesStep,
    MergeContinuedTablesStep,
    ProcessingContext,
    ProcessingStep,
    StripAIDescriptionsStep,
    ValidateStep,
)
from pdf2md_claude.table_fixer import FixTablesStep
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

    @property
    def key(self) -> str:
        return self.label.lower().replace(" ", "-")

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

    @property
    def key(self) -> str:
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
    """Create a ConversionPipeline with dummy paths for testing.
    
    If steps are provided, they override the default step chain by directly
    setting pipeline._steps after construction.
    """
    pipeline = ConversionPipeline(
        pdf_path,
        output_file,
        api_key="test-key",
        model=MODELS["sonnet"],
    )
    if steps is not None:
        pipeline._steps = steps
    return pipeline


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
        assert ctx.api is None
        assert ctx.work_dir is None
        assert ctx.table_fix_stats is None
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
        ctx1.validation.errors.append(("test", "err"))
        assert ctx2.validation.ok

    def test_api_defaults_to_none(self):
        """The api field should default to None."""
        ctx = _make_ctx()
        assert ctx.api is None


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
        assert isinstance(FixTablesStep(), ProcessingStep)
        assert isinstance(ExtractImagesStep(), ProcessingStep)
        assert isinstance(ValidateStep(), ProcessingStep)

    def test_step_name(self):
        assert MergeContinuedTablesStep().name == "merge continued tables"
        assert FixTablesStep().name == "fix tables"
        assert ExtractImagesStep().name == "extract images"
        assert StripAIDescriptionsStep().name == "strip AI descriptions"
        assert FormatMarkdownStep().name == "format markdown"
        assert ValidateStep().name == "validate"

    def test_builtin_steps_have_key_property(self):
        """All built-in steps must have a key property."""
        steps = [
            MergeContinuedTablesStep(),
            FixTablesStep(),
            ExtractImagesStep(),
            StripAIDescriptionsStep(),
            FormatMarkdownStep(),
            ValidateStep(),
        ]
        for step in steps:
            assert hasattr(step, "key"), f"{step.name} missing key property"
            assert isinstance(step.key, str), f"{step.name}.key must be str"
            assert step.key, f"{step.name}.key must be non-empty"

    def test_builtin_step_keys_are_stable(self):
        """Verify specific key values for built-in steps."""
        assert MergeContinuedTablesStep().key == "tables"
        assert FixTablesStep().key == "fix-tables"
        assert ExtractImagesStep().key == "images"
        assert StripAIDescriptionsStep().key == "strip-ai"
        assert FormatMarkdownStep().key == "format"
        assert ValidateStep().key == "validate"

    def test_default_step_chain_order_matches_docs(self, tmp_path):
        """Verify default step chain order matches AGENTS.md documentation.
        
        This test prevents drift between the documented step chain
        (tables → fix-tables → images → strip-ai → format → validate)
        and the actual implementation.
        """
        pdf_path = tmp_path / "test.pdf"
        output_file = tmp_path / "test.md"
        
        # Create pipeline with all default flags (nothing disabled)
        pipeline = ConversionPipeline(
            pdf_path,
            output_file,
            api_key="test-key",
            model=MODELS["sonnet"],
            # All processing steps enabled (defaults)
            no_images=False,
            strip_ai_descriptions=False,
            no_format=False,
            no_fix_tables=False,
        )
        
        # Expected order from AGENTS.md:
        # tables → fix-tables → images → strip-ai → format → validate
        # Note: strip-ai is only included when strip_ai_descriptions=True
        expected_keys = ["tables", "fix-tables", "images", "format", "validate"]
        expected_names = [
            "merge continued tables",
            "fix tables",
            "extract images",
            "format markdown",
            "validate",
        ]
        
        actual_keys = [step.key for step in pipeline._steps]
        actual_names = [step.name for step in pipeline._steps]
        
        assert actual_keys == expected_keys, (
            f"Step key order mismatch. Expected {expected_keys}, got {actual_keys}"
        )
        assert actual_names == expected_names, (
            f"Step name order mismatch. Expected {expected_names}, got {actual_names}"
        )


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
        pipeline = _make_pipeline(steps=[])
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

            @property
            def key(self) -> str:
                return "warn"

            def run(self, ctx: ProcessingContext) -> None:
                ctx.validation.warnings.append(("test", "test warning"))

        pipeline = _make_pipeline(steps=[WarnStep()])
        ctx = _make_ctx()
        pipeline._run_steps(ctx)
        assert "test warning" in ctx.validation.warning_messages

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
        
        # Pipeline derives staging dir from output_file -> result.staging
        (tmp_path / "result.staging" / "chunks").mkdir(parents=True)
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

    def test_process_passes_work_dir_to_context(self, tmp_path):
        """_process should pass work_dir to ProcessingContext."""
        output_file = tmp_path / "result.md"
        (tmp_path / "result.staging" / "chunks").mkdir(parents=True)
        
        # Create a step that verifies work_dir is set
        @dataclass
        class CheckWorkDirStep:
            work_dir_was_set: list[bool] = field(default_factory=list)
            
            @property
            def name(self) -> str:
                return "check work dir"
            
            @property
            def key(self) -> str:
                return "check-work-dir"
            
            def run(self, ctx: ProcessingContext) -> None:
                self.work_dir_was_set.append(ctx.work_dir is not None)
        
        step = CheckWorkDirStep()
        pipeline = _make_pipeline(steps=[step], output_file=output_file)
        
        ctx, _ = pipeline._process(parts=["# Test"])
        
        assert step.work_dir_was_set == [True]
        assert ctx.work_dir is not None
        assert ctx.work_dir.path == tmp_path / "result.staging"

    def test_process_sets_table_fix_stats_on_context(self, tmp_path):
        """Processing step can set table_fix_stats on the context."""
        from pdf2md_claude.workdir import TableFixStats
        
        output_file = tmp_path / "result.md"
        (tmp_path / "result.staging" / "chunks").mkdir(parents=True)
        
        # Create a step that sets table_fix_stats on the context
        @dataclass
        class MockTableFixStep:
            @property
            def name(self) -> str:
                return "mock table fix"
            
            @property
            def key(self) -> str:
                return "mock-table-fix"
            
            def run(self, ctx: ProcessingContext) -> None:
                ctx.table_fix_stats = TableFixStats(
                    tables_found=2,
                    tables_fixed=2,
                    total_input_tokens=1000,
                    total_output_tokens=500,
                    total_cost=0.10,
                    total_elapsed_seconds=15.0,
                )
        
        step = MockTableFixStep()
        pipeline = _make_pipeline(steps=[step], output_file=output_file)
        
        ctx, _ = pipeline._process(parts=["# Test"])
        
        # Verify ctx.table_fix_stats was set
        assert ctx.table_fix_stats is not None
        assert ctx.table_fix_stats.tables_fixed == 2

    def test_run_appends_table_fix_stage_cost_to_stats(self, tmp_path):
        """Pipeline.run() should append table fix costs to DocumentUsageStats.stages."""
        from unittest.mock import Mock
        from pdf2md_claude.models import DocumentUsageStats, StageCost
        from pdf2md_claude.workdir import TableFixStats, WorkDir, ChunkUsageStats
        from pdf2md_claude.converter import ConversionResult, ChunkResult, ChunkPlan
        from pdf2md_claude.pipeline import resolve_output
        
        # Create a minimal test PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        output_file = resolve_output(pdf_path, None)
        work_dir = WorkDir(output_file.with_suffix(".staging"))
        work_dir.path.mkdir(parents=True)
        work_dir._chunks_path.mkdir(parents=True)
        
        # Create a step that sets table_fix_stats
        @dataclass
        class MockTableFixStep:
            @property
            def name(self) -> str:
                return "mock table fix"
            
            @property
            def key(self) -> str:
                return "mock-table-fix"
            
            def run(self, ctx: ProcessingContext) -> None:
                ctx.table_fix_stats = TableFixStats(
                    tables_found=3,
                    tables_fixed=2,
                    total_input_tokens=1500,
                    total_output_tokens=800,
                    total_cost=0.15,
                    total_elapsed_seconds=20.0,
                )
        
        # Create a pipeline with our mock step
        pipeline = _make_pipeline(steps=[MockTableFixStep()], output_file=output_file)
        
        # Mock the converter to avoid real API calls
        mock_plan = ChunkPlan(
            index=0,
            page_start=1,
            page_end=1,
            is_first=True,
            is_last=True,
        )
        mock_usage = ChunkUsageStats(
            index=0,
            page_start=1,
            page_end=1,
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=0.01,
            elapsed_seconds=1.0,
        )
        mock_chunk = ChunkResult(
            plan=mock_plan,
            markdown="# Test",
            context_tail="",
            usage=mock_usage,
        )
        mock_result = ConversionResult(
            chunks=[mock_chunk],
            stats=DocumentUsageStats(
                doc_name="test",
                pages=1,
                input_tokens=100,
                output_tokens=50,
                cost=0.01,
                chunks=1,
                elapsed_seconds=1.0,
            ),
            cached_chunks=0,
            fresh_chunks=1,
        )
        pipeline._converter = Mock()
        pipeline._converter.convert = Mock(return_value=mock_result)
        
        # Run the pipeline
        result = pipeline.run(pages_per_chunk=10)
        
        # Verify stage cost was appended to stats
        assert len(result.stats.stages) == 1
        stage = result.stats.stages[0]
        assert isinstance(stage, StageCost)
        assert stage.name == "table fixes"
        assert stage.input_tokens == 1500
        assert stage.output_tokens == 800
        assert stage.cost == 0.15
        assert stage.elapsed_seconds == 20.0
        assert stage.detail == "2 tables"
        
        # Verify total_cost includes stage
        assert result.stats.total_cost == 0.16  # 0.01 base + 0.15 stage

    def test_merge_preserves_persisted_table_fix_stats(self, tmp_path):
        """Merge with --no-fix-tables should preserve persisted table-fix stats."""
        from unittest.mock import Mock
        from pdf2md_claude.models import DocumentUsageStats, StageCost
        from pdf2md_claude.workdir import TableFixStats, WorkDir, ChunkUsageStats
        from pdf2md_claude.pipeline import resolve_output
        
        # Create a minimal test PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        output_file = resolve_output(pdf_path, None)
        work_dir = WorkDir(output_file.with_suffix(".staging"))
        work_dir.path.mkdir(parents=True)
        work_dir._chunks_path.mkdir(parents=True)
        
        # Save base chunk stats
        base_stats = DocumentUsageStats(
            doc_name="test", pages=1, chunks=1,
            input_tokens=100, output_tokens=50,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_stats(base_stats)
        
        # Save persisted table-fix stats (from a previous run)
        tf_stats = TableFixStats(
            tables_found=2, tables_fixed=2,
            total_input_tokens=500, total_output_tokens=300,
            total_cost=0.05, total_elapsed_seconds=10.0,
        )
        work_dir.save_table_fix_stats(tf_stats)
        
        # Save a dummy chunk
        work_dir.create_or_validate(
            pdf_path=pdf_path,
            model_id="test-model",
            pages_per_chunk=10,
            total_pages=1,
            num_chunks=1,
            max_pages=None,
        )
        chunk_usage = ChunkUsageStats(
            index=0, page_start=1, page_end=1,
            input_tokens=100, output_tokens=50,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_chunk(0, "# Test", "", chunk_usage)
        
        # Create pipeline with no_fix_tables=True (so FixTablesStep doesn't run)
        pipeline = ConversionPipeline(
            pdf_path,
            output_file,
            api_key="test-key",
            model=MODELS["sonnet"],
            no_fix_tables=True,
        )
        # Override steps to exclude ValidateStep (which would try to open the dummy PDF)
        pipeline._steps = []
        
        # Run from merge (should preserve persisted table-fix stats)
        result = pipeline.run(pages_per_chunk=10, from_step="merge")
        
        # Verify persisted stage is included
        assert len(result.stats.stages) == 1
        assert result.stats.stages[0].name == "table fixes"
        assert result.stats.stages[0].cost == pytest.approx(0.05)
        assert result.stats.stages[0].detail == "2 tables"
        assert result.stats.total_cost == pytest.approx(0.06)  # 0.01 base + 0.05 stage

    def test_merge_replaces_persisted_stage_when_fix_runs(self, tmp_path):
        """Merge with table fixing should replace persisted stage with fresh one."""
        from unittest.mock import Mock
        from pdf2md_claude.models import DocumentUsageStats, StageCost
        from pdf2md_claude.workdir import TableFixStats, WorkDir, ChunkUsageStats
        from pdf2md_claude.pipeline import resolve_output
        
        # Create a minimal test PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        output_file = resolve_output(pdf_path, None)
        work_dir = WorkDir(output_file.with_suffix(".staging"))
        work_dir.path.mkdir(parents=True)
        work_dir._chunks_path.mkdir(parents=True)
        
        # Save base chunk stats
        base_stats = DocumentUsageStats(
            doc_name="test", pages=1, chunks=1,
            input_tokens=100, output_tokens=50,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_stats(base_stats)
        
        # Save OLD persisted table-fix stats
        old_tf_stats = TableFixStats(
            tables_found=2, tables_fixed=2,
            total_input_tokens=500, total_output_tokens=300,
            total_cost=0.05, total_elapsed_seconds=10.0,
        )
        work_dir.save_table_fix_stats(old_tf_stats)
        
        # Save a dummy chunk
        work_dir.create_or_validate(
            pdf_path=pdf_path,
            model_id="test-model",
            pages_per_chunk=10,
            total_pages=1,
            num_chunks=1,
            max_pages=None,
        )
        chunk_usage = ChunkUsageStats(
            index=0, page_start=1, page_end=1,
            input_tokens=100, output_tokens=50,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_chunk(0, "# Test", "", chunk_usage)
        
        # Create a step that sets FRESH table_fix_stats (different from persisted)
        @dataclass
        class FreshTableFixStep:
            @property
            def name(self) -> str:
                return "fresh table fix"
            
            @property
            def key(self) -> str:
                return "fresh-table-fix"
            
            def run(self, ctx: ProcessingContext) -> None:
                ctx.table_fix_stats = TableFixStats(
                    tables_found=3,
                    tables_fixed=3,
                    total_input_tokens=1000,
                    total_output_tokens=600,
                    total_cost=0.10,
                    total_elapsed_seconds=15.0,
                )
        
        # Create pipeline with fresh fix step
        pipeline = _make_pipeline(steps=[FreshTableFixStep()], output_file=output_file)
        
        # Run from merge (should replace old stage with fresh one)
        result = pipeline.run(pages_per_chunk=10, from_step="merge")
        
        # Verify only ONE stage (fresh one, not duplicate)
        assert len(result.stats.stages) == 1
        assert result.stats.stages[0].name == "table fixes"
        assert result.stats.stages[0].cost == 0.10  # Fresh cost, not 0.05
        assert result.stats.stages[0].detail == "3 tables"  # Fresh count
        assert result.stats.total_cost == 0.11  # 0.01 base + 0.10 fresh stage

    def test_merge_clears_stale_stage_when_zero_tables_fixed(self, tmp_path):
        """Merge should remove old stage when FixTablesStep runs but fixes zero tables."""
        from unittest.mock import Mock
        from pdf2md_claude.models import DocumentUsageStats, StageCost
        from pdf2md_claude.workdir import TableFixStats, WorkDir, ChunkUsageStats
        from pdf2md_claude.pipeline import resolve_output
        
        # Create a minimal test PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        output_file = resolve_output(pdf_path, None)
        work_dir = WorkDir(output_file.with_suffix(".staging"))
        work_dir.path.mkdir(parents=True)
        work_dir._chunks_path.mkdir(parents=True)
        
        # Save base chunk stats
        base_stats = DocumentUsageStats(
            doc_name="test", pages=1, chunks=1,
            input_tokens=100, output_tokens=50,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_stats(base_stats)
        
        # Save OLD persisted table-fix stats (from previous successful run)
        old_tf_stats = TableFixStats(
            tables_found=2, tables_fixed=2,
            total_input_tokens=500, total_output_tokens=300,
            total_cost=0.05, total_elapsed_seconds=10.0,
        )
        work_dir.save_table_fix_stats(old_tf_stats)
        
        # Save a dummy chunk
        work_dir.create_or_validate(
            pdf_path=pdf_path,
            model_id="test-model",
            pages_per_chunk=10,
            total_pages=1,
            num_chunks=1,
            max_pages=None,
        )
        chunk_usage = ChunkUsageStats(
            index=0, page_start=1, page_end=1,
            input_tokens=100, output_tokens=50,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost=0.01, elapsed_seconds=1.0,
        )
        work_dir.save_chunk(0, "# Test", "", chunk_usage)
        
        # Create a step that sets table_fix_stats with tables_fixed=0 (all failed)
        @dataclass
        class FailedTableFixStep:
            @property
            def name(self) -> str:
                return "failed table fix"
            
            @property
            def key(self) -> str:
                return "failed-table-fix"
            
            def run(self, ctx: ProcessingContext) -> None:
                # FixTablesStep ran but all regenerations failed
                ctx.table_fix_stats = TableFixStats(
                    tables_found=2,
                    tables_fixed=0,  # All failed!
                    total_input_tokens=0,
                    total_output_tokens=0,
                    total_cost=0.0,
                    total_elapsed_seconds=0.0,
                )
        
        # Create pipeline with failed fix step
        pipeline = _make_pipeline(steps=[FailedTableFixStep()], output_file=output_file)
        
        # Run from merge (should remove old stage, not re-add since tables_fixed=0)
        result = pipeline.run(pages_per_chunk=10, from_step="merge")
        
        # Verify NO stage (old was cleared, new was not added since tables_fixed=0)
        assert len(result.stats.stages) == 0
        assert result.stats.total_cost == 0.01  # Only base cost


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


def _write_manifest(staging_dir: Path, pages_per_chunk: int = 20) -> None:
    """Write a minimal manifest.json into a .staging/ directory."""
    staging_dir.mkdir(parents=True, exist_ok=True)
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
    (staging_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2) + "\n",
        encoding="utf-8",
    )


class TestResolvePagesPerChunk:
    """Tests for ConversionPipeline.resolve_pages_per_chunk()."""

    def test_no_workdir_returns_requested(self, tmp_path: Path):
        """When no workdir exists, returns the requested value."""
        output_file = tmp_path / "doc.md"
        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        assert pipeline.resolve_pages_per_chunk(10) == 10

    def test_manifest_matches_returns_silently(self, tmp_path: Path):
        """When manifest matches requested value, returns it (no warning)."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.staging", pages_per_chunk=15)

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        assert pipeline.resolve_pages_per_chunk(15) == 15

    def test_manifest_mismatch_returns_manifest_value(self, tmp_path: Path):
        """When manifest differs, returns the manifest value."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.staging", pages_per_chunk=20)

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        assert pipeline.resolve_pages_per_chunk(10) == 20

    def test_manifest_mismatch_logs_warning(self, tmp_path: Path, caplog):
        """When manifest differs, a warning is logged."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.staging", pages_per_chunk=20)

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
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
        staging_dir = tmp_path / "doc.staging"
        staging_dir.mkdir()
        (staging_dir / "manifest.json").write_text("bad json", encoding="utf-8")

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        assert pipeline.resolve_pages_per_chunk(10) == 10

    def test_force_bypasses_manifest(self, tmp_path: Path):
        """With force=True, returns requested value even if manifest differs."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.staging", pages_per_chunk=20)

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        assert pipeline.resolve_pages_per_chunk(10, force=True) == 10

    def test_force_no_warning(self, tmp_path: Path, caplog):
        """With force=True, no warning is logged even on mismatch."""
        output_file = tmp_path / "doc.md"
        _write_manifest(tmp_path / "doc.staging", pages_per_chunk=20)

        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        import logging
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            pipeline.resolve_pages_per_chunk(10, force=True)

        assert not any(
            "pages_per_chunk" in msg for msg in caplog.messages
        )


# ---------------------------------------------------------------------------
# run() from_step validation tests
# ---------------------------------------------------------------------------


class TestRunFromStepValidation:
    """Tests for ConversionPipeline.run() from_step validation."""

    def test_unsupported_from_step_raises_value_error(self, tmp_path: Path):
        """run() raises ValueError for unsupported from_step values."""
        output_file = tmp_path / "doc.md"
        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []

        with pytest.raises(ValueError, match="Unsupported --from step: 'unknown'"):
            pipeline.run(pages_per_chunk=10, from_step="unknown")

    def test_merge_passes_validation_guard(self, tmp_path: Path):
        """from_step='merge' passes the ValueError guard (hits RuntimeError next)."""
        output_file = tmp_path / "doc.md"
        pipeline = ConversionPipeline(
            _DUMMY_PDF, output_file, api_key="test-key", model=MODELS["sonnet"]
        )
        pipeline._steps = []
        # No staging dir → RuntimeError proves it passed the ValueError check.
        with pytest.raises(RuntimeError, match="Staging directory not found"):
            pipeline.run(pages_per_chunk=10, from_step="merge")

    def test_none_from_step_runs_full_conversion(self, tmp_path: Path):
        """from_step=None proceeds to full conversion without ValueError."""
        from unittest.mock import Mock, patch

        output_file = tmp_path / "doc.md"
        (tmp_path / "doc.staging" / "chunks").mkdir(parents=True)

        mock_model = Mock()
        mock_model.model_id = "test-model"
        mock_model.beta_header = None

        # Patch anthropic.Anthropic and PdfConverter
        with patch("pdf2md_claude.pipeline.anthropic.Anthropic") as mock_anthropic_class:
            with patch("pdf2md_claude.pipeline.PdfConverter") as mock_converter_class:
                mock_converter = Mock()
                mock_converter.convert.return_value = Mock(
                    chunks=[], stats=Mock(), cached_chunks=0, fresh_chunks=0,
                )
                mock_converter_class.return_value = mock_converter

                pipeline = ConversionPipeline(
                    _DUMMY_PDF, output_file,
                    api_key="test-key",
                    model=mock_model,
                )
                pipeline._steps = []
                result = pipeline.run(pages_per_chunk=10, from_step=None)
                assert result is not None
                mock_converter.convert.assert_called_once()
                mock_anthropic_class.assert_called_once_with(api_key="test-key")
