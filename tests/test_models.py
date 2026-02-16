"""Unit tests for models.py (DocumentUsageStats, StageCost, format_summary)."""

from pdf2md_claude.models import (
    DocumentUsageStats,
    StageCost,
    SONNET_4_5,
    format_summary,
)


class TestStageCost:
    """Tests for StageCost dataclass."""

    def test_basic_construction(self):
        """StageCost should be constructible with basic fields."""
        stage = StageCost(
            name="test stage",
            input_tokens=100,
            output_tokens=50,
            cost=0.10,
            elapsed_seconds=5.0,
            detail="3 items",
        )
        assert stage.name == "test stage"
        assert stage.input_tokens == 100
        assert stage.output_tokens == 50
        assert stage.cost == 0.10
        assert stage.elapsed_seconds == 5.0
        assert stage.detail == "3 items"


class TestDocumentUsageStatsProperties:
    """Tests for DocumentUsageStats total properties with stages."""

    def test_total_cost_without_stages(self):
        """total_cost should equal base cost when no stages."""
        stats = DocumentUsageStats(doc_name="test", pages=10, cost=0.50)
        assert stats.total_cost == 0.50

    def test_total_cost_with_stages(self):
        """total_cost should sum base + stages."""
        stats = DocumentUsageStats(
            doc_name="test", pages=10, cost=0.50,
            stages=[
                StageCost(name="s1", cost=0.10),
                StageCost(name="s2", cost=0.20),
            ]
        )
        assert stats.total_cost == 0.80  # 0.50 + 0.10 + 0.20

    def test_total_all_input_tokens(self):
        """total_all_input_tokens should sum base (incl cache) + stages."""
        stats = DocumentUsageStats(
            doc_name="test", pages=10,
            input_tokens=1000,
            cache_creation_tokens=200,
            cache_read_tokens=50,
            stages=[
                StageCost(name="s1", input_tokens=300),
                StageCost(name="s2", input_tokens=500),
            ]
        )
        assert stats.total_input_tokens == 1250  # base incl cache
        assert stats.total_all_input_tokens == 2050  # base + stages

    def test_total_all_output_tokens(self):
        """total_all_output_tokens should sum base + stages."""
        stats = DocumentUsageStats(
            doc_name="test", pages=10,
            output_tokens=500,
            stages=[
                StageCost(name="s1", output_tokens=100),
                StageCost(name="s2", output_tokens=200),
            ]
        )
        assert stats.total_all_output_tokens == 800  # 500 + 100 + 200

    def test_total_elapsed(self):
        """total_elapsed should sum base + stages."""
        stats = DocumentUsageStats(
            doc_name="test", pages=10,
            elapsed_seconds=10.0,
            stages=[
                StageCost(name="s1", elapsed_seconds=5.0),
                StageCost(name="s2", elapsed_seconds=3.0),
            ]
        )
        assert stats.total_elapsed == 18.0  # 10 + 5 + 3


