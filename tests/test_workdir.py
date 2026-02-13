"""Unit tests for WorkDir, Manifest, and ChunkUsageStats."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pdf2md_claude.models import DocumentUsageStats
from pdf2md_claude.workdir import ChunkUsageStats, Manifest, WorkDir


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
        """First call creates the .chunks dir and manifest.json."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        cached = wd.create_or_validate(**_default_params(pdf))

        assert cached == []
        assert (tmp_path / "out.chunks" / "manifest.json").exists()

    def test_matching_manifest_returns_empty_cached(self, tmp_path: Path):
        """Repeated call with same params returns empty cached list."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))
        cached = wd.create_or_validate(**_default_params(pdf))

        assert cached == []

    def test_matching_manifest_detects_cached_chunks(self, tmp_path: Path):
        """If chunks exist on disk and manifest matches, they're detected."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        # Save chunk 0.
        wd.save_chunk(0, "# Hello", "context", _make_usage(0))

        # Re-validate: chunk 0 should be cached.
        cached = wd.create_or_validate(**_default_params(pdf))
        assert cached == [0]

    def test_staleness_clears_chunks(self, tmp_path: Path):
        """Changing a parameter invalidates all cached chunks."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
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
        wd = WorkDir(tmp_path / "out.chunks")
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
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "# Title\n\nContent", "tail", _make_usage(0))
        assert wd.load_chunk_markdown(0) == "# Title\n\nContent"

    def test_save_load_context(self, tmp_path: Path):
        """Context tail should survive save/load."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "my context tail", _make_usage(0))
        assert wd.load_chunk_context(0) == "my context tail"

    def test_load_context_missing_returns_empty(self, tmp_path: Path):
        """Loading context for a non-existent chunk returns empty string."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_chunk_context(99) == ""

    def test_save_load_usage(self, tmp_path: Path):
        """ChunkUsageStats should survive save/load roundtrip."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        usage = _make_usage(1)
        wd.save_chunk(1, "md", "ctx", usage)
        loaded = wd.load_chunk_usage(1)
        assert loaded == usage

    def test_file_naming_1_indexed(self, tmp_path: Path):
        """Chunk files should use 1-indexed naming."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "ctx", _make_usage(0))
        assert (tmp_path / "out.chunks" / "chunk_01.md").exists()
        assert (tmp_path / "out.chunks" / "chunk_01_context.md").exists()
        assert (tmp_path / "out.chunks" / "chunk_01_meta.json").exists()


# ---------------------------------------------------------------------------
# 5. has_chunk
# ---------------------------------------------------------------------------


class TestHasChunk:
    """Tests for has_chunk completeness check."""

    def test_false_before_save(self, tmp_path: Path):
        """has_chunk returns False for unsaved chunks."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        assert not wd.has_chunk(0)

    def test_true_after_save(self, tmp_path: Path):
        """has_chunk returns True after save_chunk completes."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        wd.save_chunk(0, "md", "ctx", _make_usage(0))
        assert wd.has_chunk(0)

    def test_false_for_different_index(self, tmp_path: Path):
        """has_chunk returns False for a different (unsaved) index."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
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
        wd = WorkDir(tmp_path / "out.chunks")
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
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.load_stats() is None


# ---------------------------------------------------------------------------
# 7. invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    """Tests for WorkDir.invalidate()."""

    def test_clears_everything(self, tmp_path: Path):
        """invalidate removes chunks, stats, and manifest."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
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
        assert not (tmp_path / "out.chunks" / "manifest.json").exists()

    def test_keeps_directory(self, tmp_path: Path):
        """invalidate keeps the .chunks directory itself."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))
        wd.save_chunk(0, "md", "ctx", _make_usage(0))

        wd.invalidate()

        assert wd.path.exists()
        assert wd.path.is_dir()

    def test_clears_manifest(self, tmp_path: Path):
        """invalidate removes manifest.json."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))
        wd.save_chunk(0, "md", "ctx", _make_usage(0))

        wd.invalidate()

        assert not (tmp_path / "out.chunks" / "manifest.json").exists()
        assert wd.load_manifest() is None

    def test_resets_cached_manifest(self, tmp_path: Path):
        """invalidate clears the in-memory manifest cache."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        wd.invalidate()

        # After invalidate, chunk_count() should fail (no manifest).
        with pytest.raises(RuntimeError, match="manifest not loaded"):
            wd.chunk_count()

    def test_safe_when_directory_missing(self, tmp_path: Path):
        """invalidate does not raise when .chunks/ dir does not exist."""
        wd = WorkDir(tmp_path / "nonexistent.chunks")
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
        wd1 = WorkDir(tmp_path / "out.chunks")
        wd1.create_or_validate(**params)
        wd1.save_chunk(0, "chunk0", "ctx0", _make_usage(0))
        wd1.save_chunk(1, "chunk1", "ctx1", _make_usage(1))

        # Second "run": new WorkDir instance with same params.
        wd2 = WorkDir(tmp_path / "out.chunks")
        cached = wd2.create_or_validate(**params)

        assert sorted(cached) == [0, 1]
        assert wd2.load_chunk_markdown(0) == "chunk0"
        assert wd2.load_chunk_markdown(1) == "chunk1"

    def test_partial_resume(self, tmp_path: Path):
        """Resume after crash: only completed chunks are detected."""
        pdf = _make_pdf(tmp_path)
        params = _default_params(pdf)

        # Save only chunk 0 (chunk 1 "crashed").
        wd1 = WorkDir(tmp_path / "out.chunks")
        wd1.create_or_validate(**params)
        wd1.save_chunk(0, "chunk0", "ctx0", _make_usage(0))

        # Resume: only chunk 0 is cached.
        wd2 = WorkDir(tmp_path / "out.chunks")
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
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        manifest = wd.load_manifest()
        assert manifest is not None
        assert manifest.pages_per_chunk == 20
        assert manifest.total_pages == 40
        assert manifest.model_id == "claude-test-1"

    def test_returns_none_when_missing(self, tmp_path: Path):
        """load_manifest returns None when .chunks/ does not exist."""
        wd = WorkDir(tmp_path / "nonexistent.chunks")
        assert wd.load_manifest() is None

    def test_returns_none_when_corrupt(self, tmp_path: Path):
        """load_manifest returns None on corrupt manifest.json."""
        chunks_dir = tmp_path / "out.chunks"
        chunks_dir.mkdir()
        (chunks_dir / "manifest.json").write_text("not json!", encoding="utf-8")

        wd = WorkDir(chunks_dir)
        assert wd.load_manifest() is None

    def test_independent_of_internal_cache(self, tmp_path: Path):
        """load_manifest reads from disk, independent of _manifest cache."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        # Create a fresh WorkDir instance (no _manifest cached).
        wd2 = WorkDir(tmp_path / "out.chunks")
        manifest = wd2.load_manifest()
        assert manifest is not None
        assert manifest.num_chunks == 2


class TestChunkCount:
    """Tests for WorkDir.chunk_count()."""

    def test_returns_num_chunks(self, tmp_path: Path):
        """chunk_count returns num_chunks from the manifest."""
        pdf = _make_pdf(tmp_path)
        wd = WorkDir(tmp_path / "out.chunks")
        wd.create_or_validate(**_default_params(pdf))

        assert wd.chunk_count() == 2

    def test_raises_without_manifest(self, tmp_path: Path):
        """chunk_count raises if no manifest has been loaded."""
        wd = WorkDir(tmp_path / "nonexistent.chunks")
        with pytest.raises(RuntimeError, match="manifest not loaded"):
            wd.chunk_count()
