import pytest
from pii_engine.detectors import (
    luhn_valid,
    ssn_plausible,
    tfn_valid,
    medicare_valid,
    abn_valid,
    phone_plausible,
    PREDEFINED_DETECTORS,
    PREDEFINED_BY_KEY,
    Detector
)


class TestLuhnValidator:
    def test_valid_credit_card(self):
        assert luhn_valid("4532015112830366") is True
        assert luhn_valid("5425233430109903") is True

    def test_invalid_credit_card(self):
        assert luhn_valid("1234567812345678") is False
        assert luhn_valid("4532015112830367") is False

    def test_with_spaces_and_hyphens(self):
        assert luhn_valid("4532-0151-1283-0366") is True
        assert luhn_valid("4532 0151 1283 0366") is True


class TestSSNValidator:
    def test_valid_ssn(self):
        assert ssn_plausible("123-45-6789") is True
        assert ssn_plausible("123456789") is True

    def test_invalid_ssn_patterns(self):
        assert ssn_plausible("000-45-6789") is False
        assert ssn_plausible("123-00-6789") is False
        assert ssn_plausible("123-45-0000") is False
        assert ssn_plausible("666-45-6789") is False


class TestAUTFNValidator:
    def test_valid_tfn(self):
        assert tfn_valid("123456782") is True

    def test_invalid_tfn(self):
        assert tfn_valid("123456789") is False


class TestAUMedicareValidator:
    def test_valid_medicare(self):
        assert medicare_valid("2950014051") is True

    def test_invalid_medicare(self):
        assert medicare_valid("2123456789") is False


class TestAUABNValidator:
    def test_valid_abn(self):
        assert abn_valid("51824753556") is True

    def test_invalid_abn(self):
        assert abn_valid("12345678901") is False


class TestPhoneValidator:
    def test_valid_phone(self):
        assert phone_plausible("1234567") is True
        assert phone_plausible("12345678901") is True

    def test_invalid_phone_too_short(self):
        assert phone_plausible("123456") is False

    def test_invalid_phone_too_long(self):
        assert phone_plausible("123456789012") is False

    def test_phone_not_matched_inside_alphanumeric_token(self):
        detector = PREDEFINED_BY_KEY["phone"]
        text = "Doc ID: 3d0f0b57ef4125201a125022575227a7ef1e84a7"
        matches = list(detector.finditer(text))
        assert matches == []


class TestDetectorRegistry:
    def test_predefined_detectors_exist(self):
        assert len(PREDEFINED_DETECTORS) > 0
        assert len(PREDEFINED_BY_KEY) > 0

    def test_detector_has_required_fields(self):
        for detector in PREDEFINED_DETECTORS:
            assert hasattr(detector, 'key')
            assert hasattr(detector, 'label')
            assert hasattr(detector, 'category')
            assert hasattr(detector, 'pattern')
            assert hasattr(detector, 'severity')

    def test_predefined_by_key_matches(self):
        assert "credit_card" in PREDEFINED_BY_KEY
        assert "us_ssn" in PREDEFINED_BY_KEY
        assert "email" in PREDEFINED_BY_KEY

    def test_detector_finditer(self):
        detector = PREDEFINED_BY_KEY["email"]
        text = "Contact me at test@example.com or admin@test.org"
        matches = list(detector.finditer(text))
        assert len(matches) == 2
