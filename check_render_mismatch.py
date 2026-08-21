#!/usr/bin/env python3
"""
Check if there's a rendering mismatch between pdfplumber and pypdfium2.
This overlays pdfplumber word boxes on the pypdfium2 rendered image.
"""

import sys
import os
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

def check_render_alignment(pdf_path: str, page_num: int = 0):
    """Overlay word boxes on rendered image to check alignment."""

    print(f"Checking render alignment for: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num]
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)

        print(f"\nPage dimensions: {page.width} x {page.height} pts")

    # Render with pypdfium2
    scale = 3.0
    doc = pdfium.PdfDocument(pdf_path)
    pdf_page = doc[page_num]
    bitmap = pdf_page.render(scale=scale)
    img = bitmap.to_pil().convert("RGB")
    doc.close()

    print(f"Rendered image size: {img.size} px")
    print(f"Expected size: ({page.width * scale:.0f}, {page.height * scale:.0f}) px")

    # Draw boxes around every word
    draw = ImageDraw.Draw(img)

    # Draw first 20 words with their boxes
    for i, w in enumerate(words[:20]):
        x0, top, x1, bottom = w['x0'], w['top'], w['x1'], w['bottom']

        # Convert to image coordinates
        rect = [x0 * scale, top * scale, x1 * scale, bottom * scale]

        # Alternate colors so we can see individual boxes
        color = (255, 0, 0) if i % 2 == 0 else (0, 255, 0)
        draw.rectangle(rect, outline=color, width=2)

        # Draw the text coordinate values
        try:
            text = f"{w['text'][:8]}"
            draw.text((rect[0], rect[1] - 15), text, fill=color)
        except:
            pass

    output = "_work/alignment_check.png"
    img.save(output)

    print(f"\n✅ Saved to: {output}")
    print(f"\nInstructions:")
    print(f"  1. Open {output}")
    print(f"  2. Check if red/green boxes align with the actual words")
    print(f"  3. If boxes are BELOW the words → coordinate offset problem")
    print(f"  4. If boxes are correctly placed → our code is fine")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_render_mismatch.py <path_to_pdf> [page_number]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2]) - 1 if len(sys.argv) > 2 else 0
    os.makedirs("_work", exist_ok=True)

    try:
        check_render_alignment(pdf_path, page_num)
    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
