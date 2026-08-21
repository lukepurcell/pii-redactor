#!/usr/bin/env python3
"""
Quick visual test to compare redaction accuracy before/after improvements.
Run this to see the difference in box coverage.
"""

import os
from pii_engine.redactor import redact_pdf
from pii_engine.engine import RuleConfig

def test_redaction():
    """Test redaction on the sample document."""
    sample = "samples/sample_pii.pdf"

    if not os.path.exists(sample):
        print("❌ Sample PDF not found. Run: python samples/make_sample.py samples/sample_pii.pdf")
        return

    output = "_work/test_redaction.pdf"
    os.makedirs("_work", exist_ok=True)

    config = RuleConfig(
        enabled_keys=['credit_card', 'us_ssn', 'email', 'phone', 'au_tfn', 'au_medicare'],
        custom_rules=[]
    )

    print("🔍 Running redaction with improved box merging and padding...")
    report = redact_pdf(sample, output, config, dpi=200)

    print(f"\n✅ Redaction complete!")
    print(f"   📄 Output: {output}")
    print(f"   📊 Pages processed: {report['pages']}")
    print(f"   🔒 Total redactions: {report['total_redactions']}")

    # Verification
    verification = report['verification']
    if verification['passed']:
        print(f"\n✅ VERIFICATION PASSED")
        print(f"   No PII values found in output (complete coverage)")
    else:
        print(f"\n⚠️  VERIFICATION WARNING")
        print(f"   Leaked values: {verification['leaked_values']}")

    # Summary by type
    print(f"\n📋 Redactions by type:")
    for pii_type, info in report['summary']['by_type'].items():
        count = info['count']
        severity = info['severity']
        samples = ', '.join(info['samples'][:2])
        print(f"   • {info['label']}: {count} ({severity}) - e.g., {samples}")

    print(f"\n🎨 To view results:")
    print(f"   1. Open: {output}")
    print(f"   2. Compare with: {sample}")
    print(f"   3. Or run the web app: python app.py")

if __name__ == "__main__":
    test_redaction()
