"""Claude API client setup for PDF conversion."""

from __future__ import annotations

import logging

import anthropic

from pdf2md_claude.models import ModelConfig

_log = logging.getLogger("client")


def create_client(api_key: str, model: ModelConfig) -> anthropic.Anthropic:
    """Create an Anthropic client configured for the given model.

    Sets the 1M context beta header when the model config requires it.

    Args:
        api_key: Anthropic API key (ANTHROPIC_API_KEY).
        model: Model configuration with optional beta_header.

    Returns:
        Configured Anthropic client.
    """
    kwargs: dict = {"api_key": api_key}

    if model.beta_header:
        _log.debug("  Enabling beta header: %s", model.beta_header)
        kwargs["default_headers"] = {"anthropic-beta": model.beta_header}

    return anthropic.Anthropic(**kwargs)
