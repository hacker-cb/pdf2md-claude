"""Unit tests for the AI-based table regeneration step in table_fixer.py."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pdf2md_claude.models import OPUS_4_6, SONNET_4_5, HAIKU_4_5
from pdf2md_claude.pipeline import ProcessingContext
from pdf2md_claude.table_fixer import ComplexTable, FixTablesStep, fix_single_table, _build_thinking_config

from tests.conftest import wrap_pages as _wrap_pages


# ---------------------------------------------------------------------------
# fix_single_table() tests
# ---------------------------------------------------------------------------


class TestFixSingleTable:
    """Tests for the fix_single_table() function (table regeneration)."""

    def test_calls_api_with_pdf_pages(self, tmp_path):
        """Should extract PDF pages and regenerate table via Claude API."""
        # Create a mock PDF file
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        # Mock API response
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_response = Mock(
            markdown="<table><tr><td>Fixed</td></tr></table>",
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0
        )
        mock_api.send_message.return_value = mock_response

        # Create markdown with proper line structure
        markdown = (
            "# Document Title\n\n"
            "Some introductory text.\n\n"
            "**Table 5 – Test Table**\n\n"
            "<table><tr><td>Broken</td></tr></table>\n\n"
            "Text after the table.\n\n"
            "More content here.\n"
        )
        
        # Create a complex table (positions match the markdown above)
        table_start = markdown.index("<table>")
        table_end = markdown.index("</table>") + len("</table>")
        
        table = ComplexTable(
            table_html="<table><tr><td colspan=\"2\">Broken</td></tr></table>",
            match_start=table_start,
            match_end=table_end,
            page_numbers=[1, 2],
            label="Table 5",
        )

        # Mock extract_pdf_pages
        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            result = fix_single_table(mock_api, pdf_path, table, markdown)

            # Verify PDF extraction was called
            mock_extract.assert_called_once_with(pdf_path, 1, 2)

            # Verify API was called
            assert mock_api.send_message.called
            call_args = mock_api.send_message.call_args
            assert "system" in call_args[1]
            assert "messages" in call_args[1]
            assert "retry_context" in call_args[1]
            assert call_args[1]["retry_context"] == "Table 5"

            # Verify result (now returns 4-tuple)
            assert result is not None
            corrected_html, response, elapsed, cost = result
            assert corrected_html == "<table><tr><td>Fixed</td></tr></table>"
            assert response is mock_response
            assert isinstance(elapsed, float)
            assert elapsed >= 0
            assert isinstance(cost, float)
            assert cost >= 0

    def test_passes_thinking_config_to_api(self, tmp_path):
        """Should pass thinking config to API for table regeneration."""
        # Create a mock PDF file
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        # Mock API with model property
        mock_api = Mock()
        mock_api.model = OPUS_4_6
        mock_response = Mock(
            markdown="<table><tr><td>Fixed</td></tr></table>",
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0
        )
        mock_api.send_message.return_value = mock_response

        # Create markdown with proper line structure
        markdown = (
            "# Document Title\n\n"
            "Some text.\n\n"
            "**Table 1 – Test**\n\n"
            "<table><tr><td colspan=\"2\">Broken</td></tr></table>\n\n"
            "More text.\n"
        )
        
        table_start = markdown.index("<table>")
        table_end = markdown.index("</table>") + len("</table>")
        
        table = ComplexTable(
            table_html="<table><tr><td colspan=\"2\">Broken</td></tr></table>",
            match_start=table_start,
            match_end=table_end,
            page_numbers=[1],
            label="Table 1",
        )

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            result = fix_single_table(mock_api, pdf_path, table, markdown)

            # Verify API was called with thinking parameter
            assert mock_api.send_message.called
            call_args = mock_api.send_message.call_args
            assert "thinking" in call_args[1]
            # For Opus 4.6, should be adaptive thinking
            assert call_args[1]["thinking"] == {"type": "adaptive"}

            # Verify result (now returns 4-tuple)
            assert result is not None
            corrected_html, response, elapsed, cost = result
            assert corrected_html == "<table><tr><td>Fixed</td></tr></table>"
            assert response is mock_response
            assert isinstance(elapsed, float)
            assert elapsed >= 0
            assert isinstance(cost, float)
            assert cost >= 0

    def test_returns_none_when_no_page_numbers(self, tmp_path):
        """Should return None when page_numbers is empty."""
        pdf_path = tmp_path / "test.pdf"
        mock_api = Mock()

        table = ComplexTable(
            table_html="<table></table>",
            match_start=0,
            match_end=10,
            page_numbers=[],  # Empty!
            label="Table 1",
        )

        result = fix_single_table(mock_api, pdf_path, table, "markdown")
        assert result is None

    def test_returns_none_when_no_table_in_response(self, tmp_path):
        """Should return None when Claude's response lacks <table>."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_api.send_message.return_value = Mock(
            markdown="Sorry, I cannot fix this table."
        )

        table = ComplexTable(
            table_html="<table></table>",
            match_start=0,
            match_end=10,
            page_numbers=[1],
            label="Table 1",
        )

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            result = fix_single_table(mock_api, pdf_path, table, "markdown")

        assert result is None

    def test_context_extraction_non_empty_lines(self, tmp_path):
        """Context should include only non-empty lines for table regeneration."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_response = Mock(
            markdown="<table><tr><td>Fixed</td></tr></table>",
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0
        )
        mock_api.send_message.return_value = mock_response

        # Create markdown with blank lines that should be skipped
        markdown = (
            "Line 1\n"
            "\n"  # Blank line should be skipped
            "Line 2\n"
            "Line 3\n"
            "\n\n"  # Multiple blank lines
            "**Table 1 – Test**\n\n"
            "<table><tr><td rowspan=\"2\">Broken</td></tr></table>\n\n"
            "\n"  # Blank after
            "After 1\n"
            "After 2\n"
            "\n"
            "After 3\n"
        )
        
        # Find table position
        table_start = markdown.index("<table>")
        table_end = markdown.index("</table>") + len("</table>")

        table = ComplexTable(
            table_html="<table><tr><td rowspan=\"2\">Broken</td></tr></table>",
            match_start=table_start,
            match_end=table_end,
            page_numbers=[1],
            label="Table 1",
        )

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            result = fix_single_table(mock_api, pdf_path, table, markdown)

            # Verify API was called
            assert mock_api.send_message.called
            call_args = mock_api.send_message.call_args
            messages = call_args[1]["messages"]
            user_text = messages[0]["content"][1]["text"]

            # Should have 3 non-empty lines before (Line 2, Line 3, Table title)
            # and 3 non-empty lines after (After 1, 2, 3)
            # Note: Line 1 is too far back, only the 3 closest non-empty lines are included
            assert "Line 2" in user_text
            assert "Line 3" in user_text
            assert "**Table 1 – Test**" in user_text
            assert "After 1" in user_text
            assert "After 2" in user_text
            assert "After 3" in user_text
            # Verify blank lines were skipped (Line 1 is too far)
            assert user_text.count("Line 1") == 0
            
            # Verify result (now returns 4-tuple)
            assert result is not None
            corrected_html, response, elapsed, cost = result
            assert corrected_html == "<table><tr><td>Fixed</td></tr></table>"
            assert response is mock_response
            assert isinstance(elapsed, float)
            assert elapsed >= 0
            assert isinstance(cost, float)
            assert cost >= 0

    def test_context_extraction_at_newline_boundary(self, tmp_path):
        """Context extraction should handle positions exactly at newline boundaries."""
        from pdf2md_claude.table_fixer import _extract_context_lines
        
        # Create markdown where table starts exactly at a newline
        markdown = "Line 1\nLine 2\nLine 3\n<table>Content</table>\nAfter 1\nAfter 2\n"
        
        # Position exactly at the newline before <table>
        table_pos = markdown.index("<table>")
        
        # Extract before context
        before = _extract_context_lines(markdown, table_pos, 2, before=True)
        assert "Line 2" in before
        assert "Line 3" in before
        
        # Extract after context
        table_end = markdown.index("</table>") + len("</table>")
        after = _extract_context_lines(markdown, table_end, 2, before=False)
        assert "After 1" in after
        assert "After 2" in after

    def test_context_extraction_start_of_document(self, tmp_path):
        """Context extraction at document start should not crash."""
        from pdf2md_claude.table_fixer import _extract_context_lines
        
        markdown = "<table>First table</table>\nAfter line\n"
        
        # Position at very beginning
        before = _extract_context_lines(markdown, 0, 3, before=True)
        assert before == ""  # No lines before
        
        after = _extract_context_lines(markdown, 0, 1, before=False)
        assert "After line" in after

    def test_context_extraction_end_of_document(self, tmp_path):
        """Context extraction at document end should not crash."""
        from pdf2md_claude.table_fixer import _extract_context_lines
        
        markdown = "Before line\n<table>Last table</table>"
        
        # Position at very end
        end_pos = len(markdown)
        before = _extract_context_lines(markdown, end_pos, 1, before=True)
        assert "Before line" in before
        
        after = _extract_context_lines(markdown, end_pos, 3, before=False)
        assert after == ""  # No lines after

    def test_context_extraction_fewer_than_requested(self, tmp_path):
        """Context extraction should handle cases with fewer lines than requested."""
        from pdf2md_claude.table_fixer import _extract_context_lines
        
        markdown = "Only one before\n<table>Content</table>\nOnly one after"
        table_pos = markdown.index("<table>")
        
        # Request 5 lines but only 1 available
        before = _extract_context_lines(markdown, table_pos, 5, before=True)
        lines = [l for l in before.split('\n') if l.strip()]
        assert len(lines) == 1
        assert "Only one before" in before


# ---------------------------------------------------------------------------
# FixTablesStep tests
# ---------------------------------------------------------------------------


class TestFixTablesStep:
    """Tests for the FixTablesStep processing step (table regeneration)."""

    def _make_ctx(self, markdown: str, api=None, pdf_path=None) -> ProcessingContext:
        """Create a ProcessingContext for testing."""
        return ProcessingContext(
            markdown=markdown,
            pdf_path=pdf_path,
            output_file=Path("/tmp/test.md"),
            api=api,
        )

    def test_no_complex_tables_skips_api(self):
        """When no complex tables, should skip API calls."""
        md = _wrap_pages(
            "**Table 1 – Simple**\n\n"
            "<table>\n"
            "<thead><tr><th>A</th><th>B</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        mock_api = Mock()
        ctx = self._make_ctx(md, api=mock_api, pdf_path=Path("/tmp/test.pdf"))

        step = FixTablesStep()
        step.run(ctx)

        # API should not be called (table has no colspan/rowspan)
        assert not mock_api.send_message.called

    def test_skips_when_api_is_none(self):
        """When ctx.api is None, should skip gracefully."""
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">Header</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        ctx = self._make_ctx(md, api=None, pdf_path=Path("/tmp/test.pdf"))

        step = FixTablesStep()
        step.run(ctx)  # Should not raise

        # Markdown should be unchanged
        assert ctx.markdown == md

    def test_skips_when_pdf_path_is_none(self):
        """When ctx.pdf_path is None, should skip gracefully."""
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th rowspan=\"2\">A</th><th>B</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        mock_api = Mock()
        ctx = self._make_ctx(md, api=mock_api, pdf_path=None)

        step = FixTablesStep()
        step.run(ctx)  # Should not raise

        # API should not be called
        assert not mock_api.send_message.called

    def test_replaces_complex_tables_last_to_first(self, tmp_path):
        """Should process tables in reverse order to preserve offsets."""
        md = _wrap_pages(
            "**Table 1 – First Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">Header</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n\n"
            "**Table 2 – Second Complex**\n\n"
            "<table>\n"
            "<thead><tr><th rowspan=\"2\">X</th><th>Y</th></tr></thead>\n"
            "<tbody><tr><td>4</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        # Mock API to return fixed tables with identifiable markers
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        call_count = [0]

        def mock_send_message(**kwargs):
            call_count[0] += 1
            mock_resp = Mock()
            mock_resp.input_tokens = 100
            mock_resp.output_tokens = 50
            mock_resp.cache_creation_tokens = 0
            mock_resp.cache_read_tokens = 0
            # Return different table based on call order
            if call_count[0] == 1:
                # First call (Table 2, processed last-to-first)
                mock_resp.markdown = "<table><tr><td>FIXED_2</td></tr></table>"
            else:
                # Second call (Table 1)
                mock_resp.markdown = "<table><tr><td>FIXED_1</td></tr></table>"
            return mock_resp

        mock_api.send_message.side_effect = mock_send_message

        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path)

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            step = FixTablesStep()
            step.run(ctx)

            # Verify both tables were replaced
            assert "FIXED_1" in ctx.markdown
            assert "FIXED_2" in ctx.markdown
            assert "colspan=\"2\"" not in ctx.markdown  # original table 1 gone
            assert "rowspan=\"2\"" not in ctx.markdown  # original table 2 gone

    def test_step_protocol_properties(self):
        """Verify FixTablesStep implements ProcessingStep protocol."""
        step = FixTablesStep()
        assert step.name == "fix tables"
        assert step.key == "fix-tables"

    def test_sets_table_fix_stats_on_context(self, tmp_path):
        """Should set ctx.table_fix_stats after processing tables."""
        from pdf2md_claude.workdir import WorkDir

        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5

        def mock_send_message(**kwargs):
            mock_resp = Mock()
            mock_resp.input_tokens = 100
            mock_resp.output_tokens = 50
            mock_resp.cache_creation_tokens = 0
            mock_resp.cache_read_tokens = 0
            mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
            return mock_resp

        mock_api.send_message.side_effect = mock_send_message

        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)

        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path)
        ctx.work_dir = work_dir

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            step = FixTablesStep()
            step.run(ctx)

            # Should set table_fix_stats
            assert ctx.table_fix_stats is not None
            assert ctx.table_fix_stats.tables_found == 1
            assert ctx.table_fix_stats.tables_fixed == 1
            assert ctx.table_fix_stats.total_input_tokens == 100
            assert ctx.table_fix_stats.total_output_tokens == 50

    def test_persists_results_when_work_dir_available(self, tmp_path):
        """Should persist table fix results when work_dir is available."""
        from pdf2md_claude.workdir import WorkDir

        md = _wrap_pages(
            "**Table 3 – Test**\n\n"
            "<table>\n"
            "<thead><tr><th rowspan=\"2\">A</th><th>B</th></tr></thead>\n"
            "<tbody><tr><td>1</td></tr></tbody>\n"
            "</table>\n",
            start=2, end=2,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_resp = Mock()
        mock_resp.input_tokens = 150
        mock_resp.output_tokens = 75
        mock_resp.cache_creation_tokens = 10
        mock_resp.cache_read_tokens = 5
        mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        mock_api.send_message.return_value = mock_resp

        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)

        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path)
        ctx.work_dir = work_dir

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            step = FixTablesStep()
            step.run(ctx)

            # Verify files were created
            table_fixer_dir = tmp_path / "out.staging" / "table_fixer"
            assert table_fixer_dir.exists()
            assert (table_fixer_dir / "p002-002_table_3.json").exists()
            assert (table_fixer_dir / "p002-002_table_3_before.html").exists()
            assert (table_fixer_dir / "p002-002_table_3_after.html").exists()
            assert (table_fixer_dir / "stats.json").exists()

            # Verify stats were saved
            loaded_stats = work_dir.load_table_fix_stats()
            assert loaded_stats is not None
            assert loaded_stats.tables_found == 1
            assert loaded_stats.tables_fixed == 1

    def test_sets_stats_without_work_dir(self, tmp_path):
        """Should set ctx.table_fix_stats even when work_dir is None."""
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"3\">ABC</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5

        def mock_send_message(**kwargs):
            mock_resp = Mock()
            mock_resp.input_tokens = 100
            mock_resp.output_tokens = 50
            mock_resp.cache_creation_tokens = 0
            mock_resp.cache_read_tokens = 0
            mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
            return mock_resp

        mock_api.send_message.side_effect = mock_send_message

        # Create context WITHOUT work_dir
        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path)
        assert ctx.work_dir is None

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            step = FixTablesStep()
            step.run(ctx)

            # Should set table_fix_stats even without work_dir
            assert ctx.table_fix_stats is not None
            assert ctx.table_fix_stats.tables_found == 1
            assert ctx.table_fix_stats.tables_fixed == 1
            assert ctx.table_fix_stats.total_input_tokens == 100
            assert ctx.table_fix_stats.total_output_tokens == 50

    def test_user_message_no_diagnostics(self, tmp_path):
        """User message should not include diagnostic column counts."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_response = Mock(
            markdown="<table><tr><td>Fixed</td></tr></table>",
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=0,
            cache_read_tokens=0
        )
        mock_api.send_message.return_value = mock_response

        markdown = (
            "# Document\n\n"
            "Some text.\n\n"
            "**Table 1 – Test**\n\n"
            "<table><tr><td colspan=\"3\">Broken</td></tr></table>\n\n"
            "More text.\n"
        )
        
        table_start = markdown.index("<table>")
        table_end = markdown.index("</table>") + len("</table>")
        
        table = ComplexTable(
            table_html="<table><tr><td colspan=\"3\">Broken</td></tr></table>",
            match_start=table_start,
            match_end=table_end,
            page_numbers=[1],
            label="Table 1",
        )

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            result = fix_single_table(mock_api, pdf_path, table, markdown)

            # Verify user message doesn't contain diagnostics
            call_args = mock_api.send_message.call_args
            messages = call_args[1]["messages"]
            user_text = messages[0]["content"][1]["text"]
            
            # Should NOT contain diagnostic details
            assert "Issues detected" not in user_text
            assert "column-count inconsistencies" not in user_text
            assert "widths of" not in user_text
            assert "inconsistent row(s)" not in user_text
            
            # Should contain essential elements
            assert "**Table identification:** Table 1" in user_text
            assert "**Previous extraction (for reference only — complex table with merged cells):**" in user_text
            assert "Generate the complete, correctly structured table from the PDF with proper colspan/rowspan attributes." in user_text

    def test_clears_old_table_fixer_results_before_run(self, tmp_path):
        """FixTablesStep should clear old table-fixer results before processing."""
        from pdf2md_claude.workdir import WorkDir, TableFixResult
        
        md = _wrap_pages(
            "**Table 1 – Test**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)

        # Save an OLD table-fix result (from a previous run)
        old_result = TableFixResult(
            index=0,
            label="Old Table",
            page_numbers=[5],
            input_tokens=999,
            output_tokens=999,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost=9.99,
            elapsed_seconds=99.0,
            before_chars=100,
            after_chars=200,
        )
        work_dir.save_table_fix(old_result, "<table>old</table>", "<table>old_fixed</table>")

        # Verify old file exists
        table_fixer_dir = tmp_path / "out.staging" / "table_fixer"
        assert (table_fixer_dir / "p005-005_old_table.json").exists()

        # Now run FixTablesStep with a mock API
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_resp = Mock()
        mock_resp.input_tokens = 100
        mock_resp.output_tokens = 50
        mock_resp.cache_creation_tokens = 0
        mock_resp.cache_read_tokens = 0
        mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        mock_api.send_message.return_value = mock_resp

        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path)
        ctx.work_dir = work_dir

        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"

            step = FixTablesStep()
            step.run(ctx)

            # Old file should be gone, replaced with new one
            assert not (table_fixer_dir / "p005-005_old_table.json").exists()
            assert (table_fixer_dir / "p001-001_table_1.json").exists()


