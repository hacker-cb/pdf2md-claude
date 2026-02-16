"""Unit tests for WorkDir, Manifest, and ChunkUsageStats."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pdf2md_claude.models import DocumentUsageStats, StageCost
from pdf2md_claude.workdir import ChunkUsageStats, Manifest, TableFixResult, TableFixStats, WorkDir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path, name: str = "doc.pdf", size: int = 1024) -> Path:
    """Create a dummy PDF file with a known size."""
    pdf = tmp_path / name
    pdf.write_bytes(b"\x00" * size)
    return pdf


def _make_usage(index: int = 0) -> ChunkUsageStats:
    """Create a sample ChunkUsageStats for testing."""
    return ChunkUsageStats(
        index=index,
        page_start=index * 20 + 1,
        page_end=(index + 1) * 20,
        input_tokens=150_000,
        output_tokens=8_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        cost=1.95,
        elapsed_seconds=45.2,
    )


def _default_params(pdf_path: Path) -> dict:
    """Default create_or_validate parameters."""
    return dict(
        pdf_path=pdf_path,
        total_pages=40,
        pages_per_chunk=20,
        max_pages=None,
        model_id="claude-test-1",
        num_chunks=2,
    )


# ---------------------------------------------------------------------------
# 1. Manifest roundtrip
# ---------------------------------------------------------------------------


class TestManifest:
    """Tests for Manifest dataclass serialization."""

    def test_roundtrip(self):
        """Manifest -> asdict -> JSON -> Manifest should round-trip."""
        m = Manifest(
            pdf_mtime=1707321600.0,
            pdf_size=4521984,
            total_pages=88,
            pages_per_chunk=20,
            max_pages=None,
            model_id="claude-opus-4-6",
            num_chunks=5,
        )
        data = asdict(m)
        json_str = json.dumps(data)
        restored = Manifest(**json.loads(json_str))
        assert restored == m

    def test_frozen(self):
        """Manifest should be frozen (immutable)."""
        m = Manifest(
            pdf_mtime=0.0, pdf_size=0, total_pages=1,
            pages_per_chunk=1, max_pages=None,
            model_id="test", num_chunks=1,
        )
        with pytest.raises(AttributeError):
            m.pdf_mtime = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. ChunkUsageStats roundtrip
# ---------------------------------------------------------------------------


class TestChunkUsageStats:
    """Tests for ChunkUsageStats serialization."""

    def test_roundtrip(self):
        """ChunkUsageStats -> asdict -> JSON -> ChunkUsageStats."""
        usage = _make_usage(0)
        data = asdict(usage)
        json_str = json.dumps(data)
        restored = ChunkUsageStats(**json.loads(json_str))
        assert restored == usage


# ---------------------------------------------------------------------------
# 3. WorkDir.create_or_validate
# ---------------------------------------------------------------------------


class TestCreateOrValidate:
    """Tests for WorkDir manifest creation and validation."""

    def test_creates_directory_and_manifest(self, tmp_path: Path):
        """First call creates the .staging dir and manifest.json."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        cached = wd.create_or_validate(**_default_params(pdf))

        assert cached == []
        assert (tmp_path / "out.staging" / "manifest.json").exists()

    def test_matching_manifest_returns_empty_cached(self, tmp_path: Path):
        """Repeated call with same params returns empty cached list."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))
        cached = wd.create_or_validate(**_default_params(pdf))

        assert cached == []

    def test_matching_manifest_detects_cached_chunks(self, tmp_path: Path):
        """If chunks exist on disk and manifest matches, they're detected."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Save chunk 0.
        wd.save_chunk(0, "# Hello", "context", _make_usage(0))

        # Re-validate: chunk 0 should be cached.
        cached = wd.create_or_validate(**_default_params(pdf))
        assert cached == [0]

    def test_staleness_clears_chunks(self, tmp_path: Path):
        """Changing a parameter invalidates all cached chunks."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Save a chunk.
        wd.save_chunk(0, "# Hello", "context", _make_usage(0))
        assert wd.has_chunk(0)

        # Change pages_per_chunk -> triggers invalidation.
        params = _default_params(pdf)
        params["pages_per_chunk"] = 10
        params["num_chunks"] = 4
        cached = wd.create_or_validate(**params)

        assert cached == []
        assert not wd.has_chunk(0)

    def test_staleness_on_model_change(self, tmp_path: Path):
        """Changing model_id invalidates all cached chunks."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))
        wd.save_chunk(0, "md", "ctx", _make_usage(0))

        params = _default_params(pdf)
        params["model_id"] = "claude-different-model"
        cached = wd.create_or_validate(**params)

        assert cached == []
        assert not wd.has_chunk(0)


