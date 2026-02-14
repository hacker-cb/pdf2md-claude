"""Core PDF → Markdown conversion logic using Claude's native PDF API.

Implements chunked conversion with context passing between chunks for
high-fidelity conversion of dense technical documents.
"""

from __future__ import annotations

import base64
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
import pymupdf

from pdf2md_claude.markers import PAGE_BEGIN, PAGE_END
from pdf2md_claude.models import ModelConfig, DocumentUsageStats, calculate_cost, fmt_duration
from pdf2md_claude.workdir import ChunkUsageStats, WorkDir
from pdf2md_claude.prompt import (
    CONTEXT_NOTE_END,
    CONTEXT_NOTE_MIDDLE,
    CONTEXT_NOTE_START,
    CONVERT_CHUNK_PROMPT,
    PREVIOUS_CONTEXT_BLOCK,
    SYSTEM_PROMPT,
)

_log = logging.getLogger("converter")

DEFAULT_PAGES_PER_CHUNK = 10
"""Default number of PDF pages per conversion chunk."""

# Context tail: pass at least N complete pages from the end of the
# previous chunk to the next chunk, helping Claude maintain continuity.
# If those N pages yield fewer than _CONTEXT_MIN_LINES, additional
# complete pages are included until the threshold is met.
_CONTEXT_MIN_PAGES = 3
_CONTEXT_MIN_LINES = 200

# Retry configuration for transient API/network errors.
_DEFAULT_MAX_RETRIES = 10
"""Default maximum total attempts per chunk (1 = no retry)."""

_RETRY_MIN_DELAY_S = 1
"""Initial retry delay in seconds."""

_RETRY_MAX_DELAY_S = 30
"""Maximum retry delay in seconds (cap for exponential backoff)."""


