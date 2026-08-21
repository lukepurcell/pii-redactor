"""
PII detector library.

Each detector is a regex pattern plus an optional *validator* function. The
validator lets us go beyond naive pattern-matching (which produces a lot of
false positives) and confirm a candidate with a checksum or structural rule.
This is what makes detections trustworthy enough to act on automatically.

Adding a new predefined type is a one-line entry in PREDEFINED_DETECTORS.
End users can also supply their own ad-hoc rules at runtime (see engine.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
#  Validators  (return True if a candidate string is a *real* instance)
# --------------------------------------------------------------------------- #
def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def luhn_valid(s: str) -> bool:
    """Luhn checksum — used by all major credit-card networks."""
    d = [int(c) for c in _digits(s)]
    if len(d) < 13:
        return False
    checksum, parity = 0, len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def abn_valid(s: str) -> bool:
    """Australian Business Number — 11 digits, weighted mod-89 checksum."""
    d = _digits(s)
    if len(d) != 11:
        return False
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    nums = [int(c) for c in d]
    nums[0] -= 1
    return sum(w * n for w, n in zip(weights, nums)) % 89 == 0


def tfn_valid(s: str) -> bool:
    """Australian Tax File Number — 8 or 9 digit weighted checksum."""
    d = _digits(s)
    if len(d) == 9:
        weights = [1, 4, 3, 7, 5, 8, 6, 9, 10]
    elif len(d) == 8:
        weights = [10, 7, 8, 4, 6, 3, 5, 1]
    else:
        return False
    return sum(w * int(n) for w, n in zip(weights, d)) % 11 == 0


def medicare_valid(s: str) -> bool:
    """Australian Medicare card — first 8 digits + check digit."""
    d = _digits(s)
    if len(d) < 10:
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    base = [int(c) for c in d[:8]]
    check = int(d[8])
    return sum(w * n for w, n in zip(weights, base)) % 10 == check


def phone_plausible(s: str) -> bool:
    """A real phone has 7–11 significant digits; this rejects fragments of
    longer numeric runs (e.g. a 16-digit card) that happen to look phone-ish."""
    n = len(_digits(s))
    return 7 <= n <= 11


def ssn_plausible(s: str) -> bool:
    """Rule out structurally-invalid US SSNs (000 area, 00 group, etc.)."""
    d = _digits(s)
    if len(d) != 9:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    if area in {"000", "666"} or area[0] == "9":
        return False
    if group == "00" or serial == "0000":
        return False
    return True


# --------------------------------------------------------------------------- #
#  Detector model
# --------------------------------------------------------------------------- #
@dataclass
class Detector:
    key: str                      # stable id, e.g. "us_ssn"
    label: str                    # human label shown in UI
    category: str                 # grouping for the UI
    pattern: str                  # regex source
    severity: str = "medium"      # low | medium | high
    flags: int = re.IGNORECASE
    validator: Optional[Callable[[str], bool]] = None
    default_on: bool = True
    description: str = ""
    _compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        self._compiled = re.compile(self.pattern, self.flags)

    def finditer(self, text: str):
        for m in self._compiled.finditer(text):
            if self.validator is None or self.validator(m.group(0)):
                yield m


# --------------------------------------------------------------------------- #
#  Predefined detectors
# --------------------------------------------------------------------------- #
PREDEFINED_DETECTORS: list[Detector] = [
    # ---- Financial -------------------------------------------------------- #
    Detector(
        key="credit_card",
        label="Credit / Debit Card Number",
        category="Financial",
        pattern=r"\b(?:\d[ -]*?){13,16}\b",
        severity="high",
        validator=luhn_valid,
        description="13–16 digit card numbers, confirmed with the Luhn checksum.",
    ),
    Detector(
        key="iban",
        label="IBAN (Bank Account)",
        category="Financial",
        pattern=r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        severity="high",
        description="International Bank Account Numbers.",
    ),
    # ---- Government / National IDs --------------------------------------- #
    Detector(
        key="us_ssn",
        label="US Social Security Number",
        category="Government ID",
        pattern=r"\b(?:\d{3}-\d{2}-\d{4}|\d{9})\b",
        severity="high",
        validator=ssn_plausible,
        description="US SSNs in NNN-NN-NNNN or 9-digit form, structurally validated.",
    ),
    Detector(
        key="us_passport",
        label="US Passport Number",
        category="Government ID",
        pattern=r"\b[A-Z]?\d{8,9}\b",
        severity="high",
        default_on=False,
        description="US passport numbers (broad; off by default to limit noise).",
    ),
    Detector(
        key="au_tfn",
        label="AU Tax File Number",
        category="Government ID",
        pattern=r"\b\d{3}\s?\d{3}\s?\d{2,3}\b",
        severity="high",
        validator=tfn_valid,
        description="Australian TFN, confirmed with the ATO checksum.",
    ),
    Detector(
        key="au_medicare",
        label="AU Medicare Number",
        category="Government ID",
        pattern=r"\b\d{4}\s?\d{5}\s?\d{1,2}\b",
        severity="high",
        validator=medicare_valid,
        description="Australian Medicare card number, checksum-validated.",
    ),
    Detector(
        key="au_abn",
        label="AU Business Number (ABN)",
        category="Government ID",
        pattern=r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b",
        severity="low",
        validator=abn_valid,
        default_on=False,
        description="Australian Business Number, mod-89 validated.",
    ),
    # ---- Contact / Identity --------------------------------------------- #
    Detector(
        key="email",
        label="Email Address",
        category="Contact",
        pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        severity="medium",
        description="RFC-ish email addresses.",
    ),
    Detector(
        key="phone",
        label="Phone Number",
        category="Contact",
        pattern=(
            r"(?<![A-Za-z0-9])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?"
            r"\d{3,4}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?(?![A-Za-z0-9])"
        ),
        severity="medium",
        default_on=True,
        validator=phone_plausible,
        description="US, AU and international phone formats.",
    ),
    Detector(
        key="ip_address",
        label="IP Address (IPv4)",
        category="Contact",
        pattern=r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        severity="low",
        default_on=False,
        description="IPv4 addresses.",
    ),
    Detector(
        key="dob",
        label="Date of Birth / Date",
        category="Contact",
        pattern=(
            r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
            r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b"
        ),
        severity="low",
        default_on=False,
        description="Common date formats (broad; off by default).",
    ),
]

PREDEFINED_BY_KEY = {d.key: d for d in PREDEFINED_DETECTORS}