class TestFormatSummary:
    """Tests for format_summary() output."""

    def test_single_document_no_stages(self):
        """Summary should show single document row without stages."""
        stats = [
            DocumentUsageStats(
                doc_name="test", pages=10,
                input_tokens=1000, output_tokens=500,
                cost=0.05, elapsed_seconds=10.0,
            )
        ]
        summary = format_summary(SONNET_4_5, stats)
        lines = summary.split("\n")
        
        # Should have document row but no stage sub-lines
        doc_lines = [l for l in lines if "test" in l]
        assert len(doc_lines) == 1
        assert "test" in doc_lines[0]
        assert "$" in doc_lines[0]
        
        # Should not have any indented sub-lines
        sublines = [l for l in lines if l.startswith("  ")]
        assert len(sublines) == 0

    def test_single_document_with_stages(self):
        """Summary should show document row + conversion sub-line + stage sub-lines."""
        stats = [
            DocumentUsageStats(
                doc_name="test", pages=10,
                input_tokens=1000, output_tokens=500,
                cost=0.05, elapsed_seconds=10.0,
                stages=[
                    StageCost(
                        name="table fixes",
                        input_tokens=2000,
                        output_tokens=1500,
                        cost=0.15,
                        elapsed_seconds=20.0,
                        detail="3 tables",
                    )
                ]
            )
        ]
        summary = format_summary(SONNET_4_5, stats)
        lines = summary.split("\n")
        
        # Should have document row showing grand totals
        doc_lines = [l for l in lines if l.strip().startswith("test")]
        assert len(doc_lines) == 1
        assert "3,000" in doc_lines[0]  # grand input (1000 + 2000)
        assert "2,000" in doc_lines[0]  # grand output (500 + 1500)
        assert "$   0.20" in doc_lines[0] or "$ 0.20" in doc_lines[0]  # total cost (0.05 + 0.15)
        
        # Should have conversion sub-line showing base conversion
        conv_lines = [l for l in lines if "conversion" in l]
        assert len(conv_lines) == 1
        assert "  conversion (1 chunk)" in conv_lines[0]
        assert "1,000" in conv_lines[0]  # base input
        assert "500" in conv_lines[0]  # base output
        assert "$   0.05" in conv_lines[0] or "$ 0.05" in conv_lines[0]  # base cost
        
        # Should have stage sub-line
        stage_lines = [l for l in lines if "table fixes" in l]
        assert len(stage_lines) == 1
        assert "  table fixes (3 tables)" in stage_lines[0]
        assert "2,000" in stage_lines[0]  # stage input
        assert "1,500" in stage_lines[0]  # stage output
        assert "$   0.15" in stage_lines[0] or "$ 0.15" in stage_lines[0]  # stage cost

    def test_multiple_documents_mixed_stages(self):
        """Summary should handle multiple documents, some with stages."""
        stats = [
            DocumentUsageStats(
                doc_name="doc1", pages=5,
                input_tokens=500, output_tokens=250,
                cost=0.03, elapsed_seconds=5.0,
            ),
            DocumentUsageStats(
                doc_name="doc2", pages=10,
                input_tokens=1000, output_tokens=500,
                cost=0.05, elapsed_seconds=10.0,
                stages=[
                    StageCost(name="table fixes", input_tokens=500, output_tokens=300, cost=0.02, elapsed_seconds=3.0, detail="1 table")
                ]
            ),
        ]
        summary = format_summary(SONNET_4_5, stats)
        lines = summary.split("\n")
        
        # Should have 2 document rows
        doc_lines = [l for l in lines if "doc1" in l or "doc2" in l]
        assert len([l for l in doc_lines if "doc1" in l]) == 1
        assert len([l for l in doc_lines if "doc2" in l]) == 1
        
        # Should have 1 conversion sub-line (only doc2, which has stages)
        conv_lines = [l for l in lines if "conversion" in l]
        assert len(conv_lines) == 1
        assert "  conversion (1 chunk)" in conv_lines[0]
        assert "1,000" in conv_lines[0]  # doc2 base input
        assert "500" in conv_lines[0]  # doc2 base output
        
        # Should have 1 stage sub-line (only doc2)
        stage_lines = [l for l in lines if "table fixes" in l]
        assert len(stage_lines) == 1
        
        # Total row should sum grand totals
        total_lines = [l for l in lines if l.strip().startswith("TOTAL")]
        assert len(total_lines) == 1
        # doc1: 500 + doc2: 1000 + doc2 stage: 500 = 2000 input
        # doc1: 250 + doc2: 500 + doc2 stage: 300 = 1050 output
        assert "2,000" in total_lines[0]
        assert "1,050" in total_lines[0]

    def test_conversion_subline_multi_chunk(self):
        """Conversion sub-line should show chunk count when > 1."""
        stats = [
            DocumentUsageStats(
                doc_name="test", pages=20,
                input_tokens=5000, output_tokens=2500,
                cost=0.25, elapsed_seconds=50.0,
                chunks=4,
                stages=[
                    StageCost(name="table fixes", input_tokens=1000, output_tokens=500, cost=0.05, elapsed_seconds=10.0, detail="2 tables")
                ]
            )
        ]
        summary = format_summary(SONNET_4_5, stats)
        lines = summary.split("\n")
        
        # Conversion sub-line should show "(4 chunks)"
        conv_lines = [l for l in lines if "conversion" in l]
        assert len(conv_lines) == 1
        assert "  conversion (4 chunks)" in conv_lines[0]
        assert "5,000" in conv_lines[0]  # base input
        assert "2,500" in conv_lines[0]  # base output
        assert "$   0.25" in conv_lines[0] or "$ 0.25" in conv_lines[0]  # base cost