# ---------------------------------------------------------------------------
# _build_thinking_config() tests
# ---------------------------------------------------------------------------


class TestBuildThinkingConfig:
    """Tests for the _build_thinking_config() helper function."""

    def test_opus_4_6_uses_adaptive_thinking(self):
        """Opus 4.6 should use adaptive thinking."""
        config = _build_thinking_config(OPUS_4_6)
        assert config == {"type": "adaptive"}

    def test_sonnet_4_5_uses_budget_thinking(self):
        """Sonnet 4.5 should use budget-based thinking."""
        config = _build_thinking_config(SONNET_4_5)
        assert config == {"type": "enabled", "budget_tokens": 10_000}

    def test_haiku_4_5_uses_budget_thinking(self):
        """Haiku 4.5 should use budget-based thinking."""
        config = _build_thinking_config(HAIKU_4_5)
        assert config == {"type": "enabled", "budget_tokens": 10_000}


# ---------------------------------------------------------------------------
# Table fixer caching tests
# ---------------------------------------------------------------------------


class TestTableFixerCaching:
    """Tests for table fixer output caching."""

    def _make_ctx(self, markdown: str, api=None, pdf_path=None, work_dir=None) -> ProcessingContext:
        """Create a ProcessingContext for testing."""
        return ProcessingContext(
            markdown=markdown,
            pdf_path=pdf_path,
            output_file=Path("/tmp/test.md"),
            api=api,
            work_dir=work_dir,
        )

    def test_cache_hit_skips_api_calls(self, tmp_path):
        """When cache hit, should skip API calls and load cached output."""
        from pdf2md_claude.workdir import WorkDir, TableFixStats
        
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )
        
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)
        
        # Write merged.md to disk (for hashing)
        (tmp_path / "out.staging" / "merged.md").write_text(md, encoding="utf-8")
        
        # Compute hash and save cached output + stats
        input_hash = work_dir.content_hash_glob("merged.md")
        cached_markdown = "# CACHED OUTPUT\n\n<table><tr><td>Cached fixed table</td></tr></table>"
        work_dir.save_table_fixer_output(cached_markdown)
        
        cached_stats = TableFixStats(
            tables_found=1,
            tables_fixed=1,
            total_input_tokens=100,
            total_output_tokens=50,
            total_cost=0.05,
            total_elapsed_seconds=5.0,
            input_hash=input_hash,
        )
        work_dir.save_table_fix_stats(cached_stats)
        
        # Create context with mock API
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path, work_dir=work_dir)
        
        # Run FixTablesStep
        step = FixTablesStep()
        step.run(ctx)
        
        # API should NOT be called (cache hit)
        assert not mock_api.send_message.called
        
        # Markdown should be loaded from cache
        assert ctx.markdown == cached_markdown
        
        # Stats should be loaded from cache
        assert ctx.table_fix_stats is not None
        assert ctx.table_fix_stats.tables_fixed == 1
        assert ctx.table_fix_stats.total_cost == 0.05

    def test_cache_miss_hash_differs(self, tmp_path):
        """When hash differs, should re-process tables."""
        from pdf2md_claude.workdir import WorkDir, TableFixStats
        
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )
        
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)
        
        # Write DIFFERENT merged.md to disk (hash will differ)
        different_md = _wrap_pages("Different content\n\n<table><tr><td colspan=\"2\">X</td></tr></table>", 1, 1)
        (tmp_path / "out.staging" / "merged.md").write_text(different_md, encoding="utf-8")
        
        # Save cached output + stats with WRONG hash
        cached_markdown = "# OLD CACHED OUTPUT"
        work_dir.save_table_fixer_output(cached_markdown)
        
        old_stats = TableFixStats(
            tables_found=1,
            tables_fixed=1,
            total_input_tokens=100,
            total_output_tokens=50,
            total_cost=0.05,
            total_elapsed_seconds=5.0,
            input_hash="wrong_hash_value",
        )
        work_dir.save_table_fix_stats(old_stats)
        
        # Mock API to return new fixed table
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_resp = Mock()
        mock_resp.input_tokens = 200
        mock_resp.output_tokens = 100
        mock_resp.cache_creation_tokens = 0
        mock_resp.cache_read_tokens = 0
        mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        mock_api.send_message.return_value = mock_resp
        
        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path, work_dir=work_dir)
        
        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            
            step = FixTablesStep()
            step.run(ctx)
            
            # API SHOULD be called (cache miss)
            assert mock_api.send_message.called
            
            # Markdown should be updated with new fix
            assert "<table><tr><th>A</th><th>B</th></tr>" in ctx.markdown
            assert "OLD CACHED OUTPUT" not in ctx.markdown
            
            # New stats should be set
            assert ctx.table_fix_stats is not None
            assert ctx.table_fix_stats.total_input_tokens == 200
            
            # New output + stats should be saved
            loaded_output = work_dir.load_table_fixer_output()
            assert loaded_output == ctx.markdown
            
            loaded_stats = work_dir.load_table_fix_stats()
            assert loaded_stats is not None
            assert loaded_stats.input_hash != "wrong_hash_value"
            assert loaded_stats.input_hash != ""

    def test_cache_miss_output_missing(self, tmp_path):
        """When output.md is missing but stats exist, should re-process."""
        from pdf2md_claude.workdir import WorkDir, TableFixStats
        
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )
        
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        work_dir = WorkDir(tmp_path / "out.staging")
        work_dir.path.mkdir(parents=True, exist_ok=True)
        
        # Write merged.md to disk
        (tmp_path / "out.staging" / "merged.md").write_text(md, encoding="utf-8")
        
        # Compute hash and save stats, but DON'T save output.md
        input_hash = work_dir.content_hash_glob("merged.md")
        stats = TableFixStats(
            tables_found=1,
            tables_fixed=1,
            total_input_tokens=100,
            total_output_tokens=50,
            total_cost=0.05,
            total_elapsed_seconds=5.0,
            input_hash=input_hash,
        )
        work_dir.save_table_fix_stats(stats)
        # Note: NOT saving output.md
        
        # Mock API
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_resp = Mock()
        mock_resp.input_tokens = 150
        mock_resp.output_tokens = 75
        mock_resp.cache_creation_tokens = 0
        mock_resp.cache_read_tokens = 0
        mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        mock_api.send_message.return_value = mock_resp
        
        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path, work_dir=work_dir)
        
        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            
            step = FixTablesStep()
            step.run(ctx)
            
            # API SHOULD be called (output.md missing)
            assert mock_api.send_message.called
            
            # New output should be saved
            loaded_output = work_dir.load_table_fixer_output()
            assert loaded_output is not None
            assert "<table><tr><th>A</th><th>B</th></tr>" in loaded_output

    def test_cache_without_work_dir(self, tmp_path):
        """When work_dir is None, should skip caching logic."""
        md = _wrap_pages(
            "**Table 1 – Complex**\n\n"
            "<table>\n"
            "<thead><tr><th colspan=\"2\">AB</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n"
            "</table>\n",
            start=1, end=1,
        )
        
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        
        # Mock API
        mock_api = Mock()
        mock_api.model = SONNET_4_5
        mock_resp = Mock()
        mock_resp.input_tokens = 100
        mock_resp.output_tokens = 50
        mock_resp.cache_creation_tokens = 0
        mock_resp.cache_read_tokens = 0
        mock_resp.markdown = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        mock_api.send_message.return_value = mock_resp
        
        # Create context WITHOUT work_dir
        ctx = self._make_ctx(md, api=mock_api, pdf_path=pdf_path, work_dir=None)
        
        with patch("pdf2md_claude.table_fixer.extract_pdf_pages") as mock_extract:
            mock_extract.return_value = "base64encodedpdf"
            
            step = FixTablesStep()
            step.run(ctx)
            
            # Should still process tables (no cache check without work_dir)
            assert mock_api.send_message.called
            assert ctx.table_fix_stats is not None
            # input_hash should be empty string when work_dir is None
            assert ctx.table_fix_stats.input_hash == ""
