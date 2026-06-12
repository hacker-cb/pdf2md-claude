"""Claude CLI backend — run conversions through the ``claude -p`` headless mode.

This is an alternative to :class:`pdf2md_claude.claude_api.ClaudeApi` that
does not need ``PDF2MD_CLAUDE_API_KEY`` (or the legacy ``ANTHROPIC_API_KEY``).
Instead it shells out to a locally installed
`Claude Code <https://claude.com/claude-code>`_ CLI in non-interactive print
mode, which authenticates with whatever credentials ``claude`` already uses
(typically a Claude subscription via OAuth).

The PDF content blocks that pdf2md-claude builds are forwarded verbatim to the
model by feeding them to ``claude`` over ``--input-format stream-json`` — the
CLI passes ``document``/``text`` content blocks straight through to the
Messages API, so conversion behaviour matches the direct-SDK path.

The class mirrors the small surface that the rest of the package relies on:
``model`` (property), :meth:`cached_block`, and :meth:`send_message`, returning
the same :class:`~pdf2md_claude.claude_api.ApiResponse`.

Caveats compared to the direct-API path:

* Prompt caching is managed by Claude Code itself; ``--cache`` is a no-op here
  and :meth:`cached_block` does not add ``cache_control``.
* Beta headers (e.g. the 1M-context window) are not forwarded — ``claude``
  only accepts ``--betas`` for API-key users.
* ``max_tokens`` follows Claude Code's per-model default rather than
  ``ModelConfig.max_output_tokens``.
* Token counts (and therefore the cost report) come from the CLI's ``result``
  event; on the first call for a document most input tokens show up as
  cache-creation tokens because Claude Code caches the PDF.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable

from pdf2md_claude.claude_api import ApiResponse, _backoff_delay
from pdf2md_claude.models import ModelConfig

_log = logging.getLogger("claude_cli")

CLAUDE_BIN_ENV = "PDF2MD_CLAUDE_BIN"
"""Env var overriding the name/path of the Claude Code executable."""

_DEFAULT_TIMEOUT_S = 1800
"""Per-call wall-clock timeout for the ``claude`` subprocess (30 min)."""

_DEFAULT_MAX_RETRIES = 5
"""Default number of attempts per request (Claude Code retries internally too)."""

_RETRYABLE_API_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
"""HTTP statuses (as reported in the CLI ``result`` event) worth retrying."""


def resolve_claude_bin(claude_bin: str | None) -> str:
    """Resolve the Claude executable: explicit arg, then env var, then ``claude``."""
    return claude_bin or os.environ.get(CLAUDE_BIN_ENV) or "claude"


def claude_cli_available(claude_bin: str | None = None) -> bool:
    """Return ``True`` if the Claude Code CLI looks usable on this machine."""
    return shutil.which(resolve_claude_bin(claude_bin)) is not None


def _strip_cache_control(content: object) -> object:
    """Recursively drop ``cache_control`` keys from message content.

    Claude Code manages prompt caching itself, so any ``cache_control`` blocks
    that pdf2md-claude added (when ``--cache`` is set) are removed before the
    content is handed to the CLI.
    """
    if isinstance(content, dict):
        return {k: _strip_cache_control(v) for k, v in content.items() if k != "cache_control"}
    if isinstance(content, list):
        return [_strip_cache_control(item) for item in content]
    return content


class _RetryableCliError(RuntimeError):
    """Internal marker for transient ``claude`` failures worth retrying."""


class ClaudeCliApi:
    """Drop-in replacement for :class:`ClaudeApi` backed by the ``claude`` CLI.

    Usage::

        api = ClaudeCliApi(model)
        resp = api.send_message(system_prompt, messages, retry_context="pages 1-10")
        print(resp.markdown)
    """

    def __init__(
        self,
        model: ModelConfig,
        *,
        claude_bin: str | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        use_cache: bool = False,
    ) -> None:
        """Initialize the CLI-backed API wrapper.

        Args:
            model: Model configuration (``model_id`` is passed to ``claude --model``).
            claude_bin: Name or path of the Claude Code executable. ``None`` ⇒
                the ``PDF2MD_CLAUDE_BIN`` env var, falling back to ``claude``.
            max_retries: Maximum number of attempts per request (1 = no retry).
            timeout_s: Wall-clock timeout for each ``claude`` invocation.
            use_cache: Accepted for interface parity; ignored (Claude Code
                manages prompt caching itself). A warning is logged if set.
        """
        self._model = model
        self._claude_bin = resolve_claude_bin(claude_bin)
        self._max_retries = max(1, max_retries)
        self._timeout_s = timeout_s
        if use_cache:
            _log.warning(
                "--cache has no effect with the Claude CLI backend "
                "(Claude Code manages prompt caching itself)"
            )
        if not claude_cli_available(self._claude_bin):
            raise RuntimeError(
                f"Claude CLI executable {self._claude_bin!r} not found on PATH. "
                f"Install Claude Code or set {CLAUDE_BIN_ENV}."
            )

    @property
    def model(self) -> ModelConfig:
        """The model configuration used by this API client."""
        return self._model

    def cached_block(self, block: dict) -> dict:
        """Return *block* unchanged.

        The CLI backend does not add ``cache_control`` blocks — Claude Code
        decides what to cache on its own.
        """
        return block

    # -- internals ---------------------------------------------------------

    def _build_command(self, system: str, thinking: dict | None) -> list[str]:
        """Assemble the ``claude`` argv for a single non-interactive request."""
        cmd = [
            self._claude_bin,
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",  # required for stream-json output in --print mode
            "--model", self._model.model_id,
            "--system-prompt", system,
            "--tools", "",  # pure single-turn completion, no agentic tools
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
        ]
        if thinking is not None:
            # The CLI exposes thinking via effort level rather than a token budget.
            cmd += ["--effort", "high"]
        return cmd

    def _build_stdin(self, messages: list[dict]) -> str:
        """Render *messages* as a stream-json stdin payload for ``claude``."""
        lines: list[str] = []
        for msg in messages:
            envelope = {
                "type": "user",
                "message": {
                    "role": msg.get("role", "user"),
                    "content": _strip_cache_control(msg.get("content", [])),
                },
            }
            lines.append(json.dumps(envelope))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _parse_output(
        stdout: str,
        on_thinking_delta: Callable[[str], None] | None,
    ) -> tuple[dict | None, str]:
        """Scan stream-json *stdout*, returning ``(result_event, text)``.

        ``text`` is the concatenation of assistant ``text`` blocks, used as a
        fallback if the ``result`` event has no ``result`` string.
        """
        result_event: dict | None = None
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for raw in stdout.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "assistant":
                for block in event.get("message", {}).get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "thinking" and on_thinking_delta is not None:
                        thinking_parts.append(block.get("thinking", ""))
            elif etype == "result":
                result_event = event
        if on_thinking_delta is not None and thinking_parts:
            on_thinking_delta("".join(thinking_parts))
        return result_event, "".join(text_parts)

    def _run_once(
        self,
        cmd: list[str],
        stdin_data: str,
        retry_context: str,
        on_thinking_delta: Callable[[str], None] | None,
    ) -> ApiResponse:
        """Invoke ``claude`` once and turn its output into an :class:`ApiResponse`.

        Raises:
            _RetryableCliError: On timeouts and transient API errors.
            RuntimeError: On permanent failures.
        """
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                encoding="utf-8",  # Claude Code emits UTF-8 regardless of locale
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise _RetryableCliError(
                f"claude CLI timed out after {self._timeout_s}s ({retry_context})"
            ) from exc
        except FileNotFoundError as exc:  # pragma: no cover - guarded in __init__
            raise RuntimeError(f"claude CLI not found: {exc}") from exc

        result_event, fallback_text = self._parse_output(proc.stdout, on_thinking_delta)

        if result_event is None:
            detail = (proc.stderr or proc.stdout or "").strip()[-800:]
            msg = (
                f"claude CLI produced no result ({retry_context}); "
                f"exit code {proc.returncode}: {detail or '<no output>'}"
            )
            # Non-zero exit with no structured result is usually transient
            # (network/transport); zero exit with no result is unexpected but
            # also worth one more try.
            raise _RetryableCliError(msg)

        if result_event.get("is_error") or result_event.get("subtype") not in (None, "success"):
            subtype = result_event.get("subtype") or "error"
            api_status = result_event.get("api_error_status")
            detail = result_event.get("result") or result_event.get("error") or subtype
            text = f"claude CLI error ({retry_context}): {subtype}: {detail}"
            if isinstance(api_status, int) and api_status in _RETRYABLE_API_STATUS:
                raise _RetryableCliError(text)
            raise RuntimeError(text)

        markdown = result_event.get("result")
        if not isinstance(markdown, str) or not markdown:
            markdown = fallback_text
        usage = result_event.get("usage") or {}

        def _u(key: str) -> int:
            value = usage.get(key)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        return ApiResponse(
            markdown=markdown,
            input_tokens=_u("input_tokens"),
            output_tokens=_u("output_tokens"),
            cache_creation_tokens=_u("cache_creation_input_tokens"),
            cache_read_tokens=_u("cache_read_input_tokens"),
            stop_reason=str(result_event.get("stop_reason") or "end_turn"),
        )

    # -- public API --------------------------------------------------------

    def send_message(
        self,
        system: str,
        messages: list[dict],
        retry_context: str = "",
        thinking: dict | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
    ) -> ApiResponse:
        """Send a message via ``claude -p`` with retry on transient errors.

        Mirrors :meth:`pdf2md_claude.claude_api.ClaudeApi.send_message`.

        Args:
            system: System prompt text.
            messages: Anthropic messages-API content (a list of ``{"role", "content"}``
                dicts; ``cache_control`` blocks are stripped before sending).
            retry_context: Optional label used in log messages.
            thinking: If not ``None``, extended thinking is requested (mapped to
                ``claude --effort high``); the exact config dict is ignored.
            on_thinking_delta: Optional callback; invoked once with the model's
                full thinking text when thinking is enabled.

        Returns:
            ApiResponse with markdown text, token counts, and stop reason.

        Raises:
            RuntimeError: On permanent CLI/API failures.
        """
        cmd = self._build_command(system, thinking)
        stdin_data = self._build_stdin(messages)
        context_str = f" ({retry_context})" if retry_context else ""
        start = time.time()

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._run_once(cmd, stdin_data, retry_context, on_thinking_delta)
                _log.debug(
                    "claude CLI call%s: %.1fs, stop=%s",
                    context_str, time.time() - start, resp.stop_reason,
                )
                return resp
            except _RetryableCliError as exc:
                if attempt == self._max_retries:
                    raise RuntimeError(str(exc)) from exc
                delay = _backoff_delay(attempt)
                _log.warning(
                    "%s (attempt %d/%d, retrying in %.0fs)",
                    exc, attempt, self._max_retries, delay,
                )
                time.sleep(delay)

        raise AssertionError("retry loop exited without returning or raising")
