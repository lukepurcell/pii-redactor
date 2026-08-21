#!/usr/bin/env python3
"""
Diagnostic script to understand coordinate systems and identify the misalignment issue.
"""

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

def diagnose_coordinates(pdf_path: str):
    """Check coordinate system behavior."""

    print("=" * 60)
    print("COORDINATE SYSTEM DIAGNOSIS")
    print("=" * 60)

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]

        print(f"\n📄 Page dimensions:")
        print(f"   Width:  {page.width} pts")
        print(f"   Height: {page.height} pts")

        # Get some words
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)

        print(f"\n📝 Sample words with coordinates:")
        for i, w in enumerate(words[:5]):
            print(f"\n   Word {i+1}: '{w['text']}'")
            print(f"   x0={w['x0']:.2f}, top={w['top']:.2f}")
            print(f"   x1={w['x1']:.2f}, bottom={w['bottom']:.2f}")
            print(f"   Height: {w['bottom'] - w['top']:.2f} pts")

            # Check if top < bottom (top-down) or bottom < top (bottom-up)
            if w['top'] < w['bottom']:
                print(f"   ✅ Top < Bottom (origin at TOP-LEFT, Y increases DOWN)")
            else:
                print(f"   ⚠️  Top > Bottom (origin at BOTTOM-LEFT, Y increases UP)")

    # Now check how pypdfium2 renders
    scale = 2.0
    doc = pdfium.PdfDocument(pdf_path)
    page = doc[0]
    bitmap = page.render(scale=scale)
    img = bitmap.to_pil().convert("RGB")
    doc.close()

    print(f"\n🖼️  Rendered image:")
    print(f"   Size: {img.size} pixels")
    print(f"   Expected: ({page.width * scale:.0f}, {page.height * scale:.0f})")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    import sys
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "samples/sample_pii.pdf"

    try:
        diagnose_coordinates(pdf_path)
    except Exception as e:
        print(f"Error: {e}")
        print("\nUsage: python diagnose_coords.py <path_to_pdf>")