# ---------------------------------------------------------------------------
# 4. save_chunk / load_chunk roundtrip
# ---------------------------------------------------------------------------


class TestChunkIO:
    """Tests for chunk save/load operations."""

    def test_save_load_markdown(self, tmp_path: Path):
        """Markdown content should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "# Title\n\nContent", "tail", _make_usage(0))
        assert wd.load_chunk_markdown(0) == "# Title\n\nContent"

    def test_save_load_context(self, tmp_path: Path):
        """Context tail should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "my context tail", _make_usage(0))
        assert wd.load_chunk_context(0) == "my context tail"

    def test_load_context_missing_returns_empty(self, tmp_path: Path):
        """Loading context for a non-existent chunk returns empty string."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_chunk_context(99) == ""

    def test_save_load_usage(self, tmp_path: Path):
        """ChunkUsageStats should survive save/load roundtrip."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        usage = _make_usage(1)
        wd.save_chunk(1, "md", "ctx", usage)
        loaded = wd.load_chunk_usage(1)
        assert loaded == usage

    def test_file_naming_1_indexed(self, tmp_path: Path):
        """Chunk files should use 1-indexed naming."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "ctx", _make_usage(0))
        assert (tmp_path / "out.staging" / "chunks" / "chunk_01.md").exists()
        assert (tmp_path / "out.staging" / "chunks" / "chunk_01_context.md").exists()
        assert (tmp_path / "out.staging" / "chunks" / "chunk_01_meta.json").exists()


# ---------------------------------------------------------------------------
# 5. has_chunk
# ---------------------------------------------------------------------------


class TestHasChunk:
    """Tests for has_chunk completeness check."""

    def test_false_before_save(self, tmp_path: Path):
        """has_chunk returns False for unsaved chunks."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert not wd.has_chunk(0)

    def test_true_after_save(self, tmp_path: Path):
        """has_chunk returns True after save_chunk completes."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "ctx", _make_usage(0))
        assert wd.has_chunk(0)

    def test_false_for_different_index(self, tmp_path: Path):
        """has_chunk returns False for a different (unsaved) index."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "ctx", _make_usage(0))
        assert not wd.has_chunk(1)


# ---------------------------------------------------------------------------
# 6. save_stats / load_stats
# ---------------------------------------------------------------------------


