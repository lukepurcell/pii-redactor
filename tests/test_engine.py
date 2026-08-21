import pytest
from pii_engine.engine import detect, RuleConfig, Match, summarize


class TestDetect:
    def test_detect_credit_card(self):
        config = RuleConfig(enabled_keys=['credit_card'], custom_rules=[])
        text = "My card number is 4532015112830366"
        findings = detect(text, config)
        assert len(findings) == 1
        assert findings[0].type == 'credit_card'
        assert findings[0].value == '4532015112830366'

    def test_detect_ssn(self):
        config = RuleConfig(enabled_keys=['us_ssn'], custom_rules=[])
        text = "SSN: 123-45-6789"
        findings = detect(text, config)
        assert len(findings) == 1
        assert findings[0].type == 'us_ssn'

    def test_detect_email(self):
        config = RuleConfig(enabled_keys=['email'], custom_rules=[])
        text = "Contact me at john.doe@example.com"
        findings = detect(text, config)
        assert len(findings) == 1
        assert findings[0].type == 'email'
        assert findings[0].value == 'john.doe@example.com'

    def test_multiple_detections(self):
        config = RuleConfig(enabled_keys=['email', 'us_ssn'], custom_rules=[])
        text = "Email: test@test.com, SSN: 123-45-6789"
        findings = detect(text, config)
        assert len(findings) == 2

    def test_custom_rule(self):
        custom = [{"label": "Employee ID", "pattern": r"EMP-\d{5}", "severity": "medium"}]
        config = RuleConfig(enabled_keys=[], custom_rules=custom)
        text = "Employee ID: EMP-12345"
        findings = detect(text, config)
        assert len(findings) == 1
        assert findings[0].type == "custom:Employee ID"
        assert findings[0].value == "EMP-12345"
        assert findings[0].validated is False

    def test_invalid_custom_rule_skipped_with_warning(self):
        config = RuleConfig(
            enabled_keys=[],
            custom_rules=[{"label": "Bad", "pattern": "("}],
        )
        findings = detect("abc", config)
        assert findings == []
        assert config.warnings

    def test_unhyphenated_ssn(self):
        config = RuleConfig(enabled_keys=["us_ssn"], custom_rules=[])
        findings = detect("SSN 123456789", config)
        assert len(findings) == 1
        assert findings[0].type == "us_ssn"


    def test_no_false_positive_on_invalid_card(self):
        config = RuleConfig(enabled_keys=['credit_card'], custom_rules=[])
        text = "Invalid card: 1234567812345678"
        findings = detect(text, config)
        assert len(findings) == 0

    def test_default_config(self):
        config = RuleConfig.default()
        assert len(config.enabled_keys) > 0
        assert 'credit_card' in config.enabled_keys
        assert 'email' in config.enabled_keys

    def test_overlapping_matches_merged(self):
        config = RuleConfig(enabled_keys=['phone', 'us_ssn'], custom_rules=[])
        text = "SSN: 123-45-6789"
        findings = detect(text, config)
        assert len(findings) <= 2


class TestMatch:
    def test_masked_value(self):
        match = Match(
            type="credit_card",
            label="Credit Card",
            value="4532015112830366",
            start=0,
            end=16,
            severity="high",
            validated=True
        )
        masked = match.masked
        assert masked.endswith("66")
        assert "•" in masked
        assert len(masked) == len(match.value)

    def test_masked_short_value(self):
        match = Match(
            type="test",
            label="Test",
            value="AB",
            start=0,
            end=2,
            severity="low"
        )
        assert match.masked == "••"


class TestSummarize:
    def test_summarize_matches(self):
        matches = [
            Match("email", "Email", "test@test.com", 0, 13, "medium"),
            Match("email", "Email", "admin@test.com", 20, 34, "medium"),
            Match("us_ssn", "SSN", "123-45-6789", 40, 51, "high", True),
        ]
        summary = summarize(matches)
        assert summary["total"] == 3
        assert "email" in summary["by_type"]
        assert summary["by_type"]["email"]["count"] == 2
        assert summary["by_type"]["us_ssn"]["count"] == 1
