"""Model configurations, pricing, and usage tracking for Claude PDF conversion.

References:
  - Models overview: https://platform.claude.com/docs/en/about-claude/models/overview#latest-models-comparison
  - Model pricing:   https://platform.claude.com/docs/en/about-claude/pricing#model-pricing
  - Long context:    https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing
Last verified: 2026-02-08
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelPricing:
    """Pricing tiers for a Claude model (USD per million tokens).

    Cache pricing uses multipliers applied to the effective input rate
    (which may be base or long-context depending on total input tokens).

    References:
      - Model pricing: https://platform.claude.com/docs/en/about-claude/pricing#model-pricing
      - Long context: https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing
    """

    # Base rates (model pricing table)
    input_per_mtok: float
    output_per_mtok: float
    # Premium rates for >long_ctx_threshold input tokens (long context pricing table)
    long_ctx_input_per_mtok: float
    long_ctx_output_per_mtok: float
    long_ctx_threshold: int  # input token count above which long-context pricing applies
    # Cache multipliers applied to the effective input rate (model pricing table)
    cache_write_multiplier: float = 2.0  # 1h TTL: 2x base input rate
    cache_read_multiplier: float = 0.1  # cache hit: 0.1x base input rate


@dataclass(frozen=True)
class ModelConfig:
    """Complete configuration for a Claude model."""

    model_id: str
    display_name: str
    max_output_tokens: int
    max_context_tokens: int
    max_pdf_pages: int  # Hard API limit per request (100 for all current models)
    pricing: ModelPricing
    beta_header: str | None = None


@dataclass
class DocumentUsageStats:
    """Token usage statistics for a single document conversion.

    ``cost`` is accumulated per-request to avoid the long-context pricing
    bug where aggregate totals across chunks would incorrectly exceed the
    200K threshold.  Use ``cost`` in summaries instead of recalculating
    from aggregate token counts.
    """

    doc_name: str
    pages: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost: float = 0.0  # accumulated per-request USD cost
    chunks: int = 1
    elapsed_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        """Total tokens (all input including cache + output)."""
        return self.total_input_tokens + self.output_tokens

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens including cache write/read."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# Models: https://platform.claude.com/docs/en/about-claude/models/overview#latest-models-comparison
# Pricing: https://platform.claude.com/docs/en/about-claude/pricing#model-pricing
# Long context: https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing
OPUS_4_6 = ModelConfig(
    model_id="claude-opus-4-6",
    display_name="Claude Opus 4.6",
    max_output_tokens=64_000,
    max_context_tokens=1_000_000,
    max_pdf_pages=100,
    beta_header="context-1m-2025-08-07",
    pricing=ModelPricing(
        input_per_mtok=5.0,       # $5 / MTok
        output_per_mtok=25.0,     # $25 / MTok
        long_ctx_input_per_mtok=10.0,   # $10 / MTok (>200K input)
        long_ctx_output_per_mtok=37.5,  # $37.50 / MTok (>200K input)
        long_ctx_threshold=200_000,
    ),
)

SONNET_4_5 = ModelConfig(
    model_id="claude-sonnet-4-5",
    display_name="Claude Sonnet 4.5",
    max_output_tokens=64_000,
    max_context_tokens=1_000_000,
    max_pdf_pages=100,
    beta_header="context-1m-2025-08-07",
    pricing=ModelPricing(
        input_per_mtok=3.0,       # $3 / MTok
        output_per_mtok=15.0,     # $15 / MTok
        long_ctx_input_per_mtok=6.0,    # $6 / MTok (>200K input)
        long_ctx_output_per_mtok=22.5,  # $22.50 / MTok (>200K input)
        long_ctx_threshold=200_000,
    ),
)

HAIKU_4_5 = ModelConfig(
    model_id="claude-haiku-4-5",
    display_name="Claude Haiku 4.5",
    max_output_tokens=64_000,
    max_context_tokens=200_000,
    max_pdf_pages=100,
    beta_header=None,  # no 1M context support
    pricing=ModelPricing(
        input_per_mtok=1.0,       # $1 / MTok
        output_per_mtok=5.0,      # $5 / MTok
        long_ctx_input_per_mtok=1.0,    # N/A -- same as base (200K max)
        long_ctx_output_per_mtok=5.0,   # N/A -- same as base (200K max)
        long_ctx_threshold=200_000,
    ),
)

MODELS: dict[str, ModelConfig] = {
    "opus": OPUS_4_6,
    "sonnet": SONNET_4_5,
    "haiku": HAIKU_4_5,
}


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def calculate_cost(
    model: ModelConfig,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calculate USD cost for a **single API request**.

    The long-context threshold is evaluated per-request: if the total
    input tokens (uncached + cache-write + cache-read) for this request
    exceed the threshold, premium rates apply to ALL tokens.
    See: https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing

    Cache pricing multipliers stack on top of the effective input rate
    (base or long-context).
    See: https://platform.claude.com/docs/en/about-claude/pricing#model-pricing

    Args:
        model: Model configuration with pricing info.
        input_tokens: Uncached input tokens (after last cache breakpoint).
        output_tokens: Output tokens.
        cache_creation_tokens: Tokens written to cache this request.
        cache_read_tokens: Tokens read from cache this request.
    """
    p = model.pricing
    total_input = input_tokens + cache_creation_tokens + cache_read_tokens

    if total_input > p.long_ctx_threshold:
        base_input_rate = p.long_ctx_input_per_mtok
        output_rate = p.long_ctx_output_per_mtok
    else:
        base_input_rate = p.input_per_mtok
        output_rate = p.output_per_mtok

    input_cost = input_tokens * base_input_rate / 1_000_000
    cache_write_cost = (
        cache_creation_tokens * base_input_rate * p.cache_write_multiplier / 1_000_000
    )
    cache_read_cost = (
        cache_read_tokens * base_input_rate * p.cache_read_multiplier / 1_000_000
    )
    output_cost = output_tokens * output_rate / 1_000_000

    return input_cost + cache_write_cost + cache_read_cost + output_cost


