"""
Detection engine.

Takes raw text + a configuration (which predefined types are enabled, plus any
user-supplied custom rules) and returns a clean, de-duplicated, non-overlapping
list of matches with character offsets. Those offsets are what the redactor maps
back onto PDF word boxes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .detectors import Detector, PREDEFINED_BY_KEY

MAX_CUSTOM_PATTERN_LEN = 256
_MAX_CUSTOM_QUANTIFIERS = 20


@dataclass
class Match:
    type: str          # detector key, e.g. "us_ssn" or "custom:Employee ID"
    label: str         # human label
    value: str         # the raw matched text
    start: int         # char offset (inclusive)
    end: int           # char offset (exclusive)
    severity: str
    validated: bool = False   # True if confirmed by a checksum/structural rule

    @property
    def masked(self) -> str:
        """A safe preview for audit logs: keep last 2 chars, mask the rest."""
        v = self.value
        if len(v) <= 2:
            return "•" * len(v)
        return "•" * (len(v) - 2) + v[-2:]


@dataclass
class RuleConfig:
    """What the caller wants detected."""
    enabled_keys: list[str]                       # predefined detector keys
    custom_rules: list[dict]                      # [{"label","pattern","severity"?}]
    warnings: list[str] = field(default_factory=list)

    @staticmethod
    def default() -> "RuleConfig":
        keys = [k for k, d in PREDEFINED_BY_KEY.items() if d.default_on]
        return RuleConfig(enabled_keys=keys, custom_rules=[])


def _pattern_too_complex(pattern: str) -> bool:
    quantifiers = pattern.count("*") + pattern.count("+") + pattern.count("?")
    if quantifiers > _MAX_CUSTOM_QUANTIFIERS:
        return True
    if re.search(r"(\.\*){3,}|(\+\+|\*\*)", pattern):
        return True
    return False


def _compile_custom_pattern(pattern: str):
    """Compile a user regex. Prefer RE2 when installed; otherwise stdlib re."""
    try:
        import re2  # type: ignore
        return re2.compile(pattern)
    except ImportError:
        pass
    except Exception as e:
        raise ValueError(str(e)) from e
    return re.compile(pattern)


def resolve_detectors(config: "RuleConfig") -> tuple[list[Detector], list[str]]:
    """Predefined detectors plus custom rules that passed compile/safety checks."""
    detectors: list[Detector] = [
        PREDEFINED_BY_KEY[k] for k in config.enabled_keys if k in PREDEFINED_BY_KEY
    ]
    warnings: list[str] = []
    for r in config.custom_rules:
        label = (r.get("label") or "Custom Rule").strip()
        pattern = r.get("pattern") or ""
        if not pattern:
            continue
        if len(pattern) > MAX_CUSTOM_PATTERN_LEN:
            warnings.append(
                f"Skipped custom rule '{label}': pattern exceeds "
                f"{MAX_CUSTOM_PATTERN_LEN} characters"
            )
            continue
        if _pattern_too_complex(pattern):
            warnings.append(
                f"Skipped custom rule '{label}': pattern is too complex"
            )
            continue
        try:
            compiled = _compile_custom_pattern(pattern)
        except (ValueError, re.error) as e:
            warnings.append(f"Skipped custom rule '{label}': {e}")
            continue
        det = Detector(
            key=f"custom:{label}",
            label=label,
            category="Custom",
            pattern=pattern,
            severity=r.get("severity", "medium"),
        )
        det._compiled = compiled
        detectors.append(det)
    config.warnings = warnings
    return detectors, warnings


def _priority(m: "Match") -> tuple:
    """Higher tuple wins when several detectors fire on the same text."""
    sev = {"high": 3, "medium": 2, "low": 1}.get(m.severity, 0)
    return (1 if m.validated else 0, sev, m.end - m.start)


def _merge_overlaps(matches: list[Match], text: str) -> list[Match]:
    """
    Merge overlapping matches into clusters, cover the union of each cluster,
    and label the cluster with its most authoritative member. This means a
    checksum-validated TFN beats a generic 'phone' match on the same digits,
    while redaction still covers everything any detector flagged (fail-safe).
    """
    if not matches:
        return []
    matches.sort(key=lambda m: (m.start, m.end))
    clusters: list[tuple[int, int, list[Match]]] = []
    cstart, cend, members = matches[0].start, matches[0].end, [matches[0]]
    for m in matches[1:]:
        if m.start < cend:                       # overlapping
            cend = max(cend, m.end)
            members.append(m)
        else:                                    # gap -> new cluster
            clusters.append((cstart, cend, members))
            cstart, cend, members = m.start, m.end, [m]
    clusters.append((cstart, cend, members))

    out: list[Match] = []
    for s, e, mem in clusters:
        rep = max(mem, key=_priority)
        out.append(
            Match(
                type=rep.type, label=rep.label, value=text[s:e],
                start=s, end=e, severity=rep.severity, validated=rep.validated,
            )
        )
    return out


def detect(
    text: str,
    config: Optional[RuleConfig] = None,
    *,
    detectors: Optional[list[Detector]] = None,
) -> list[Match]:
    config = config or RuleConfig.default()
    if detectors is None:
        detectors, _ = resolve_detectors(config)

    matches: list[Match] = []
    for det in detectors:
        is_validated = det.validator is not None
        for m in det.finditer(text):
            matches.append(
                Match(
                    type=det.key,
                    label=det.label,
                    value=m.group(0),
                    start=m.start(),
                    end=m.end(),
                    severity=det.severity,
                    validated=is_validated,
                )
            )
    return _merge_overlaps(matches, text)


def summarize(matches: list[Match]) -> dict:
    by_type: dict[str, dict] = {}
    for m in matches:
        slot = by_type.setdefault(
            m.type, {"label": m.label, "severity": m.severity, "count": 0, "samples": []}
        )
        slot["count"] += 1
        if len(slot["samples"]) < 3:
            slot["samples"].append(m.masked)
    return {"total": len(matches), "by_type": by_type}
