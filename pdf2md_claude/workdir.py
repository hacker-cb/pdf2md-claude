"""Work directory for chunked PDF conversion with resume support.

Manages a ``.staging/`` directory alongside the output file, persisting
each chunk's markdown, context tail, and usage stats to disk immediately
after conversion.  On resume, already-converted chunks are skipped.

Staleness detection: a ``manifest.json`` records the conversion
parameters.  If any parameter changes between runs, all cached chunks
are invalidated.

Also manages a ``table_fixer/`` subdirectory for persisting table
regeneration results (per-table metadata, before/after HTML, aggregate stats).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from pdf2md_claude.models import DocumentUsageStats, StageCost

_log = logging.getLogger("workdir")


# ---------------------------------------------------------------------------
# Dataclasses (serialized to JSON)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    """Conversion parameters recorded for staleness detection.

    If any field differs between runs, all cached chunks are invalidated.
    Serialized to ``manifest.json`` in the work directory.
    """

    pdf_mtime: float
    pdf_size: int
    total_pages: int
    pages_per_chunk: int
    max_pages: int | None
    model_id: str
    num_chunks: int


@dataclass
class ChunkUsageStats:
    """Per-chunk token usage, cost, and timing.

    Serialized to ``chunk_NN_meta.json`` in the work directory.
    ``cost`` is computed at save time via ``calculate_cost()`` so that
    each chunk's metadata is self-contained (no model config needed
    to reconstruct costs later).
    """

    index: int  # 0-based chunk index
    page_start: int  # 1-indexed first page
    page_end: int  # 1-indexed last page (inclusive)
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float  # USD cost for this chunk
    elapsed_seconds: float


@dataclass
class TableFixResult:
    """Per-table regeneration result metadata.

    Serialized to ``table_fixer/pNNN-NNN_label.json`` in the work directory.
    Companion HTML files (``_before.html``, ``_after.html``) contain the
    original broken and regenerated table HTML.
    """

    index: int
    """0-based table index (order of detection)."""

    label: str
    """Human-readable label (e.g. ``"Table 3"``)."""

    page_numbers: list[int]
    """PDF page numbers this table spans."""

    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float
    """USD cost for regenerating this table."""

    elapsed_seconds: float
    """Time spent regenerating this table."""

    before_chars: int
    """Character count of original broken HTML."""

    after_chars: int
    """Character count of regenerated HTML."""


@dataclass
class TableFixStats:
    """Aggregate stats for all table regenerations in a document.

    Serialized to ``table_fixer/stats.json`` in the work directory.
    """

    tables_found: int
    """Total number of broken tables detected."""

    tables_fixed: int
    """Number successfully regenerated."""

    total_input_tokens: int
    total_output_tokens: int
    total_cost: float
    """Accumulated USD cost for all table fixes."""

    total_elapsed_seconds: float
    """Total time spent on all table regenerations."""

    input_hash: str = ""
    """SHA256 hash of the input markdown (for cache validation)."""


# ---------------------------------------------------------------------------
# WorkDir
# ---------------------------------------------------------------------------


class WorkDir:
    """Manages a ``.staging/`` work directory for chunked conversion.

    Handles manifest validation, per-chunk save/load, and aggregated
    stats persistence.  All cross-chunk data flows through the
    filesystem -- never held in memory across loop iterations.
    """

    _MANIFEST_FILE = "manifest.json"
    _STATS_FILE = "stats.json"
    _CHUNKS_SUBDIR = "chunks"
    _MERGED_FILE = "merged.md"
    _TABLE_FIXER_SUBDIR = "table_fixer"
    _TABLE_FIXER_OUTPUT = "output.md"

    def __init__(self, path: Path) -> None:
        """Wrap a ``.staging/`` directory path.

        The directory is not created until :meth:`create_or_validate`
        is called.

        Args:
            path: Path to the ``.staging/`` directory.
        """
        self._path = path
        self._chunks_path = path / self._CHUNKS_SUBDIR
        self._table_fixer_path = path / self._TABLE_FIXER_SUBDIR
        self._manifest: Manifest | None = None

    @property
    def path(self) -> Path:
        """Path to the ``.staging/`` directory."""
        return self._path

    @property
    def table_fixer_path(self) -> Path:
        """Path to the ``table_fixer/`` subdirectory."""
        return self._table_fixer_path

    # -- Naming helpers (1-indexed, zero-padded) ----------------------------

    def _chunk_md(self, index: int) -> Path:
        return self._chunks_path / f"chunk_{index + 1:02d}.md"

    def _chunk_context(self, index: int) -> Path:
        return self._chunks_path / f"chunk_{index + 1:02d}_context.md"

    def _chunk_meta(self, index: int) -> Path:
        return self._chunks_path / f"chunk_{index + 1:02d}_meta.json"

    # -- Manifest -----------------------------------------------------------

    def create_or_validate(
        self,
        pdf_path: Path,
        total_pages: int,
        pages_per_chunk: int,
        max_pages: int | None,
        model_id: str,
        num_chunks: int,
    ) -> list[int]:
        """Create or validate the work directory and manifest.

        If the directory does not exist, creates it and writes a fresh
        manifest.  If it exists and the manifest matches the current
        parameters, returns the list of already-cached chunk indices.
        If the manifest differs, invalidates all chunks and rewrites
        the manifest.

        Args:
            pdf_path: Source PDF (used for mtime/size).
            total_pages: Total pages to convert.
            pages_per_chunk: Pages per chunk.
            max_pages: Optional page cap (``None`` = all).
            model_id: Model identifier string.
            num_chunks: Expected number of chunks.

        Returns:
            List of 0-based chunk indices that are already cached.
        """
        stat = pdf_path.stat()
        new_manifest = Manifest(
            pdf_mtime=stat.st_mtime,
            pdf_size=stat.st_size,
            total_pages=total_pages,
            pages_per_chunk=pages_per_chunk,
            max_pages=max_pages,
            model_id=model_id,
            num_chunks=num_chunks,
        )

        self._path.mkdir(parents=True, exist_ok=True)
        self._chunks_path.mkdir(exist_ok=True)
        manifest_file = self._path / self._MANIFEST_FILE

        if manifest_file.exists():
            existing = self._read_manifest(manifest_file)
            if existing == new_manifest:
                # Manifest matches -- find cached chunks.
                self._manifest = existing
                cached = [
                    i for i in range(num_chunks) if self.has_chunk(i)
                ]
                if cached:
                    _log.info(
                        "  WorkDir: %d/%d chunks cached in %s",
                        len(cached), num_chunks, self._path,
                    )
                return cached
            else:
                # Parameters changed -- invalidate everything.
                _log.warning(
                    "  WorkDir: manifest mismatch, invalidating %s",
                    self._path,
                )
                self.invalidate()

        # Write fresh manifest.
        self._write_manifest(manifest_file, new_manifest)
        self._manifest = new_manifest
        return []

    @staticmethod
    def _read_manifest(path: Path) -> Manifest:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Manifest(**data)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise RuntimeError(
                f"Corrupt manifest in {path}. "
                f"Re-run with -f (--force) to rebuild: {exc}"
            ) from exc

    @staticmethod
    def _write_manifest(path: Path, manifest: Manifest) -> None:
        path.write_text(
            json.dumps(asdict(manifest), indent=2) + "\n",
            encoding="utf-8",
        )

    # -- Chunk I/O ----------------------------------------------------------

    def save_chunk(
        self,
        index: int,
        markdown: str,
        context_tail: str,
        usage: ChunkUsageStats,
    ) -> None:
        """Persist a converted chunk to disk.

        Writes files in order: ``_context.md`` -> ``.md`` ->
        ``_meta.json``.  The meta file is written **last** so that
        :meth:`has_chunk` (which checks meta existence) only returns
        ``True`` for fully-written chunks.

        Args:
            index: 0-based chunk index.
            markdown: Raw markdown output (post-remap).
            context_tail: Context tail for the next chunk.
            usage: Per-chunk usage stats.
        """
        self._chunk_context(index).write_text(context_tail, encoding="utf-8")
        self._chunk_md(index).write_text(markdown, encoding="utf-8")
        self._chunk_meta(index).write_text(
            json.dumps(asdict(usage), indent=2) + "\n",
            encoding="utf-8",
        )

    def load_chunk_markdown(self, index: int) -> str:
        """Read the raw markdown for a chunk.

        Args:
            index: 0-based chunk index.

        Returns:
            Markdown content.
        """
        return self._chunk_md(index).read_text(encoding="utf-8")

    def load_chunk_context(self, index: int) -> str:
        """Read the context tail for a chunk.

        Args:
            index: 0-based chunk index.

        Returns:
            Context tail string, or ``""`` if the file does not exist.
        """
        path = self._chunk_context(index)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def load_chunk_usage(self, index: int) -> ChunkUsageStats:
        """Read and deserialize the usage stats for a chunk.

        Args:
            index: 0-based chunk index.

        Returns:
            ``ChunkUsageStats`` instance.

        Raises:
            RuntimeError: If the meta file is corrupt or has unexpected keys.
        """
        path = self._chunk_meta(index)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ChunkUsageStats(**data)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise RuntimeError(
                f"Corrupt chunk metadata in {path}. "
                f"Re-run with -f (--force) to rebuild: {exc}"
            ) from exc

    def has_chunk(self, index: int) -> bool:
        """Check whether a chunk has been fully written.

        Checks for the presence of ``chunk_NN_meta.json``, which is
        written last during :meth:`save_chunk`.

        Args:
            index: 0-based chunk index.
        """
        return self._chunk_meta(index).exists()

    # -- Stats I/O ----------------------------------------------------------

    def save_stats(self, stats: DocumentUsageStats) -> None:
        """Write aggregated document usage stats to ``stats.json``.

        Args:
            stats: Aggregated usage stats for the full document.
        
        Note:
            Stages are persisted separately in ``table_fixer/stats.json``
            and excluded from this file to prevent double-counting.
        """
        path = self._chunks_path / self._STATS_FILE
        data = asdict(stats)
        data.pop("stages", None)  # stages are persisted separately
        path.write_text(
            json.dumps(data, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_stats(self) -> DocumentUsageStats | None:
        """Read aggregated document usage stats from ``stats.json``.

        Returns:
            ``DocumentUsageStats`` instance, or ``None`` if the file
            does not exist or is corrupt (returns ``None`` on error).
        """
        path = self._chunks_path / self._STATS_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return DocumentUsageStats(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            _log.warning("Corrupt stats file %s — ignoring", path)
            return None

    def load_combined_stats(self) -> DocumentUsageStats | None:
        """Load chunk stats + table-fixer stats as a combined DocumentUsageStats.

        Loads ``chunks/stats.json`` for base conversion costs, then appends
        ``table_fixer/stats.json`` as a :class:`StageCost` entry if present.

        Returns:
            ``DocumentUsageStats`` with stages populated, or ``None`` if
            ``chunks/stats.json`` does not exist.
        """
        stats = self.load_stats()
        if stats is None:
            return None

        tf_stats = self.load_table_fix_stats()
        if tf_stats is not None and tf_stats.tables_fixed > 0:
            stats.stages.append(StageCost(
                name="table fixes",
                input_tokens=tf_stats.total_input_tokens,
                output_tokens=tf_stats.total_output_tokens,
                cost=tf_stats.total_cost,
                elapsed_seconds=tf_stats.total_elapsed_seconds,
                detail=f"{tf_stats.tables_fixed} tables",
            ))
        return stats

    # -- Housekeeping -------------------------------------------------------

    def invalidate(self) -> None:
        """Remove all contents of the work directory.

        Deletes everything (chunks, stats, manifest) and recreates the
        empty directory.  Safe to call even if the directory does not
        exist yet.
        """
        if not self._path.exists():
            return
        shutil.rmtree(self._path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._chunks_path.mkdir(exist_ok=True)
        self._manifest = None

    def clear_table_fixer(self) -> None:
        """Remove and recreate the table_fixer subdirectory.

        Clears all per-table result files and aggregate stats from previous
        runs. Safe to call even if the directory does not exist yet.
        """
        if self._table_fixer_path.exists():
            shutil.rmtree(self._table_fixer_path)
        self._table_fixer_path.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> Manifest | None:
        """Read the manifest from disk if it exists.

        Returns ``None`` if the manifest file is missing or corrupt.
        Unlike :meth:`_load_manifest`, this method never raises.
        """
        path = self._path / self._MANIFEST_FILE
        if not path.exists():
            return None
        try:
            return self._read_manifest(path)
        except RuntimeError:
            return None

    def _load_manifest(self) -> Manifest:
        """Lazy-load the manifest from disk.

        Raises:
            RuntimeError: If the manifest file does not exist on disk.
        """
        if self._manifest is None:
            manifest_file = self._path / self._MANIFEST_FILE
            if manifest_file.exists():
                self._manifest = self._read_manifest(manifest_file)
            else:
                raise RuntimeError(
                    "WorkDir manifest not loaded; call create_or_validate() first"
                )
        return self._manifest

    def chunk_count(self) -> int:
        """Return the expected number of chunks from the manifest.

        Lazy-loads the manifest from disk if it has not been loaded yet.

        Raises:
            RuntimeError: If the manifest file does not exist on disk.
        """
        return self._load_manifest().num_chunks

    def total_pages(self) -> int:
        """Return the total page count from the manifest.

        Lazy-loads the manifest from disk if it has not been loaded yet.

        Raises:
            RuntimeError: If the manifest file does not exist on disk.
        """
        return self._load_manifest().total_pages

    # -- Phase output -------------------------------------------------------

    def save_output(self, markdown: str) -> None:
        """Write the merged phase output to ``merged.md``."""
        path = self._path / self._MERGED_FILE
        path.write_text(markdown, encoding="utf-8")

    def load_output(self) -> str | None:
        """Read the phase output if it exists."""
        path = self._path / self._MERGED_FILE
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # -- Table fixer I/O ----------------------------------------------------

    @staticmethod
    def _build_table_fix_prefix(page_numbers: list[int], label: str) -> str:
        """Build filename prefix from page range and label.

        Args:
            page_numbers: List of page numbers the table spans (must be non-empty).
            label: Human-readable label (e.g. ``"Table 3"``).

        Returns:
            Filename prefix like ``p001-001_table_3`` or ``p003-006_table_23``.

        Raises:
            ValueError: If page_numbers is empty.
        """
        if not page_numbers:
            raise ValueError("page_numbers must not be empty")
        min_page = min(page_numbers)
        max_page = max(page_numbers)
        page_prefix = f"p{min_page:03d}-{max_page:03d}"
        
        # Sanitize label: lowercase, spaces to underscores
        sanitized_label = label.lower().replace(" ", "_").replace("–", "-").replace("—", "-")
        
        return f"{page_prefix}_{sanitized_label}"

    def save_table_fix(
        self,
        result: TableFixResult,
        before_html: str,
        after_html: str,
    ) -> None:
        """Persist a table regeneration result to disk.

        Writes three files:
        - ``pNNN-NNN_label.json`` -- metadata
        - ``pNNN-NNN_label_before.html`` -- original broken HTML
        - ``pNNN-NNN_label_after.html`` -- regenerated HTML

        Args:
            result: Table fix metadata.
            before_html: Original broken table HTML.
            after_html: Regenerated table HTML.
        """
        self._table_fixer_path.mkdir(parents=True, exist_ok=True)
        
        prefix = self._build_table_fix_prefix(result.page_numbers, result.label)
        
        # Write files
        (self._table_fixer_path / f"{prefix}.json").write_text(
            json.dumps(asdict(result), indent=2) + "\n",
            encoding="utf-8",
        )
        (self._table_fixer_path / f"{prefix}_before.html").write_text(
            before_html,
            encoding="utf-8",
        )
        (self._table_fixer_path / f"{prefix}_after.html").write_text(
            after_html,
            encoding="utf-8",
        )

    def save_table_fix_stats(self, stats: TableFixStats) -> None:
        """Write aggregate table fix stats to ``table_fixer/stats.json``.

        Args:
            stats: Aggregate stats for all table regenerations.
        """
        self._table_fixer_path.mkdir(parents=True, exist_ok=True)
        path = self._table_fixer_path / self._STATS_FILE
        path.write_text(
            json.dumps(asdict(stats), indent=2) + "\n",
            encoding="utf-8",
        )

    def load_table_fix_stats(self) -> TableFixStats | None:
        """Read aggregate table fix stats from ``table_fixer/stats.json``.

        Returns:
            ``TableFixStats`` instance, or ``None`` if the file does not
            exist or is corrupt (returns ``None`` on error).
        """
        path = self._table_fixer_path / self._STATS_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TableFixStats(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            _log.warning("Corrupt table fix stats file %s — ignoring", path)
            return None

    @staticmethod
    def content_hash(paths: list[Path]) -> str:
        """Compute SHA256 hex digest over sorted file contents.

        Files are processed in sorted order for determinism.
        Returns empty string if no files match or list is empty.

        Args:
            paths: List of file paths to hash.

        Returns:
            SHA256 hex digest, or ``""`` if paths is empty.
        """
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(p.read_bytes())
        return h.hexdigest() if paths else ""

    def content_hash_glob(self, *patterns: str) -> str:
        """Compute SHA256 over files matching glob patterns within staging dir.

        Args:
            patterns: Glob patterns relative to the staging directory.

        Returns:
            SHA256 hex digest of all matching files, or ``""`` if no matches.

        Example::

            work_dir.content_hash_glob("merged.md")
            work_dir.content_hash_glob("chunks/*.md")
        """
        paths: list[Path] = []
        for pattern in patterns:
            paths.extend(self._path.glob(pattern))
        return self.content_hash(paths)

    def save_table_fixer_output(self, markdown: str) -> None:
        """Write post-table-fix markdown to ``table_fixer/output.md``.

        Args:
            markdown: Markdown content after table fixes have been applied.
        """
        self._table_fixer_path.mkdir(parents=True, exist_ok=True)
        (self._table_fixer_path / self._TABLE_FIXER_OUTPUT).write_text(
            markdown, encoding="utf-8",
        )

    def load_table_fixer_output(self) -> str | None:
        """Read cached post-table-fix markdown from ``table_fixer/output.md``.

        Returns:
            Cached markdown content, or ``None`` if the file does not exist.
        """
        path = self._table_fixer_path / self._TABLE_FIXER_OUTPUT
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
