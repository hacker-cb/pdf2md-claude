"""Unit tests for claude_api module."""

from unittest.mock import Mock, MagicMock, patch

import httpx
import pytest

import anthropic

from pdf2md_claude.claude_api import ClaudeApi, _is_retryable
from pdf2md_claude.models import OPUS_4_6


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


# ---------------------------------------------------------------------------
# ClaudeApi (model property and thinking parameter)
# ---------------------------------------------------------------------------


class TestClaudeApi:
    """Tests for ClaudeApi class."""

    def test_model_property_returns_config(self):
        """The model property should return the ModelConfig passed to __init__."""
        mock_client = Mock(spec=anthropic.Anthropic)
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        
        assert api.model is OPUS_4_6
        assert api.model.model_id == "claude-opus-4-6"

    @patch.object(ClaudeApi, '_stream_message')
    def test_send_message_passes_thinking_parameter(self, mock_stream):
        """send_message should pass thinking parameter to _stream_message."""
        mock_client = Mock(spec=anthropic.Anthropic)
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        
        # Mock the response
        mock_response = Mock(
            markdown="Fixed table",
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            stop_reason="end_turn"
        )
        mock_stream.return_value = mock_response
        
        thinking_config = {"type": "adaptive"}
        api.send_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}],
            thinking=thinking_config
        )
        
        # Verify _stream_message was called with thinking parameter
        mock_stream.assert_called_once()
        call_args = mock_stream.call_args
        assert call_args[0][0] == "Test system"  # system prompt
        assert call_args[0][2] == thinking_config  # thinking parameter

    @patch.object(ClaudeApi, '_stream_message')
    def test_send_message_without_thinking(self, mock_stream):
        """send_message should work without thinking parameter (None)."""
        mock_client = Mock(spec=anthropic.Anthropic)
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        
        # Mock the response
        mock_response = Mock(
            markdown="Response",
            input_tokens=50,
            output_tokens=25,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            stop_reason="end_turn"
        )
        mock_stream.return_value = mock_response
        
        api.send_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}]
        )
        
        # Verify _stream_message was called with None for thinking
        mock_stream.assert_called_once()
        call_args = mock_stream.call_args
        assert call_args[0][2] is None  # thinking parameter should be None

    def test_stream_message_with_thinking_passes_to_sdk(self):
        """_stream_message should pass thinking to messages.stream when provided."""
        mock_client = Mock(spec=anthropic.Anthropic)
        
        # Mock the stream context manager and message
        mock_message = Mock()
        mock_message.content = [Mock(type="text", text="Response text")]
        mock_message.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0
        )
        mock_message.stop_reason = "end_turn"
        
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value.get_final_message.return_value = mock_message
        mock_client.messages.stream.return_value = mock_stream
        
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        thinking_config = {"type": "enabled", "budget_tokens": 5000}
        
        api._stream_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}],
            thinking=thinking_config
        )
        
        # Verify messages.stream was called with thinking in kwargs
        mock_client.messages.stream.assert_called_once()
        call_kwargs = mock_client.messages.stream.call_args[1]
        assert "thinking" in call_kwargs
        assert call_kwargs["thinking"] == thinking_config

    def test_stream_message_without_thinking_omits_from_sdk(self):
        """_stream_message should not pass thinking to messages.stream when None."""
        mock_client = Mock(spec=anthropic.Anthropic)
        
        # Mock the stream context manager and message
        mock_message = Mock()
        mock_message.content = [Mock(type="text", text="Response text")]
        mock_message.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0
        )
        mock_message.stop_reason = "end_turn"
        
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value.get_final_message.return_value = mock_message
        mock_client.messages.stream.return_value = mock_stream
        
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        
        api._stream_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}],
            thinking=None
        )
        
        # Verify messages.stream was called WITHOUT thinking in kwargs
        mock_client.messages.stream.assert_called_once()
        call_kwargs = mock_client.messages.stream.call_args[1]
        assert "thinking" not in call_kwargs

    def test_on_thinking_delta_callback_invoked(self):
        """on_thinking_delta callback should be invoked with thinking chunks."""
        mock_client = Mock(spec=anthropic.Anthropic)
        
        # Mock stream events with thinking_delta
        mock_event1 = Mock()
        mock_event1.type = "content_block_delta"
        mock_event1.delta = Mock(type="thinking_delta", thinking="Analyzing table structure...")
        
        mock_event2 = Mock()
        mock_event2.type = "content_block_delta"
        mock_event2.delta = Mock(type="thinking_delta", thinking="I see colspan in row 2...")
        
        mock_event3 = Mock()
        mock_event3.type = "content_block_delta"
        mock_event3.delta = Mock(type="text_delta", text="<table>")
        
        # Mock message
        mock_message = Mock()
        mock_message.content = [Mock(type="text", text="<table>fixed</table>")]
        mock_message.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0
        )
        mock_message.stop_reason = "end_turn"
        
        # Mock stream context manager
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__iter__.return_value = iter([mock_event1, mock_event2, mock_event3])
        mock_stream.get_final_message.return_value = mock_message
        mock_client.messages.stream.return_value = mock_stream
        
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        thinking_config = {"type": "adaptive"}
        
        # Track callback invocations
        thinking_chunks = []
        def callback(chunk: str):
            thinking_chunks.append(chunk)
        
        api._stream_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}],
            thinking=thinking_config,
            on_thinking_delta=callback,
        )
        
        # Verify callback was invoked with thinking deltas (not text deltas)
        assert len(thinking_chunks) == 2
        assert thinking_chunks[0] == "Analyzing table structure..."
        assert thinking_chunks[1] == "I see colspan in row 2..."

    def test_on_thinking_delta_not_called_without_thinking(self):
        """on_thinking_delta callback should not be invoked when thinking is None."""
        mock_client = Mock(spec=anthropic.Anthropic)
        
        # Mock stream events
        mock_event = Mock()
        mock_event.type = "content_block_delta"
        mock_event.delta = Mock(type="text_delta", text="Response")
        
        # Mock message
        mock_message = Mock()
        mock_message.content = [Mock(type="text", text="Response")]
        mock_message.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0
        )
        mock_message.stop_reason = "end_turn"
        
        # Mock stream context manager
        mock_stream = MagicMock()
        mock_stream.__enter__.return_value = mock_stream
        mock_stream.__iter__.return_value = iter([mock_event])
        mock_stream.get_final_message.return_value = mock_message
        mock_client.messages.stream.return_value = mock_stream
        
        api = ClaudeApi(mock_client, OPUS_4_6, use_cache=False, max_retries=1)
        
        # Track callback invocations
        thinking_chunks = []
        def callback(chunk: str):
            thinking_chunks.append(chunk)
        
        api._stream_message(
            system="Test system",
            messages=[{"role": "user", "content": "test"}],
            thinking=None,  # No thinking config
            on_thinking_delta=callback,
        )
        
        # Callback should not be invoked (thinking is None)
        assert len(thinking_chunks) == 0