class TestStatsIO:
    """Tests for document-level stats save/load."""

    def test_roundtrip(self, tmp_path: Path):
        """DocumentUsageStats should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        stats = DocumentUsageStats(
            doc_name="test-doc",
            pages=40,
            chunks=2,
            input_tokens=300_000,
            output_tokens=16_000,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=3.90,
            elapsed_seconds=90.4,
        )
        wd.save_stats(stats)
        loaded = wd.load_stats()
        assert loaded == stats

    def test_load_missing_returns_none(self, tmp_path: Path):
        """load_stats returns None when stats.json does not exist."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_stats() is None

    def test_load_combined_stats_without_table_fixes(self, tmp_path: Path):
        """load_combined_stats returns chunk-only stats when no table fixes."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        stats = DocumentUsageStats(
            doc_name="test", pages=10, chunks=1,
            input_tokens=1000, output_tokens=500,
            cost=0.05, elapsed_seconds=10.0,
        )
        wd.save_stats(stats)

        combined = wd.load_combined_stats()
        assert combined is not None
        assert combined.doc_name == "test"
        assert combined.stages == []
        assert combined.total_cost == 0.05

    def test_load_combined_stats_with_table_fixes(self, tmp_path: Path):
        """load_combined_stats includes table fixes as a stage."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Save chunk stats
        chunk_stats = DocumentUsageStats(
            doc_name="test", pages=10, chunks=1,
            input_tokens=1000, output_tokens=500,
            cost=0.05, elapsed_seconds=10.0,
        )
        wd.save_stats(chunk_stats)

        # Save table-fix stats
        tf_stats = TableFixStats(
            tables_found=3, tables_fixed=3,
            total_input_tokens=2000, total_output_tokens=1500,
            total_cost=0.15, total_elapsed_seconds=20.0,
        )
        wd.save_table_fix_stats(tf_stats)

        # Load combined
        combined = wd.load_combined_stats()
        assert combined is not None
        assert len(combined.stages) == 1
        assert combined.stages[0].name == "table fixes"
        assert combined.stages[0].detail == "3 tables"
        assert combined.stages[0].input_tokens == 2000
        assert combined.stages[0].output_tokens == 1500
        assert combined.stages[0].cost == 0.15
        assert combined.total_cost == 0.20  # chunk + stage
        assert combined.total_all_input_tokens == 3000
        assert combined.total_all_output_tokens == 2000
        assert combined.total_elapsed == 30.0

    def test_load_combined_stats_zero_fixed_no_stage(self, tmp_path: Path):
        """load_combined_stats should NOT add stage when tables_fixed == 0."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Save chunk stats
        chunk_stats = DocumentUsageStats(
            doc_name="test", pages=10, chunks=1,
            input_tokens=1000, output_tokens=500,
            cost=0.05, elapsed_seconds=10.0,
        )
        wd.save_stats(chunk_stats)

        # Save table-fix stats with tables_fixed=0 (found but not fixed)
        tf_stats = TableFixStats(
            tables_found=3, tables_fixed=0,  # None were fixed
            total_input_tokens=0, total_output_tokens=0,
            total_cost=0.0, total_elapsed_seconds=0.0,
        )
        wd.save_table_fix_stats(tf_stats)

        # Load combined
        combined = wd.load_combined_stats()
        assert combined is not None
        # Should NOT add stage when tables_fixed == 0
        assert len(combined.stages) == 0
        assert combined.total_cost == 0.05  # Only chunk cost

    def test_stage_cost_deserialization(self, tmp_path: Path):
        """DocumentUsageStats __post_init__ should convert dict stages to StageCost."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Manually construct stats with dict stages (simulates JSON load)
        stats = DocumentUsageStats(
            doc_name="test", pages=10,
            stages=[
                {"name": "stage1", "cost": 0.10, "input_tokens": 100, "output_tokens": 50, "elapsed_seconds": 5.0, "detail": "test"},
                {"name": "stage2", "cost": 0.20, "input_tokens": 200, "output_tokens": 100, "elapsed_seconds": 10.0, "detail": ""},
            ]
        )

        # __post_init__ should have converted dicts to StageCost
        assert len(stats.stages) == 2
        assert isinstance(stats.stages[0], StageCost)
        assert isinstance(stats.stages[1], StageCost)
        assert stats.stages[0].name == "stage1"
        assert stats.stages[0].cost == 0.10
        assert abs(stats.total_cost - 0.30) < 0.001  # base 0.0 + 0.10 + 0.20

    def test_backward_compatibility_no_stages_field(self, tmp_path: Path):
        """Old stats files without 'stages' key should load with empty stages."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Write old-format JSON manually (no stages key)
        old_json = {
            "doc_name": "old-doc",
            "pages": 10,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost": 0.05,
            "chunks": 1,
            "elapsed_seconds": 10.0,
        }
        stats_path = wd._chunks_path
        stats_path.mkdir(parents=True, exist_ok=True)
        (stats_path / "stats.json").write_text(json.dumps(old_json, indent=2))

        # Should load without error, stages defaults to []
        loaded = wd.load_stats()
        assert loaded is not None
        assert loaded.stages == []
        assert loaded.total_cost == 0.05  # no stages


# ---------------------------------------------------------------------------
# 7. invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    """Tests for WorkDir.invalidate()."""

    def test_clears_everything(self, tmp_path: Path):
        """invalidate removes chunks, stats, and manifest."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md0", "ctx0", _make_usage(0))
        wd.save_chunk(1, "md1", "ctx1", _make_usage(1))
        stats = DocumentUsageStats(
            doc_name="test", pages=40, chunks=2,
        )
        wd.save_stats(stats)

        wd.invalidate()

        assert not wd.has_chunk(0)
        assert not wd.has_chunk(1)
        assert wd.load_stats() is None
        assert not (tmp_path / "out.staging" / "manifest.json").exists()

    def test_keeps_directory(self, tmp_path: Path):
        """invalidate keeps the .staging directory itself."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))
        wd.save_chunk(0, "md", "ctx", _make_usage(0))

        wd.invalidate()

        assert wd.path.exists()
        assert wd.path.is_dir()

    def test_clears_manifest(self, tmp_path: Path):
        """invalidate removes manifest.json."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))
        wd.save_chunk(0, "md", "ctx", _make_usage(0))

        wd.invalidate()

        assert not (tmp_path / "out.staging" / "manifest.json").exists()
        assert wd.load_manifest() is None

    def test_resets_cached_manifest(self, tmp_path: Path):
        """invalidate clears the in-memory manifest cache."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        wd.invalidate()

        # After invalidate, chunk_count() should fail (no manifest).
        with pytest.raises(RuntimeError, match="manifest not loaded"):
            wd.chunk_count()

    def test_safe_when_directory_missing(self, tmp_path: Path):
        """invalidate does not raise when .staging/ dir does not exist."""
        wd = WorkDir(tmp_path / "nonexistent.staging")
        # Should not raise FileNotFoundError.
        wd.invalidate()


# ---------------------------------------------------------------------------
# 8. Resume scenario
# ---------------------------------------------------------------------------


class TestResume:
    """Tests for the resume workflow."""

    def test_resume_detects_saved_chunks(self, tmp_path: Path):
        """New WorkDir with same params detects previously saved chunks."""
        pdf = _make_pdf(tmp_path)
        params = _default_params(pdf)

        # First "run": save chunks 0 and 1.
        wd1 = WorkDir(tmp_path / "out.staging")
        wd1.create_or_validate(**params)
        wd1.save_chunk(0, "chunk0", "ctx0", _make_usage(0))
        wd1.save_chunk(1, "chunk1", "ctx1", _make_usage(1))

        # Second "run": new WorkDir instance with same params.
        wd2 = WorkDir(tmp_path / "out.staging")
        cached = wd2.create_or_validate(**params)

        assert sorted(cached) == [0, 1]
        assert wd2.load_chunk_markdown(0) == "chunk0"
        assert wd2.load_chunk_markdown(1) == "chunk1"

    def test_partial_resume(self, tmp_path: Path):
        """Resume after crash: only completed chunks are detected."""
        pdf = _make_pdf(tmp_path)
        params = _default_params(pdf)

        # Save only chunk 0 (chunk 1 "crashed").
        wd1 = WorkDir(tmp_path / "out.staging")
        wd1.create_or_validate(**params)
        wd1.save_chunk(0, "chunk0", "ctx0", _make_usage(0))

        # Resume: only chunk 0 is cached.
        wd2 = WorkDir(tmp_path / "out.staging")
        cached = wd2.create_or_validate(**params)

        assert cached == [0]
        assert wd2.has_chunk(0)
        assert not wd2.has_chunk(1)


# ---------------------------------------------------------------------------
# 9. chunk_count
# ---------------------------------------------------------------------------


class TestLoadManifest:
    """Tests for WorkDir.load_manifest() (lenient reader)."""

    def test_returns_manifest_when_exists(self, tmp_path: Path):
        """load_manifest returns the manifest after create_or_validate."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        manifest = wd.load_manifest()
        assert manifest is not None
        assert manifest.pages_per_chunk == 20
        assert manifest.total_pages == 40
        assert manifest.model_id == "claude-test-1"

    def test_returns_none_when_missing(self, tmp_path: Path):
        """load_manifest returns None when .staging/ does not exist."""
        wd = WorkDir(tmp_path / "nonexistent.staging")
        assert wd.load_manifest() is None

    def test_returns_none_when_corrupt(self, tmp_path: Path):
        """load_manifest returns None on corrupt manifest.json."""
        staging_dir = tmp_path / "out.staging"
        staging_dir.mkdir()
        (staging_dir / "manifest.json").write_text("not json!", encoding="utf-8")

        wd = WorkDir(staging_dir)
        assert wd.load_manifest() is None

    def test_independent_of_internal_cache(self, tmp_path: Path):
        """load_manifest reads from disk, independent of _manifest cache."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Create a fresh WorkDir instance (no _manifest cached).
        wd2 = WorkDir(tmp_path / "out.staging")
        manifest = wd2.load_manifest()
        assert manifest is not None
        assert manifest.num_chunks == 2


class TestChunkCount:
    """Tests for WorkDir.chunk_count()."""

    def test_returns_num_chunks(self, tmp_path: Path):
        """chunk_count returns num_chunks from the manifest."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.chunk_count() == 2

    def test_raises_without_manifest(self, tmp_path: Path):
        """chunk_count raises if no manifest has been loaded."""
        wd = WorkDir(tmp_path / "nonexistent.staging")
        with pytest.raises(RuntimeError, match="manifest not loaded"):
            wd.chunk_count()


