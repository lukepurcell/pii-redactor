#!/usr/bin/env python3
"""
Aggressive redaction mode for PDFs with complex layouts (tables, underlines).
Uses extra-large padding to ensure complete coverage.
"""

import sys
import os
from pii_engine.redactor import redact_pdf
from pii_engine.engine import RuleConfig

def redact_with_aggressive_padding(input_path: str, output_path: str, padding: int = 10):
    """
    Redact with aggressive padding for complex layouts.

    Args:
        input_path: Source PDF
        output_path: Where to save redacted PDF
        padding: Padding in points (default 10 = very aggressive)
    """
    config = RuleConfig.default()

    print(f"🔒 Aggressive redaction mode")
    print(f"   Input: {input_path}")
    print(f"   Padding: {padding} pts (extra coverage for tables/underlines)")

    report = redact_pdf(input_path, output_path, config, dpi=200, pad=padding)

    print(f"\n✅ Complete!")
    print(f"   Output: {output_path}")
    print(f"   Redactions: {report['total_redactions']}")

    if report['verification']['passed']:
        print(f"   ✅ Verification PASSED - no PII leaked")
    else:
        print(f"   ⚠️  Warning: {len(report['verification']['leaked_values'])} values may have leaked")
        print(f"      Leaked: {report['verification']['leaked_values']}")

    return report

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python redact_aggressive.py <input.pdf> <output.pdf> [padding]")
        print("\nExample:")
        print("  python redact_aggressive.py input.pdf redacted.pdf 10")
        print("\nPadding guide:")
        print("  6  = Default (normal documents)")
        print("  10 = Aggressive (tables, underlines)")
        print("  15 = Maximum (very complex layouts)")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_pdf = sys.argv[2]
    padding = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    if not os.path.exists(input_pdf):
        print(f"❌ Error: {input_pdf} not found")
        sys.exit(1)

    try:
        redact_with_aggressive_padding(input_pdf, output_pdf, padding)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
