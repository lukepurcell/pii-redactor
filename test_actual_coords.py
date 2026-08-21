#!/usr/bin/env python3
"""
Test the actual coordinate mapping with a real email address.
"""

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from pii_engine.engine import detect, RuleConfig

def test_email_coords(pdf_path: str):
    """Find an email and draw its box."""

    config = RuleConfig(enabled_keys=['email'], custom_rules=[])

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]

        # Extract text and words
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        parts, offsets, cursor = [], [], 0
        for w in words:
            token = w["text"]
            offsets.append((cursor, cursor + len(token), w))
            parts.append(token)
            cursor += len(token) + 1

        text = " ".join(parts)

        # Detect emails
        matches = detect(text, config)

        if not matches:
            print("No emails found")
            return

        print(f"Found {len(matches)} email(s):")
        for m in matches:
            print(f"\n  Email: {m.value}")
            print(f"  Text span: [{m.start}:{m.end}]")

            # Find matching words
            match_words = []
            for w_start, w_end, w in offsets:
                if w_start < m.end and w_end > m.start:
                    match_words.append(w)
                    print(f"    Word: '{w['text']}' at x0={w['x0']:.1f}, top={w['top']:.1f}, x1={w['x1']:.1f}, bottom={w['bottom']:.1f}")

            if match_words:
                # Calculate bounding box
                x0 = min(w['x0'] for w in match_words)
                x1 = max(w['x1'] for w in match_words)
                top = min(w['top'] for w in match_words)
                bottom = max(w['bottom'] for w in match_words)

                print(f"\n  Merged box:")
                print(f"    x0={x0:.1f}, top={top:.1f}")
                print(f"    x1={x1:.1f}, bottom={bottom:.1f}")
                print(f"    Width: {x1-x0:.1f}, Height: {bottom-top:.1f}")

                # Render the page
                scale = 2.0
                doc = pdfium.PdfDocument(pdf_path)
                pdf_page = doc[0]
                bitmap = pdf_page.render(scale=scale)
                img = bitmap.to_pil().convert("RGB")
                doc.close()

                # Draw the box
                draw = ImageDraw.Draw(img)
                pad = 3 * scale
                rect = [x0 * scale - pad, top * scale - pad,
                        x1 * scale + pad, bottom * scale + pad]

                print(f"\n  Drawing box on image (scale={scale}):")
                print(f"    Image size: {img.size}")
                print(f"    Box: {[f'{x:.1f}' for x in rect]}")

                # Draw with red outline so we can see it
                draw.rectangle(rect, outline=(255, 0, 0), width=3)

                # Save for inspection
                output = "_work/coord_test.png"
                img.save(output)
                print(f"\n  ✅ Saved to: {output}")
                print(f"     Open this file to verify the red box covers the email")

if __name__ == "__main__":
    import os
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "samples/sample_pii.pdf"
    os.makedirs("_work", exist_ok=True)

    try:
        test_email_coords(pdf_path)
    except Exception as e:
        import traceback
        print(f"Error: {e}")
        traceback.print_exc()
