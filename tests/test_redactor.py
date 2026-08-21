import pytest
import os
from pathlib import Path
from pii_engine.redactor import (
    analyze_pdf,
    redact_pdf,
    _merge_boxes_for_match,
    _verify,
    Box,
    PageAnalysis,
)
from pii_engine.engine import RuleConfig, Match


class TestBoxMerging:
    def test_merge_single_box(self):
        m = Match("test", "Test", "value", 0, 5, "high")
        box = Box(x0=10.0, top=20.0, x1=50.0, bottom=30.0, match=m)
        merged = _merge_boxes_for_match([box])
        assert merged.x0 == 10.0
        assert merged.x1 == 50.0

    def test_merge_multiple_boxes(self):
        m = Match("test", "Test", "value", 0, 5, "high")
        boxes = [
            Box(x0=10.0, top=20.0, x1=30.0, bottom=30.0, match=m),
            Box(x0=32.0, top=21.0, x1=50.0, bottom=31.0, match=m),
            Box(x0=52.0, top=19.0, x1=70.0, bottom=29.0, match=m),
        ]
        merged = _merge_boxes_for_match(boxes)
        assert merged.x0 == 10.0  # leftmost
        assert merged.x1 == 70.0  # rightmost
        assert merged.top == 19.0  # topmost
        assert merged.bottom == 31.0  # bottommost

    def test_merge_empty_list_raises(self):
        with pytest.raises(ValueError):
            _merge_boxes_for_match([])


class TestAnalyzePDF:
    @pytest.fixture
    def sample_pdf(self):
        sample_path = Path(__file__).parent.parent / "samples" / "sample_pii.pdf"
        if sample_path.exists():
            return str(sample_path)
        pytest.skip("Sample PDF not found")

    def test_analyze_detects_pii(self, sample_pdf):
        config = RuleConfig(
            enabled_keys=['credit_card', 'us_ssn', 'email'],
            custom_rules=[]
        )
        analyses = analyze_pdf(sample_pdf, config)

        assert len(analyses) > 0
        # Should find some matches in the sample
        total_matches = sum(len(pa.matches) for pa in analyses)
        assert total_matches > 0

    def test_boxes_created_for_matches(self, sample_pdf):
        config = RuleConfig(
            enabled_keys=['email'],
            custom_rules=[]
        )
        analyses = analyze_pdf(sample_pdf, config)

        # Find a page with email matches
        page_with_matches = None
        for pa in analyses:
            if pa.matches:
                page_with_matches = pa
                break

        if page_with_matches:
            # Each match should have at least one box
            assert len(page_with_matches.boxes) > 0
            # Boxes should have valid coordinates
            for box in page_with_matches.boxes:
                assert box.x0 < box.x1
                assert box.top < box.bottom


class TestRedactPDF:
    @pytest.fixture
    def sample_pdf(self):
        sample_path = Path(__file__).parent.parent / "samples" / "sample_pii.pdf"
        if sample_path.exists():
            return str(sample_path)
        pytest.skip("Sample PDF not found")

    @pytest.fixture
    def temp_output(self, tmp_path):
        return str(tmp_path / "redacted_test.pdf")

    def test_redact_creates_output(self, sample_pdf, temp_output):
        config = RuleConfig(
            enabled_keys=['email'],
            custom_rules=[]
        )
        report = redact_pdf(sample_pdf, temp_output, config, dpi=150)

        assert os.path.exists(temp_output)
        assert report['pages'] > 0

    def test_redact_report_structure(self, sample_pdf, temp_output):
        config = RuleConfig(
            enabled_keys=['credit_card', 'us_ssn'],
            custom_rules=[]
        )
        report = redact_pdf(sample_pdf, temp_output, config)

        # Check report structure
        assert 'source_file' in report
        assert 'output_file' in report
        assert 'pages' in report
        assert 'total_redactions' in report
        assert 'summary' in report
        assert 'items' in report
        assert 'verification' in report

        assert "passed" in report["verification"]
        assert "matches_have_boxes" in report["verification"]
        assert "text_layer_clean" in report["verification"]
        assert "banner" in report["verification"]
        assert isinstance(report["verification"]["passed"], bool)

    def test_surgical_redaction_cleans_text_layer(self, sample_pdf, temp_output):
        config = RuleConfig(
            enabled_keys=["credit_card", "us_ssn", "email"],
            custom_rules=[],
        )
        report = redact_pdf(sample_pdf, temp_output, config, mode="surgical")
        v = report["verification"]
        assert v["matches_have_boxes"] is True
        assert v["text_layer_clean"] is True
        assert v["banner"] == "surgical_ok"
        assert v["passed"] is True

    def test_flatten_redaction_removes_text_layer(self, sample_pdf, temp_output):
        config = RuleConfig(enabled_keys=["email"], custom_rules=[])
        report = redact_pdf(sample_pdf, temp_output, config, mode="flatten", dpi=150)
        v = report["verification"]
        assert v["text_layer_removed"] is True
        assert v["banner"] == "flatten_ok"
        assert v["passed"] is True

    def test_verify_fails_if_match_has_no_box(self, sample_pdf, temp_output):
        config = RuleConfig(enabled_keys=["email"], custom_rules=[])
        redact_pdf(sample_pdf, temp_output, config, mode="flatten", dpi=150)
        match = Match("email", "Email", "ghost@example.com", 0, 17, "medium")
        page = PageAnalysis(
            index=0, width=100, height=100, matches=[match], boxes=[], has_words=True,
        )
        v = _verify(temp_output, [page], "flatten", [0])
        assert v["matches_have_boxes"] is False
        assert v["passed"] is False
        assert v["banner"] == "fail"

    def test_custom_rule_redaction(self, sample_pdf, temp_output):
        config = RuleConfig(
            enabled_keys=[],
            custom_rules=[
                {"label": "Test ID", "pattern": r"TEST-\d{3}", "severity": "medium"}
            ]
        )
        report = redact_pdf(sample_pdf, temp_output, config)

        assert os.path.exists(temp_output)
        # Report should be generated even if no matches
        assert 'verification' in report

    def test_high_dpi_redaction(self, sample_pdf, temp_output):
        config = RuleConfig(enabled_keys=['email'], custom_rules=[])
        report = redact_pdf(sample_pdf, temp_output, config, dpi=300)

        assert os.path.exists(temp_output)
        # Higher DPI should still work
        file_size = os.path.getsize(temp_output)
        assert file_size > 0


class TestPadding:
    def test_default_padding_minimal(self):
        """Verify minimal padding since we use character-level boxes."""
        import inspect
        sig = inspect.signature(redact_pdf)
        pad_default = sig.parameters['pad'].default
        assert pad_default == 2, "Default padding should be 2 (minimal) with character-level extraction"
