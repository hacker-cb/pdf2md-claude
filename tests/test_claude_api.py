"""Unit tests for claude_api module."""

import httpx
import pytest

import anthropic

from pdf2md_claude.claude_api import _is_retryable


# ---------------------------------------------------------------------------
# _is_retryable (transient error classification)
# ---------------------------------------------------------------------------


class TestIsRetryable:
    """Tests for _is_retryable() in claude_api.py."""

    # -- Retryable (transient) errors --------------------------------------

    def test_api_connection_error_retryable(self):
        """APIConnectionError (network failure) should be retryable."""
        exc = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        assert _is_retryable(exc) is True

    def test_api_timeout_error_retryable(self):
        """APITimeoutError (request timeout) should be retryable."""
        exc = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
        assert _is_retryable(exc) is True

    def test_rate_limit_error_retryable(self):
        """RateLimitError (429) should be retryable."""
        resp = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.RateLimitError(response=resp, body=None, message="rate limited")
        assert _is_retryable(exc) is True

    def test_internal_server_error_retryable(self):
        """InternalServerError (500) should be retryable."""
        resp = httpx.Response(500, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.InternalServerError(response=resp, body=None, message="server error")
        assert _is_retryable(exc) is True

    def test_overloaded_529_retryable(self):
        """Overloaded (529) should be retryable."""
        resp = httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.APIStatusError(response=resp, body=None, message="overloaded")
        assert _is_retryable(exc) is True

    def test_status_502_retryable(self):
        """502 Bad Gateway should be retryable."""
        resp = httpx.Response(502, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.APIStatusError(response=resp, body=None, message="bad gateway")
        assert _is_retryable(exc) is True

    def test_status_503_retryable(self):
        """503 Service Unavailable should be retryable."""
        resp = httpx.Response(503, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.APIStatusError(response=resp, body=None, message="unavailable")
        assert _is_retryable(exc) is True

    def test_remote_protocol_error_retryable(self):
        """RemoteProtocolError (by class name) should be retryable."""
        # Simulate httpcore.RemoteProtocolError without importing httpcore.
        class RemoteProtocolError(Exception):
            pass

        exc = RemoteProtocolError("peer closed connection")
        assert _is_retryable(exc) is True

    def test_read_error_retryable(self):
        """ReadError (by class name) should be retryable."""
        class ReadError(Exception):
            pass

        exc = ReadError("read failed")
        assert _is_retryable(exc) is True

    def test_protocol_error_retryable(self):
        """ProtocolError (by class name) should be retryable."""
        class ProtocolError(Exception):
            pass

        exc = ProtocolError("protocol violation")
        assert _is_retryable(exc) is True

    # -- Non-retryable (permanent) errors ----------------------------------

    def test_bad_request_not_retryable(self):
        """BadRequestError (400, includes content filtering) should NOT be retryable."""
        resp = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.BadRequestError(response=resp, body=None, message="bad request")
        assert _is_retryable(exc) is False

    def test_auth_error_not_retryable(self):
        """AuthenticationError (401) should NOT be retryable."""
        resp = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.AuthenticationError(response=resp, body=None, message="unauthorized")
        assert _is_retryable(exc) is False

    def test_permission_denied_not_retryable(self):
        """PermissionDeniedError (403) should NOT be retryable."""
        resp = httpx.Response(403, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.PermissionDeniedError(response=resp, body=None, message="forbidden")
        assert _is_retryable(exc) is False

    def test_not_found_not_retryable(self):
        """NotFoundError (404) should NOT be retryable."""
        resp = httpx.Response(404, request=httpx.Request("POST", "https://api.anthropic.com"))
        exc = anthropic.NotFoundError(response=resp, body=None, message="not found")
        assert _is_retryable(exc) is False

    def test_runtime_error_not_retryable(self):
        """RuntimeError (max_tokens truncation) should NOT be retryable."""
        exc = RuntimeError("Chunk pages 1-10 truncated")
        assert _is_retryable(exc) is False

    def test_generic_exception_not_retryable(self):
        """Generic Exception should NOT be retryable."""
        exc = Exception("something unexpected")
        assert _is_retryable(exc) is False

    def test_value_error_not_retryable(self):
        """ValueError should NOT be retryable."""
        exc = ValueError("invalid argument")
        assert _is_retryable(exc) is False