def fmt_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples: ``"45s"``, ``"2m 15s"``, ``"1h 3m 12s"``.
    """
    if seconds < 0:
        return "0s"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def format_summary(model: ModelConfig, stats: list[DocumentUsageStats]) -> str:
    """Format a summary table of token usage and costs across all documents.

    Uses the per-request accumulated ``cost`` field from each
    ``DocumentUsageStats`` instead of recalculating from aggregate token
    totals (which would incorrectly trigger long-context pricing for
    multi-chunk conversions).
    """
    p = model.pricing
    lines = [
        f"Model: {model.display_name} ({model.model_id})",
        f"Pricing: ${p.input_per_mtok}/MTok input, "
        f"${p.output_per_mtok}/MTok output"
        f" (long-ctx: ${p.long_ctx_input_per_mtok}/"
        f"${p.long_ctx_output_per_mtok} above "
        f"{p.long_ctx_threshold:,} tokens)",
    ]

    # Check if any caching was used across all documents.
    has_cache = any(
        s.cache_creation_tokens > 0 or s.cache_read_tokens > 0 for s in stats
    )
    if has_cache:
        lines.append(
            f"Cache: write {p.cache_write_multiplier}x input, "
            f"read {p.cache_read_multiplier}x input (1h TTL)"
        )

    lines.append("")
    if has_cache:
        lines.append(
            f"{'Document':<30s} {'Pages':>5s} {'Input':>9s} "
            f"{'CacheWr':>9s} {'CacheRd':>9s} {'Output':>9s} "
            f"{'Time':>8s} {'Cost':>8s}"
        )
        lines.append("-" * 100)
    else:
        lines.append(
            f"{'Document':<35s} {'Pages':>5s} {'Input':>10s} {'Output':>10s} "
            f"{'Time':>10s} {'Cost':>9s}"
        )
        lines.append("-" * 85)

    total_pages = 0
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    total_cost = 0.0
    total_elapsed = 0.0

    for s in stats:
        cost = s.cost
        if has_cache:
            lines.append(
                f"{s.doc_name:<30s} {s.pages:>5d} {s.total_input_tokens:>9,} "
                f"{s.cache_creation_tokens:>9,} {s.cache_read_tokens:>9,} "
                f"{s.output_tokens:>9,} "
                f"{fmt_duration(s.elapsed_seconds):>8s} ${cost:>6.2f}"
            )
        else:
            lines.append(
                f"{s.doc_name:<35s} {s.pages:>5d} {s.total_input_tokens:>10,} "
                f"{s.output_tokens:>10,} {fmt_duration(s.elapsed_seconds):>10s} "
                f"${cost:>7.2f}"
            )
        total_pages += s.pages
        total_input += s.total_input_tokens
        total_output += s.output_tokens
        total_cache_creation += s.cache_creation_tokens
        total_cache_read += s.cache_read_tokens
        total_cost += cost
        total_elapsed += s.elapsed_seconds

    if has_cache:
        lines.append("-" * 100)
        lines.append(
            f"{'TOTAL':<30s} {total_pages:>5d} {total_input:>9,} "
            f"{total_cache_creation:>9,} {total_cache_read:>9,} "
            f"{total_output:>9,} "
            f"{fmt_duration(total_elapsed):>8s} ${total_cost:>6.2f}"
        )
    else:
        lines.append("-" * 85)
        lines.append(
            f"{'TOTAL':<35s} {total_pages:>5d} {total_input:>10,} "
            f"{total_output:>10,} {fmt_duration(total_elapsed):>10s} "
            f"${total_cost:>7.2f}"
        )

    return "\n".join(lines)
