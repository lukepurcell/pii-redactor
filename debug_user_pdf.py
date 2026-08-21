#!/usr/bin/env python3
"""
Debug a PDF using the same analysis and padding logic as the web app.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageDraw

from pii_engine.engine import RuleConfig
from pii_engine.redactor import (
    DEFAULT_PAD,
    DEFAULT_VERTICAL_SHIFT_MM,
    DEFAULT_TOP_EXTRA_MM,
    DEFAULT_BOTTOM_EXTRA_MM,
    _image_rect,
    analyze_pdf,
    redact_pdf,
)


DEFAULT_KEYS = [
    "credit_card",
    "us_ssn",
    "email",
    "phone",
    "au_tfn",
    "au_medicare",
]


def _mask(value: str) -> str:
    if len(value) <= 2:
        return "•" * len(value)
    return "•" * (len(value) - 2) + value[-2:]


def debug_pdf(pdf_path: str, page_num: int = 0, dpi: int = 200,
              pad: int = DEFAULT_PAD,
              vertical_shift_mm: float = DEFAULT_VERTICAL_SHIFT_MM,
              top_extra_mm: float = DEFAULT_TOP_EXTRA_MM,
              bottom_extra_mm: float = DEFAULT_BOTTOM_EXTRA_MM):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    config = RuleConfig(enabled_keys=DEFAULT_KEYS, custom_rules=[])
    analyses = analyze_pdf(pdf_path, config)

    if page_num < 0 or page_num >= len(analyses):
        raise ValueError(f"Page {page_num + 1} is out of range. PDF has {len(analyses)} page(s).")

    page_analysis = analyses[page_num]
    scale = dpi / 72.0

    print("=" * 72)
    print(f"DEBUGGING: {pdf_path}")
    print(f"Page: {page_num + 1}/{len(analyses)}")
    print(f"DPI: {dpi}")
    print(f"Padding: {pad} pts")
    print(f"Vertical shift: {vertical_shift_mm:.2f} mm")
    print(f"Top extra height: {top_extra_mm:.2f} mm")
    print(f"Bottom extra height: {bottom_extra_mm:.2f} mm")
    print(f"Enabled detectors: {', '.join(DEFAULT_KEYS)}")
    print("=" * 72)
    print(f"\nPage dimensions: {page_analysis.width:.1f} x {page_analysis.height:.1f} pts")
    print(f"Detected items: {len(page_analysis.boxes)}\n")

    doc = pdfium.PdfDocument(pdf_path)
    img = doc[page_num].render(scale=scale).to_pil().convert("RGB")
    doc.close()
    draw = ImageDraw.Draw(img)

    for idx, box in enumerate(page_analysis.boxes, start=1):
        rect = _image_rect(
            box, scale, pad,
            vertical_shift_mm=vertical_shift_mm,
            top_extra_mm=top_extra_mm,
            bottom_extra_mm=bottom_extra_mm,
        )
        match = box.match
        color = {
            "high": (255, 0, 0),
            "medium": (255, 165, 0),
            "low": (255, 255, 0),
        }.get(match.severity, (128, 128, 128))

        draw.rectangle(rect, outline=color, width=max(2, int(scale)))
        draw.text((rect[0], max(0, rect[1] - 18)), f"#{idx}", fill=color)

        print(f"{idx}. {match.label}: {_mask(match.value)}")
        print(f"   Type: {match.type} | Severity: {match.severity} | Source: {box.source}")
        print(f"   Text: {match.value!r}")
        print(
            "   Box pts: "
            f"[{box.x0:.1f}, {box.top:.1f}] -> [{box.x1:.1f}, {box.bottom:.1f}]"
        )
        print(
            "   Box px:  "
            f"[{rect[0]:.1f}, {rect[1]:.1f}] -> [{rect[2]:.1f}, {rect[3]:.1f}]"
        )
        print()

    work_dir = Path("_work")
    work_dir.mkdir(exist_ok=True)
    stem = Path(pdf_path).stem
    overlay_path = work_dir / f"{stem}_page_{page_num + 1}_debug.png"
    redacted_path = work_dir / f"{stem}_debug_redacted.pdf"

    img.save(overlay_path)
    report = redact_pdf(
        pdf_path, str(redacted_path), config, dpi=dpi, pad=pad,
        vertical_shift_mm=vertical_shift_mm,
        top_extra_mm=top_extra_mm,
        bottom_extra_mm=bottom_extra_mm,
    )

    print("=" * 72)
    print(f"Overlay image: {overlay_path}")
    print(f"Redacted PDF:  {redacted_path}")
    print(
        "Verification: "
        f"{'PASSED' if report['verification']['passed'] else 'FAILED'}"
    )
    if report["verification"]["leaked_values"]:
        print(f"Leaked values: {report['verification']['leaked_values']}")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Render a page overlay and redacted PDF using the app's current logic."
    )
    parser.add_argument("pdf_path", help="Path to the PDF to inspect")
    parser.add_argument(
        "page",
        nargs="?",
        type=int,
        default=1,
        help="1-based page number to visualize (default: 1)",
    )
    parser.add_argument("--dpi", type=int, default=200, help="Render DPI (default: 200)")
    parser.add_argument(
        "--pad",
        type=int,
        default=DEFAULT_PAD,
        help=f"Padding in points (default: {DEFAULT_PAD})",
    )
    parser.add_argument(
        "--vertical-shift-mm",
        type=float,
        default=DEFAULT_VERTICAL_SHIFT_MM,
        help=f"Move boxes upward by this many mm (default: {DEFAULT_VERTICAL_SHIFT_MM})",
    )
    parser.add_argument(
        "--top-extra-mm",
        type=float,
        default=DEFAULT_TOP_EXTRA_MM,
        help=f"Extra height to add above the box in mm (default: {DEFAULT_TOP_EXTRA_MM})",
    )
    parser.add_argument(
        "--bottom-extra-mm",
        type=float,
        default=DEFAULT_BOTTOM_EXTRA_MM,
        help=f"Extra height to add below the box in mm (default: {DEFAULT_BOTTOM_EXTRA_MM})",
    )
    args = parser.parse_args()

    debug_pdf(
        args.pdf_path,
        page_num=args.page - 1,
        dpi=args.dpi,
        pad=args.pad,
        vertical_shift_mm=args.vertical_shift_mm,
        top_extra_mm=args.top_extra_mm,
        bottom_extra_mm=args.bottom_extra_mm,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"\nError: {exc}")
        traceback.print_exc()
        raise SystemExit(1)