def _is_retryable(exc: BaseException) -> bool:
    """Classify whether an exception is transient and worth retrying.

    Returns ``True`` for network/transport errors and server-side failures
    that are likely to succeed on a subsequent attempt.  Returns ``False``
    for permanent client errors (bad request, auth, content filtering).

    Uses string-based type checking for ``httpcore``/``httpx`` transport
    errors to avoid adding a hard import dependency on ``httpcore``.
    """
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (429, 500, 502, 503, 529)
    # httpcore.RemoteProtocolError during streaming — not wrapped by SDK.
    type_name = type(exc).__name__
    return type_name in ("RemoteProtocolError", "ReadError", "ProtocolError")


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF file."""
    doc = pymupdf.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def extract_pdf_pages(pdf_path: Path, page_start: int, page_end: int) -> str:
    """Extract a page range from a PDF and return base64-encoded content.

    Args:
        pdf_path: Source PDF file.
        page_start: First page (1-indexed, PDF viewer numbering).
        page_end: Last page (inclusive).

    Returns:
        Base64-encoded PDF containing only the requested pages.
    """
    doc = pymupdf.open(str(pdf_path))
    try:
        total_pages = len(doc)
        start_idx = max(0, page_start - 1)  # Convert to 0-indexed
        end_idx = min(total_pages, page_end)

        doc.select(list(range(start_idx, end_idx)))
        pdf_bytes = doc.tobytes()

        actual_pages = end_idx - start_idx
        _log.debug(
            "    Extracted pages %d-%d (%d pages, %.0f KB)",
            page_start, page_end, actual_pages, len(pdf_bytes) / 1024,
        )

        return base64.standard_b64encode(pdf_bytes).decode("utf-8")
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------


@dataclass
class ChunkPlan:
    """A planned chunk of PDF pages to convert."""

    index: int  # 0-based chunk index
    page_start: int  # 1-indexed first page (inclusive)
    page_end: int  # 1-indexed last page (inclusive)
    is_first: bool
    is_last: bool

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


@dataclass
class ApiResponse:
    """Raw response from a single Claude API call.

    Replaces unnamed tuples returned by ``_send_to_claude`` and
    ``_convert_chunk``, making fields self-documenting and easy to extend.
    """

    markdown: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    stop_reason: str


@dataclass
class ChunkResult:
    """Result of converting a single PDF chunk.

    Bundles the raw markdown output, the context tail extracted for the
    next chunk, and per-chunk usage stats (via :class:`ChunkUsageStats`).
    """

    plan: ChunkPlan
    markdown: str
    context_tail: str  # extracted tail sent to next chunk
    usage: ChunkUsageStats


@dataclass
class ConversionResult:
    """Result of converting an entire PDF document.

    Returned by :meth:`PdfConverter.convert`.  Contains the per-chunk
    results, aggregated document-level stats, and counts of cached vs.
    fresh chunks.
    """

    chunks: list[ChunkResult]
    stats: DocumentUsageStats
    cached_chunks: int  # how many were loaded from disk cache
    fresh_chunks: int  # how many were converted via API this run


def plan_chunks(
    total_pages: int,
    pages_per_chunk: int,
) -> list[ChunkPlan]:
    """Plan chunk boundaries for disjoint page ranges.

    Each chunk gets exactly ``pages_per_chunk`` pages (the last chunk may
    have fewer).  Context passing between chunks handles continuity.

    Example with total_pages=88, pages_per_chunk=20::

        Chunk 0: pages  1-20
        Chunk 1: pages 21-40
        Chunk 2: pages 41-60
        Chunk 3: pages 61-80
        Chunk 4: pages 81-88

    Args:
        total_pages: Total number of pages in the PDF.
        pages_per_chunk: Target number of pages per chunk.

    Returns:
        List of ChunkPlan objects.
    """
    if total_pages <= pages_per_chunk:
        return [ChunkPlan(
            index=0, page_start=1, page_end=total_pages,
            is_first=True, is_last=True,
        )]

    chunks: list[ChunkPlan] = []
    page_start = 1
    idx = 0

    while page_start <= total_pages:
        page_end = min(page_start + pages_per_chunk - 1, total_pages)
        chunks.append(ChunkPlan(
            index=idx,
            page_start=page_start,
            page_end=page_end,
            is_first=(idx == 0),
            is_last=(page_end >= total_pages),
        ))
        if page_end >= total_pages:
            break
        page_start += pages_per_chunk
        idx += 1

    return chunks


_CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}
"""Anthropic prompt-caching control block (1-hour TTL)."""


def _get_context_tail(
    markdown: str,
    min_pages: int = _CONTEXT_MIN_PAGES,
    min_lines: int = _CONTEXT_MIN_LINES,
) -> str:
    """Extract the last N complete pages of markdown for context passing.

    Always returns whole pages (from PAGE_BEGIN to the end of the chunk).
    Guarantees at least ``min_pages`` pages; if that yields fewer than
    ``min_lines``, keeps adding one more complete page at a time until the
    threshold is met or there are no more pages.

    Falls back to the last ``min_lines`` lines if no page markers are found.
    """
    total_lines = markdown.count("\n") + 1

    # Find all PAGE_BEGIN positions.
    begin_positions = [
        m.start() for m in PAGE_BEGIN.re_value.finditer(markdown)
    ]
    if not begin_positions:
        # No page markers — fall back to line-based tail.
        lines = markdown.split("\n")
        tail = lines[-min_lines:] if len(lines) > min_lines else lines
        _log.debug(
            "    Context tail: no page markers, using last %d/%d lines",
            len(tail), total_lines,
        )
        return "\n".join(tail)

    # Start with at least min_pages pages.
    # Each "page" starts at a PAGE_BEGIN position.
    take = min(min_pages, len(begin_positions))
    while take < len(begin_positions):
        cut = begin_positions[-take]
        tail = markdown[cut:]
        if tail.count("\n") >= min_lines:
            break
        take += 1

    cut = begin_positions[-take]
    tail = markdown[cut:]
    tail_lines = tail.count("\n") + 1
    _log.debug(
        "    Context tail: %d/%d pages, %d/%d lines (min: %d pages, >=%d lines)",
        take, len(begin_positions), tail_lines, total_lines, min_pages, min_lines,
    )
    return tail


def _remap_page_markers(markdown: str, page_start: int) -> str:
    """Remap page markers from sub-PDF viewer numbers to original page numbers.

    When ``extract_pdf_pages`` creates a sub-PDF for a chunk, the viewer
    page numbers restart from 1. If Claude used those viewer numbers instead
    of the original document page numbers, this function detects the mismatch
    and remaps all markers by adding the appropriate offset.

    The detection heuristic: if the first marker's page number is less than
    ``page_start``, Claude used sub-PDF viewer numbering. The offset applied
    is ``page_start - 1`` (since viewer pages are 1-indexed).

    Args:
        markdown: Chunk markdown output (may contain page markers).
        page_start: The original PDF page number of the first page in this
            chunk (1-indexed).

    Returns:
        Markdown with remapped page markers (or unchanged if no remap needed).
    """
    markers = PAGE_BEGIN.re_value_groups.findall(markdown)
    if not markers:
        return markdown

    # markers is a list of (prefix, page_num_str, suffix) tuples.
    first_page = int(markers[0][1])

    if first_page >= page_start:
        # Markers already use original page numbers -- no remap needed.
        return markdown

    # Claude used sub-PDF viewer numbers. Remap with offset = page_start - 1.
    offset = page_start - 1
    _log.warning(
        "    Page markers appear to use sub-PDF numbering (first=%d, "
        "expected>=%d) — remapping with offset %+d",
        first_page, page_start, offset,
    )

    def _remap(match: re.Match) -> str:
        page_num = int(match.group(2)) + offset
        return f"{match.group(1)}{page_num}{match.group(3)}"

    # Remap BEGIN and END markers.
    # IMAGE_RECT no longer carries a page number — it derives the page
    # from the enclosing PAGE_BEGIN marker, so no remapping needed.
    result = PAGE_BEGIN.re_value_groups.sub(_remap, markdown)
    result = PAGE_END.re_value_groups.sub(_remap, result)
    return result


# ---------------------------------------------------------------------------
# PdfConverter class
# ---------------------------------------------------------------------------


class PdfConverter:
    """Chunked PDF-to-Markdown converter using Claude's native PDF API.

    Holds the API context (client, model, caching, system prompt) as
    instance state so that it does not need to be threaded through every
    internal method call.

    Usage::

        converter = PdfConverter(client, model, use_cache=True)
        result = converter.convert(pdf_path, work_dir, pages_per_chunk=10)
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: ModelConfig,
        use_cache: bool = False,
        system_prompt: str | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._client = client
        self._model = model
        self._use_cache = use_cache
        self._system_prompt = system_prompt
        self._max_retries = max_retries

    # -- public API --------------------------------------------------------

    def convert(
        self,
        pdf_path: Path,
        work_dir: WorkDir,
        pages_per_chunk: int,
        max_pages: int | None = None,
    ) -> ConversionResult:
        """Convert a PDF to Markdown via Claude's native PDF API.

        Chunked conversion with context passing between disjoint chunks.
        Splits the document into small chunks (``pages_per_chunk`` pages
        each).  Each chunk is persisted to disk immediately
        via ``work_dir``; on resume, already-cached chunks are skipped.

        Args:
            pdf_path: Path to the source PDF.
            work_dir: Work directory for chunk persistence and resume.
            pages_per_chunk: Number of PDF pages per conversion chunk.
                Must not exceed ``model.max_pdf_pages``.
            max_pages: Optional cap on total pages (from page 1).
                Useful for debugging (e.g., ``max_pages=5`` to test title
                extraction without converting all pages).

        Returns:
            ``ConversionResult`` with per-chunk results, aggregated stats,
            and cached/fresh chunk counts.
        """
        # Enforce API page limit: each chunk must fit within max_pdf_pages.
        if pages_per_chunk > self._model.max_pdf_pages:
            raise ValueError(
                f"pages_per_chunk ({pages_per_chunk}) exceeds API limit of "
                f"{self._model.max_pdf_pages} pages per request"
            )

        total_pages = get_pdf_page_count(pdf_path)

        # Cap total pages if requested (for debugging).
        if max_pages is not None and max_pages < total_pages:
            _log.info("  Limiting to first %d of %d pages (--max-pages)", max_pages, total_pages)
            total_pages = max_pages

        return self._convert_chunked(pdf_path, work_dir, total_pages, pages_per_chunk)

    # -- internal methods --------------------------------------------------

    def _convert_chunked(
        self,
        pdf_path: Path,
        work_dir: WorkDir,
        total_pages: int,
        pages_per_chunk: int,
    ) -> ConversionResult:
        """Convert a PDF by splitting into disjoint chunks with context passing.

        All cross-chunk state flows through ``work_dir`` on disk -- no
        ``prev_context`` variable or ``results`` accumulator is carried
        across loop iterations.  Each chunk is saved to disk immediately
        after conversion; on resume, cached chunks are skipped.
        """
        doc_name = pdf_path.stem
        chunks = plan_chunks(total_pages, pages_per_chunk)
        num_chunks = len(chunks)

        _log.info(
            "  Document has %d pages — splitting into %d chunks "
            "(%d pages/chunk)",
            total_pages, num_chunks, pages_per_chunk,
        )

        # Validate work directory manifest and discover cached chunks.
        work_dir.create_or_validate(
            pdf_path,
            total_pages=total_pages,
            pages_per_chunk=pages_per_chunk,
            max_pages=None,  # already applied above
            model_id=self._model.model_id,
            num_chunks=num_chunks,
        )

        # Conversion loop: no results list, no prev_context across iterations.
        cached_count = 0
        fresh_elapsed: list[float] = []  # for ETA display only
        conversion_start = time.time()

        for chunk in chunks:
            # 1. Check if chunk is cached on disk.
            if work_dir.has_chunk(chunk.index):
                cached_count += 1
                _log.info(
                    "  Chunk %d/%d: pages %d-%d (cached, skipping)",
                    chunk.index + 1, num_chunks,
                    chunk.page_start, chunk.page_end,
                )
                continue

            # Compute ETA from freshly-converted chunks only.
            if fresh_elapsed:
                elapsed = time.time() - conversion_start
                avg_time = sum(fresh_elapsed) / len(fresh_elapsed)
                remaining_fresh = (num_chunks - chunk.index - cached_count) * avg_time
                time_str = f" ({fmt_duration(elapsed)} elapsed, ETA ~{fmt_duration(remaining_fresh)})"
            else:
                time_str = ""

            _log.info(
                "  Chunk %d/%d: pages %d-%d (%d pages)%s...",
                chunk.index + 1, num_chunks,
                chunk.page_start, chunk.page_end, chunk.page_count,
                time_str,
            )

            # 2. Load prev_context from DISK (not from a variable).
            if chunk.index > 0:
                prev_context = work_dir.load_chunk_context(chunk.index - 1)
            else:
                prev_context = ""

            # Select context note based on chunk position.
            if chunk.is_first:
                context_note = CONTEXT_NOTE_START
            elif chunk.is_last:
                context_note = CONTEXT_NOTE_END
            else:
                context_note = CONTEXT_NOTE_MIDDLE

            # Build previous context block.
            if prev_context:
                previous_context_block = PREVIOUS_CONTEXT_BLOCK.format(
                    prev_context=prev_context,
                )
            else:
                previous_context_block = ""

            prompt = CONVERT_CHUNK_PROMPT.format(
                chunk_num=chunk.index + 1,
                total_chunks=num_chunks,
                context_note=context_note,
                previous_context_block=previous_context_block,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                page_count=chunk.page_count,
                page_start_plus_1=chunk.page_start + 1,
                page_start_plus_2=chunk.page_start + 2,
            )

            # 3. Convert via API.
            chunk_start = time.time()
            resp = self._convert_chunk(pdf_path, chunk, prompt)
            chunk_elapsed = time.time() - chunk_start

            # Remap page markers if Claude used sub-PDF viewer numbers.
            markdown = _remap_page_markers(resp.markdown, chunk.page_start)

            total_inp = (
                resp.input_tokens + resp.cache_creation_tokens + resp.cache_read_tokens
            )
            total_elapsed_so_far = time.time() - conversion_start
            if chunk.index > 0:
                time_done_str = (
                    f"{fmt_duration(chunk_elapsed)}, "
                    f"total {fmt_duration(total_elapsed_so_far)}"
                )
            else:
                time_done_str = fmt_duration(chunk_elapsed)
            _log.info(
                "  ✓ Chunk %d/%d done (%s) (%s input, %s output)",
                chunk.index + 1, num_chunks,
                time_done_str, f"{total_inp:,}", f"{resp.output_tokens:,}",
            )
            if resp.cache_creation_tokens or resp.cache_read_tokens:
                _log.info(
                    "    Cache: %s written, %s read",
                    f"{resp.cache_creation_tokens:,}",
                    f"{resp.cache_read_tokens:,}",
                )

            # Extract context tail for the next chunk.
            context_tail = _get_context_tail(markdown)

            # 4. Build typed ChunkUsageStats and save to disk IMMEDIATELY.
            usage = ChunkUsageStats(
                index=chunk.index,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cache_creation_tokens=resp.cache_creation_tokens,
                cache_read_tokens=resp.cache_read_tokens,
                cost=calculate_cost(
                    self._model, resp.input_tokens, resp.output_tokens,
                    resp.cache_creation_tokens, resp.cache_read_tokens,
                ),
                elapsed_seconds=chunk_elapsed,
            )
            work_dir.save_chunk(chunk.index, markdown, context_tail, usage)
            fresh_elapsed.append(chunk_elapsed)

        total_elapsed = time.time() - conversion_start

        # 5. Reconstruct all results from disk.
        results: list[ChunkResult] = []
        for chunk in chunks:
            results.append(ChunkResult(
                plan=chunk,
                markdown=work_dir.load_chunk_markdown(chunk.index),
                context_tail=work_dir.load_chunk_context(chunk.index),
                usage=work_dir.load_chunk_usage(chunk.index),
            ))

        # 6. Aggregate stats from disk and save stats.json.
        stats = DocumentUsageStats(
            doc_name=doc_name,
            pages=total_pages,
            chunks=num_chunks,
            input_tokens=sum(r.usage.input_tokens for r in results),
            output_tokens=sum(r.usage.output_tokens for r in results),
            cache_creation_tokens=sum(r.usage.cache_creation_tokens for r in results),
            cache_read_tokens=sum(r.usage.cache_read_tokens for r in results),
            cost=sum(r.usage.cost for r in results),
            elapsed_seconds=total_elapsed,
        )
        work_dir.save_stats(stats)

        # Log document-level totals.
        has_cache = stats.cache_creation_tokens > 0 or stats.cache_read_tokens > 0
        if has_cache:
            _log.info(
                "  Conversion done: %s input (%s cache-write, %s cache-read) "
                "+ %s output tokens, cost $%.2f, time %s",
                f"{stats.total_input_tokens:,}",
                f"{stats.cache_creation_tokens:,}",
                f"{stats.cache_read_tokens:,}",
                f"{stats.output_tokens:,}",
                stats.cost,
                fmt_duration(stats.elapsed_seconds),
            )
        else:
            _log.info(
                "  Conversion done: %s input + %s output tokens, "
                "cost $%.2f, time %s",
                f"{stats.total_input_tokens:,}",
                f"{stats.output_tokens:,}",
                stats.cost,
                fmt_duration(stats.elapsed_seconds),
            )

        fresh_count = num_chunks - cached_count
        if cached_count > 0:
            _log.info(
                "  Chunks: %d fresh, %d cached, %d total",
                fresh_count, cached_count, num_chunks,
            )

        return ConversionResult(
            chunks=results,
            stats=stats,
            cached_chunks=cached_count,
            fresh_chunks=fresh_count,
        )

    def _convert_chunk(
        self,
        pdf_path: Path,
        chunk: ChunkPlan,
        prompt: str,
    ) -> ApiResponse:
        """Convert a single chunk of PDF pages to Markdown.

        Retries transient API/network errors up to ``self._max_retries``
        total attempts with exponential backoff (1-30 s).  Non-retryable
        errors (auth, content filtering, max_tokens) are raised immediately.

        Returns:
            :class:`ApiResponse` with markdown text and token usage.

        Raises:
            RuntimeError: If the output is truncated (hit max_tokens).
        """
        pdf_b64 = extract_pdf_pages(pdf_path, chunk.page_start, chunk.page_end)

        start = time.time()
        resp: ApiResponse | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._send_to_claude(pdf_b64, prompt)
                break
            except Exception as e:
                if not _is_retryable(e) or attempt == self._max_retries:
                    raise
                # Exponential backoff: 1, 2, 4, 8, 16, 30, 30, ... capped.
                base = min(
                    _RETRY_MIN_DELAY_S * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_S,
                )
                delay = base + random.uniform(0, base * 0.25)
                _log.warning(
                    "    Chunk pages %d-%d: %s (attempt %d/%d, retrying in %.0fs)",
                    chunk.page_start, chunk.page_end,
                    f"{type(e).__name__}: {e}",
                    attempt, self._max_retries, delay,
                )
                time.sleep(delay)
        assert resp is not None  # unreachable: loop always breaks or raises
        elapsed = time.time() - start

        _log.debug(
            "    Chunk pages %d-%d: %.1fs, stop=%s",
            chunk.page_start, chunk.page_end, elapsed, resp.stop_reason,
        )

        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Chunk pages {chunk.page_start}-{chunk.page_end} truncated "
                f"(hit {self._model.max_output_tokens} max_tokens after {elapsed:.1f}s). "
                f"Try reducing --pages-per-chunk (currently {chunk.page_count})."
            )

        return resp

    def _send_to_claude(
        self,
        pdf_b64: str,
        user_prompt: str,
    ) -> ApiResponse:
        """Send a base64-encoded PDF to Claude and return the response.

        Uses streaming to avoid the 10-minute timeout limit imposed by the
        Anthropic SDK for large/slow requests (e.g., Opus models with PDF
        input).

        Returns:
            :class:`ApiResponse` with markdown text, token counts, and
            stop reason.
        """
        # Build system prompt (with optional cache_control).
        sys_text = (
            self._system_prompt
            if self._system_prompt is not None
            else SYSTEM_PROMPT
        )
        system_block: dict = {"type": "text", "text": sys_text}
        if self._use_cache:
            system_block["cache_control"] = _CACHE_CONTROL

        # Build PDF document block (with optional cache_control).
        doc_block: dict = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_b64,
            },
        }
        if self._use_cache:
            doc_block["cache_control"] = _CACHE_CONTROL

        with self._client.messages.stream(
            model=self._model.model_id,
            max_tokens=self._model.max_output_tokens,
            system=[system_block],
            messages=[
                {
                    "role": "user",
                    "content": [
                        doc_block,
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            ],
        ) as stream:
            message = stream.get_final_message()

        markdown = ""
        for block in message.content:
            if block.type == "text":
                markdown += block.text

        # Extract cache token counts (may be 0 or absent when caching is off).
        cache_creation = getattr(message.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(message.usage, "cache_read_input_tokens", 0) or 0

        return ApiResponse(
            markdown=markdown,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            stop_reason=message.stop_reason,
        )
