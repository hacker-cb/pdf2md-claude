"""Claude API client wrapper with retry logic and streaming support.

Provides a consistent interface for Claude API calls with automatic retry
on transient errors, streaming response handling, and prompt caching support.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import anthropic

from pdf2md_claude.models import ModelConfig

_log = logging.getLogger("claude_api")

# Retry configuration for transient API/network errors.
_DEFAULT_MAX_RETRIES = 10
"""Default maximum total attempts per request (1 = no retry)."""

_RETRY_MIN_DELAY_S = 1
"""Initial retry delay in seconds."""

_RETRY_MAX_DELAY_S = 30
"""Maximum retry delay in seconds (cap for exponential backoff)."""

_CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}
"""Anthropic prompt-caching control block (1-hour TTL)."""


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


@dataclass
class ApiResponse:
    """Raw response from a single Claude API call.

    Bundles the response text and token usage counts for easier handling
    by calling code.
    """

    markdown: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    stop_reason: str


class ClaudeApi:
    """Claude API client wrapper with retry and streaming support.

    Handles transport concerns (retry logic, streaming, caching) so that
    calling code can focus on building prompts and processing responses.

    Usage::

        api = ClaudeApi(client, model, use_cache=True, max_retries=10)
        
        # Build system prompt and messages
        system = "You are a helpful assistant."
        messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        
        # Send with automatic retry and streaming
        response = api.send_message(system, messages, retry_context="greeting")
        print(response.markdown)
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: ModelConfig,
        use_cache: bool = False,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        """Initialize the Claude API wrapper.

        Args:
            client: Authenticated Anthropic client.
            model: Model configuration (includes model_id, max_output_tokens).
            use_cache: Whether to enable prompt caching.
            max_retries: Maximum number of attempts per request (1 = no retry).
        """
        self._client = client
        self._model = model
        self._use_cache = use_cache
        self._max_retries = max_retries

    @property
    def model(self) -> ModelConfig:
        """The model configuration used by this API client."""
        return self._model

    def cached_block(self, block: dict) -> dict:
        """Add cache_control to a content block if caching is enabled.

        Args:
            block: Content block dict (must have "type" key).

        Returns:
            Block dict with cache_control added if caching is enabled,
            otherwise the original block unchanged.

        Example::

            # Build system prompt with caching
            system_block = api.cached_block({"type": "text", "text": prompt})
        """
        if self._use_cache:
            return {**block, "cache_control": _CACHE_CONTROL}
        return block

    def send_message(
        self,
        system: str,
        messages: list[dict],
        retry_context: str = "",
        thinking: dict | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
    ) -> ApiResponse:
        """Send a message to Claude with automatic retry and streaming.

        Combines the retry loop and streaming call into a single interface.
        Retries transient errors (network failures, rate limits, server errors)
        with exponential backoff. Non-retryable errors (auth, bad request,
        content filtering) are raised immediately.

        Args:
            system: System prompt text (cache_control is added automatically
                if caching is enabled).
            messages: List of message dicts (Anthropic messages API format).
            retry_context: Optional label for log messages (e.g. "pages 1-10").
            thinking: Optional thinking config dict (e.g. ``{"type": "adaptive"}``
                for Opus 4.6 or ``{"type": "enabled", "budget_tokens": 10000}``).
            on_thinking_delta: Optional callback invoked with each thinking chunk
                as it streams (only called when thinking is enabled).

        Returns:
            ApiResponse with markdown text, token counts, and stop reason.

        Raises:
            anthropic.APIError: On permanent API errors (auth, bad request, etc.).
            Exception: On non-retryable transport errors.
        """
        start = time.time()
        context_str = f" ({retry_context})" if retry_context else ""

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._stream_message(system, messages, thinking, on_thinking_delta)
                elapsed = time.time() - start
                _log.debug(
                    "API call%s: %.1fs, stop=%s",
                    context_str, elapsed, resp.stop_reason,
                )
                return resp
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
                    "API call%s: %s (attempt %d/%d, retrying in %.0fs)",
                    context_str,
                    f"{type(e).__name__}: {e}",
                    attempt, self._max_retries, delay,
                )
                time.sleep(delay)

        # Unreachable: loop always returns or raises
        raise AssertionError("Retry loop exited without returning or raising")

    def _stream_message(
        self,
        system: str,
        messages: list[dict],
        thinking: dict | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
    ) -> ApiResponse:
        """Send a message to Claude and stream the response.

        Uses streaming to avoid the 10-minute timeout limit imposed by the
        Anthropic SDK for large/slow requests (e.g., Opus models with PDF
        input).

        Args:
            system: System prompt text.
            messages: List of message dicts (Anthropic messages API format).
            thinking: Optional thinking config dict.
            on_thinking_delta: Optional callback invoked with each thinking chunk
                as it streams (only called when thinking is enabled).

        Returns:
            ApiResponse with markdown text, token counts, and stop reason.
        """
        # Build system prompt (with optional cache_control).
        system_block: dict = {"type": "text", "text": system}
        if self._use_cache:
            system_block["cache_control"] = _CACHE_CONTROL

        # Build API call kwargs.
        kwargs: dict = {
            "model": self._model.model_id,
            "max_tokens": self._model.max_output_tokens,
            "system": [system_block],
            "messages": messages,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking

        with self._client.messages.stream(**kwargs) as stream:
            # Stream events to capture thinking deltas if callback provided.
            if on_thinking_delta is not None and thinking is not None:
                for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "type") and event.delta.type == "thinking_delta":
                            on_thinking_delta(event.delta.thinking)
            
            message = stream.get_final_message()

        # Extract text content from response blocks.
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