# ---------------------------------------------------------------------------
# 10. Phase output
# ---------------------------------------------------------------------------


class TestOutputIO:
    """Tests for phase output save/load operations."""

    def test_save_load_output_roundtrip(self, tmp_path: Path):
        """Saved merged.md should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        markdown = "# Test Output\n\nThis is the merged markdown."
        wd.save_output(markdown)
        loaded = wd.load_output()

        assert loaded == markdown

    def test_load_output_missing_returns_none(self, tmp_path: Path):
        """load_output returns None when merged.md does not exist."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_output() is None


# ---------------------------------------------------------------------------
# 11. Table fixer persistence
# ---------------------------------------------------------------------------


class TestTableFixerIO:
    """Tests for table fixer result save/load operations."""

    def test_save_table_fix_creates_directory(self, tmp_path: Path):
        """save_table_fix creates table_fixer/ directory lazily."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        result = TableFixResult(
            index=0,
            label="Table 1",
            page_numbers=[1],
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=0.01,
            elapsed_seconds=1.5,
            before_chars=500,
            after_chars=600,
        )

        wd.save_table_fix(result, "<table>before</table>", "<table>after</table>")

        assert (tmp_path / "out.staging" / "table_fixer").exists()
        assert (tmp_path / "out.staging" / "table_fixer" / "p001-001_table_1.json").exists()
        assert (tmp_path / "out.staging" / "table_fixer" / "p001-001_table_1_before.html").exists()
        assert (tmp_path / "out.staging" / "table_fixer" / "p001-001_table_1_after.html").exists()

    def test_save_table_fix_multi_page_naming(self, tmp_path: Path):
        """save_table_fix uses range format for multi-page tables."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        result = TableFixResult(
            index=1,
            label="Table 23",
            page_numbers=[3, 4, 5, 6],
            input_tokens=200,
            output_tokens=100,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=0.02,
            elapsed_seconds=2.5,
            before_chars=1000,
            after_chars=1200,
        )

        wd.save_table_fix(result, "<table>old</table>", "<table>new</table>")

        assert (tmp_path / "out.staging" / "table_fixer" / "p003-006_table_23.json").exists()
        assert (tmp_path / "out.staging" / "table_fixer" / "p003-006_table_23_before.html").exists()
        assert (tmp_path / "out.staging" / "table_fixer" / "p003-006_table_23_after.html").exists()

    def test_table_fix_result_roundtrip(self, tmp_path: Path):
        """TableFixResult should survive save/load roundtrip."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        result = TableFixResult(
            index=0,
            label="Table 3",
            page_numbers=[2],
            input_tokens=150,
            output_tokens=75,
            cache_creation_tokens=10,
            cache_read_tokens=20,
            cost=0.015,
            elapsed_seconds=1.8,
            before_chars=450,
            after_chars=550,
        )

        before_html = "<table><tr><td>broken</td></tr></table>"
        after_html = "<table><tr><td>fixed</td></tr></table>"

        wd.save_table_fix(result, before_html, after_html)

        # Read back the files
        prefix = "p002-002_table_3"
        json_path = tmp_path / "out.staging" / "table_fixer" / f"{prefix}.json"
        before_path = tmp_path / "out.staging" / "table_fixer" / f"{prefix}_before.html"
        after_path = tmp_path / "out.staging" / "table_fixer" / f"{prefix}_after.html"

        loaded_result = TableFixResult(**json.loads(json_path.read_text()))
        loaded_before = before_path.read_text()
        loaded_after = after_path.read_text()

        assert loaded_result == result
        assert loaded_before == before_html
        assert loaded_after == after_html

    def test_table_fix_stats_roundtrip(self, tmp_path: Path):
        """TableFixStats should survive save/load roundtrip."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        stats = TableFixStats(
            tables_found=5,
            tables_fixed=4,
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cost=0.10,
            total_elapsed_seconds=10.5,
        )

        wd.save_table_fix_stats(stats)
        loaded = wd.load_table_fix_stats()

        assert loaded == stats

    def test_load_table_fix_stats_missing_returns_none(self, tmp_path: Path):
        """load_table_fix_stats returns None when file does not exist."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_table_fix_stats() is None

    def test_label_sanitization(self, tmp_path: Path):
        """Label sanitization should handle special characters."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        result = TableFixResult(
            index=0,
            label="Table 5 – Event Codes",
            page_numbers=[10],
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=0.01,
            elapsed_seconds=1.0,
            before_chars=400,
            after_chars=500,
        )

        wd.save_table_fix(result, "<table>x</table>", "<table>y</table>")

        # Should sanitize to table_5_-_event_codes
        assert (tmp_path / "out.staging" / "table_fixer" / "p010-010_table_5_-_event_codes.json").exists()

    def test_clear_table_fixer_removes_all_files(self, tmp_path: Path):
        """clear_table_fixer should remove all per-table files and stats."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Create several table-fix results
        for i in range(3):
            result = TableFixResult(
                index=i,
                label=f"Table {i+1}",
                page_numbers=[i+1],
                input_tokens=100,
                output_tokens=50,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                cost=0.01,
                elapsed_seconds=1.0,
                before_chars=400,
                after_chars=500,
            )
            wd.save_table_fix(result, f"<table>old{i}</table>", f"<table>new{i}</table>")

        # Save aggregate stats
        stats = TableFixStats(
            tables_found=3, tables_fixed=3,
            total_input_tokens=300, total_output_tokens=150,
            total_cost=0.03, total_elapsed_seconds=3.0,
        )
        wd.save_table_fix_stats(stats)

        # Verify files exist
        table_fixer_dir = tmp_path / "out.staging" / "table_fixer"
        assert (table_fixer_dir / "p001-001_table_1.json").exists()
        assert (table_fixer_dir / "p002-002_table_2.json").exists()
        assert (table_fixer_dir / "p003-003_table_3.json").exists()
        assert (table_fixer_dir / "stats.json").exists()

        # Clear table fixer directory
        wd.clear_table_fixer()

        # Verify directory exists but is empty
        assert table_fixer_dir.exists()
        assert list(table_fixer_dir.iterdir()) == []

    def test_clear_table_fixer_safe_when_dir_missing(self, tmp_path: Path):
        """clear_table_fixer should be safe to call when directory doesn't exist."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Don't create table_fixer directory
        table_fixer_dir = tmp_path / "out.staging" / "table_fixer"
        assert not table_fixer_dir.exists()

        # Should not raise
        wd.clear_table_fixer()

        # Directory should now exist (empty)
        assert table_fixer_dir.exists()
        assert list(table_fixer_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 12. Content hashing
# ---------------------------------------------------------------------------


class TestContentHash:
    """Tests for WorkDir content hashing utilities."""

    def test_content_hash_deterministic(self, tmp_path: Path):
        """content_hash should be deterministic for same input."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content A", encoding="utf-8")
        file2.write_text("content B", encoding="utf-8")

        hash1 = WorkDir.content_hash([file1, file2])
        hash2 = WorkDir.content_hash([file1, file2])
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_content_hash_sorted_order(self, tmp_path: Path):
        """content_hash should sort paths for determinism."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content A", encoding="utf-8")
        file2.write_text("content B", encoding="utf-8")

        # Order shouldn't matter (sorted internally)
        hash1 = WorkDir.content_hash([file1, file2])
        hash2 = WorkDir.content_hash([file2, file1])
        assert hash1 == hash2

    def test_content_hash_empty_list(self, tmp_path: Path):
        """content_hash should return empty string for empty list."""
        assert WorkDir.content_hash([]) == ""

    def test_content_hash_changes_with_content(self, tmp_path: Path):
        """content_hash should change when file content changes."""
        file1 = tmp_path / "file1.txt"
        file1.write_text("original", encoding="utf-8")

        hash1 = WorkDir.content_hash([file1])

        file1.write_text("modified", encoding="utf-8")
        hash2 = WorkDir.content_hash([file1])

        assert hash1 != hash2

    def test_content_hash_glob_matches_files(self, tmp_path: Path):
        """content_hash_glob should hash files matching glob pattern."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Create merged.md
        merged_path = tmp_path / "out.staging" / "merged.md"
        merged_path.write_text("# Test markdown", encoding="utf-8")

        hash1 = wd.content_hash_glob("merged.md")
        assert hash1 != ""
        assert len(hash1) == 64

        # Should match direct content_hash
        hash2 = WorkDir.content_hash([merged_path])
        assert hash1 == hash2

    def test_content_hash_glob_no_matches(self, tmp_path: Path):
        """content_hash_glob should return empty string when no files match."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        hash1 = wd.content_hash_glob("nonexistent.md")
        assert hash1 == ""

    def test_content_hash_glob_multiple_patterns(self, tmp_path: Path):
        """content_hash_glob should support multiple glob patterns."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Create multiple files
        (tmp_path / "out.staging" / "file1.md").write_text("content 1", encoding="utf-8")
        (tmp_path / "out.staging" / "file2.txt").write_text("content 2", encoding="utf-8")

        hash1 = wd.content_hash_glob("*.md", "*.txt")
        assert hash1 != ""
        assert len(hash1) == 64


# ---------------------------------------------------------------------------
# 13. Table fixer output caching
# ---------------------------------------------------------------------------


class TestTableFixerOutputCache:
    """Tests for table fixer output save/load operations."""

    def test_save_load_table_fixer_output_roundtrip(self, tmp_path: Path):
        """Saved table fixer output should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        markdown = "# Test\n\n<table><tr><td>Fixed table</td></tr></table>"
        wd.save_table_fixer_output(markdown)
        loaded = wd.load_table_fixer_output()

        assert loaded == markdown

    def test_load_table_fixer_output_missing_returns_none(self, tmp_path: Path):
        """load_table_fixer_output returns None when file does not exist."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_table_fixer_output() is None

    def test_table_fix_stats_backward_compat_no_input_hash(self, tmp_path: Path):
        """Old stats.json without input_hash should load with empty string."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Write old-format JSON manually (no input_hash key)
        old_json = {
            "tables_found": 3,
            "tables_fixed": 2,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "total_cost": 0.10,
            "total_elapsed_seconds": 15.0,
        }
        stats_path = wd.table_fixer_path
        stats_path.mkdir(parents=True, exist_ok=True)
        (stats_path / "stats.json").write_text(json.dumps(old_json, indent=2))

        # Should load without error, input_hash defaults to ""
        loaded = wd.load_table_fix_stats()
        assert loaded is not None
        assert loaded.input_hash == ""
        assert loaded.tables_found == 3
        assert loaded.tables_fixed == 2

    def test_clear_table_fixer_removes_output_md(self, tmp_path: Path):
        """clear_table_fixer should remove output.md along with other files."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.staging")
        wd.create_or_validate(**_default_params(pdf))

        # Save table fixer output
        wd.save_table_fixer_output("# Cached output")

        # Save some table fix results
        result = TableFixResult(
            index=0,
            label="Table 1",
            page_numbers=[1],
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=0.01,
            elapsed_seconds=1.0,
            before_chars=100,
            after_chars=200,
        )
        wd.save_table_fix(result, "<table>old</table>", "<table>new</table>")

        # Verify files exist
        table_fixer_dir = tmp_path / "out.staging" / "table_fixer"
        assert (table_fixer_dir / "output.md").exists()
        assert (table_fixer_dir / "p001-001_table_1.json").exists()

        # Clear table fixer directory
        wd.clear_table_fixer()

        # Verify output.md is removed
        assert not (table_fixer_dir / "output.md").exists()
        assert not (table_fixer_dir / "p001-001_table_1.json").exists()
        assert list(table_fixer_dir.iterdir()) == []
