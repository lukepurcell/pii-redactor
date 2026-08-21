"""
PDF redaction engine.

Redaction strategy: locate-then-flatten.
  1. Extract every word with its bounding box (pdfplumber).
  2. Run PII detection over the reconstructed page text.
  3. Map each detected character span back to the word boxes it covers.
  4. Render each page to a raster image (pypdfium2 / PDFium).
  5. Paint solid boxes over the PII regions on the image.
  6. Rebuild the PDF from those images (reportlab).

Why flatten the whole page to an image?
  Drawing a black rectangle on top of a normal PDF does NOT remove the text —
  it sits underneath and is still selectable/extractable, which is the classic
  redaction failure. By rendering to an image we discard the entire text layer;
  the only thing that survives is pixels, and the PII pixels are painted over.
  We then *prove* this by re-extracting text from the output and confirming the
  PII strings are gone.

  Trade-off: the output is not text-searchable. A production version could use
  PyMuPDF's apply_redactions() to surgically remove only the PII runs while
  keeping the rest of the text selectable. The detection layer is identical;
  only step 4-6 change. See README.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .engine import detect, summarize, RuleConfig, Match, resolve_detectors

DEFAULT_PAD = 2
DEFAULT_VERTICAL_SHIFT_MM = 1.0
DEFAULT_TOP_EXTRA_MM = 0.0
DEFAULT_BOTTOM_EXTRA_MM = 0.0
MM_TO_PT = 72.0 / 25.4
VALID_MODES = ("surgical", "flatten")


@dataclass
class Box:
    x0: float
    top: float
    x1: float
    bottom: float
    match: Match
    source: str = "word"


@dataclass
class PageAnalysis:
    index: int
    width: float
    height: float
    boxes: list[Box] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    has_words: bool = False


def _page_words_and_text(page):
    """Return (joined_text, words_with_offsets). Offsets index into joined_text."""
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    parts, offsets, cursor = [], [], 0
    for w in words:
        token = w["text"]
        offsets.append((cursor, cursor + len(token), w))
        parts.append(token)
        cursor += len(token) + 1          # +1 for the joining space
    return " ".join(parts), offsets


def _chars_for_word(page, word: dict, tolerance: float = 1.5) -> list[dict]:
    chars = []
    for c in page.chars:
        text = c.get("text", "")
        if not text or text.isspace():
            continue
        if (c["x1"] >= word["x0"] - tolerance and c["x0"] <= word["x1"] + tolerance
                and c["bottom"] >= word["top"] - tolerance
                and c["top"] <= word["bottom"] + tolerance):
            chars.append(c)
    chars.sort(key=lambda c: (round(c["top"], 3), c["x0"]))
    return chars


def _char_boxes_for_match(page, offsets, match: Match) -> list[Box]:
    boxes: list[Box] = []
    for w_start, w_end, word in offsets:
        if w_start >= match.end or w_end <= match.start:
            continue

        word_text = word["text"]
        local_start = max(0, match.start - w_start)
        local_end = min(len(word_text), match.end - w_start)
        if local_start >= local_end:
            continue

        chars = _chars_for_word(page, word)
        if len(chars) != len(word_text):
            return []

        match_chars = chars[local_start:local_end]
        if not match_chars:
            continue

        boxes.append(
            Box(
                x0=min(c["x0"] for c in match_chars),
                top=min(c["top"] for c in match_chars),
                x1=max(c["x1"] for c in match_chars),
                bottom=max(c["bottom"] for c in match_chars),
                match=match,
                source="char",
            )
        )
    return boxes


def _merge_boxes_for_match(boxes: list[Box]) -> Box:
    """Merge multiple word boxes into a single bounding box covering all of them."""
    if not boxes:
        raise ValueError("Cannot merge empty box list")
    if len(boxes) == 1:
        return boxes[0]

    x0 = min(b.x0 for b in boxes)
    x1 = max(b.x1 for b in boxes)
    top = min(b.top for b in boxes)
    bottom = max(b.bottom for b in boxes)

    source = "char" if all(b.source == "char" for b in boxes) else "word"
    return Box(x0=x0, top=top, x1=x1, bottom=bottom, match=boxes[0].match, source=source)


def _word_boxes_for_match(offsets, match: Match) -> list[Box]:
    boxes = []
    for w_start, w_end, w in offsets:
        if w_start < match.end and w_end > match.start:
            boxes.append(
                Box(
                    x0=w["x0"],
                    top=w["top"],
                    x1=w["x1"],
                    bottom=w["bottom"],
                    match=match,
                    source="word",
                )
            )
    return boxes


def _hybrid_match_box(char_boxes: list[Box], word_boxes: list[Box]) -> Box | None:
    if not char_boxes and not word_boxes:
        return None
    if not char_boxes:
        return _merge_boxes_for_match(word_boxes)
    if not word_boxes:
        return _merge_boxes_for_match(char_boxes)

    char_box = _merge_boxes_for_match(char_boxes)
    word_box = _merge_boxes_for_match(word_boxes)
    return Box(
        x0=min(char_box.x0, word_box.x0),
        top=min(char_box.top, word_box.top),
        x1=max(char_box.x1, word_box.x1),
        bottom=max(char_box.bottom, word_box.bottom),
        match=char_box.match,
        source="hybrid",
    )


def _image_rect(
        box: Box, scale: float, pad: int,
        vertical_shift_mm: float = DEFAULT_VERTICAL_SHIFT_MM,
        top_extra_mm: float = DEFAULT_TOP_EXTRA_MM,
        bottom_extra_mm: float = DEFAULT_BOTTOM_EXTRA_MM) -> list[float]:
    h_pad = pad * scale
    top_pad = (pad + 5) * scale + top_extra_mm * MM_TO_PT * scale
    bottom_pad = (pad + 1) * scale + bottom_extra_mm * MM_TO_PT * scale
    y_shift = vertical_shift_mm * MM_TO_PT * scale
    return [
        box.x0 * scale - h_pad,
        box.top * scale - top_pad - y_shift,
        box.x1 * scale + h_pad,
        box.bottom * scale + bottom_pad - y_shift,
    ]


def analyze_pdf(path: str, config: RuleConfig) -> list[PageAnalysis]:
    analyses: list[PageAnalysis] = []
    detectors, _ = resolve_detectors(config)
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            text, offsets = _page_words_and_text(page)
            matches = detect(text, config, detectors=detectors)
            pa = PageAnalysis(
                index=i, width=page.width, height=page.height,
                matches=matches, has_words=bool(offsets),
            )

            for m in matches:
                word_boxes = _word_boxes_for_match(offsets, m)
                match_boxes = _char_boxes_for_match(page, offsets, m)
                merged = _hybrid_match_box(match_boxes, word_boxes)
                if merged:
                    pa.boxes.append(merged)

            analyses.append(pa)
    return analyses


def _render_page_image(pdf_doc, index: int, scale: float) -> Image.Image:
    page = pdf_doc[index]
    bitmap = page.render(scale=scale)
    return bitmap.to_pil().convert("RGB")


def _flatten_pages(
        input_path: str, analyses: list[PageAnalysis],
        dpi: int, box_color, pad: int,
        vertical_shift_mm: float, top_extra_mm: float,
        bottom_extra_mm: float) -> list[tuple[Image.Image, tuple[float, float]]]:
    scale = dpi / 72.0
    pdf_doc = pdfium.PdfDocument(input_path)
    out = []
    try:
        for pa in analyses:
            img = _render_page_image(pdf_doc, pa.index, scale)
            draw = ImageDraw.Draw(img)
            for b in pa.boxes:
                draw.rectangle(
                    _image_rect(
                        b, scale, pad,
                        vertical_shift_mm=vertical_shift_mm,
                        top_extra_mm=top_extra_mm,
                        bottom_extra_mm=bottom_extra_mm,
                    ),
                    fill=box_color,
                )
            out.append((img, (pa.width, pa.height)))
    finally:
        pdf_doc.close()
    return out


def _write_image_pdf(
        pages: list[tuple[Image.Image, tuple[float, float]]],
        output_path: str) -> None:
    c = canvas.Canvas(output_path)
    for img, (w_pts, h_pts) in pages:
        c.setPageSize((w_pts, h_pts))
        c.drawImage(ImageReader(img), 0, 0, width=w_pts, height=h_pts)
        c.showPage()
    c.save()


def _image_page_pdf_bytes(img: Image.Image, width_pts: float, height_pts: float) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pts, height_pts))
    c.drawImage(ImageReader(img), 0, 0, width=width_pts, height=height_pts)
    c.showPage()
    c.save()
    return buf.getvalue()


def _fitz_rect(box: Box, pad: float):
    import fitz
    return fitz.Rect(box.x0 - pad, box.top - pad, box.x1 + pad, box.bottom + pad)


def _apply_surgical_redactions(page, boxes: list[Box], pad: float) -> None:
    for b in boxes:
        page.add_redact_annot(_fitz_rect(b, pad), fill=(0, 0, 0))
    page.apply_redactions()


def _redact_flatten(
        input_path: str, output_path: str, analyses: list[PageAnalysis],
        dpi: int, box_color, pad: int,
        vertical_shift_mm: float, top_extra_mm: float,
        bottom_extra_mm: float) -> list[int]:
    pages = _flatten_pages(
        input_path, analyses, dpi, box_color, pad,
        vertical_shift_mm, top_extra_mm, bottom_extra_mm,
    )
    _write_image_pdf(pages, output_path)
    return [pa.index for pa in analyses]


def _redact_surgical(
        input_path: str, output_path: str, analyses: list[PageAnalysis],
        dpi: int, box_color, pad: int,
        vertical_shift_mm: float, top_extra_mm: float,
        bottom_extra_mm: float) -> list[int]:
    import fitz

    flatten_idx = {pa.index for pa in analyses if not pa.has_words}
    if flatten_idx == {pa.index for pa in analyses}:
        return _redact_flatten(
            input_path, output_path, analyses, dpi, box_color, pad,
            vertical_shift_mm, top_extra_mm, bottom_extra_mm,
        )

    if not flatten_idx:
        doc = fitz.open(input_path)
        try:
            for pa in analyses:
                _apply_surgical_redactions(doc[pa.index], pa.boxes, pad)
            doc.save(output_path, garbage=4, deflate=True)
        finally:
            doc.close()
        return []

    flat_pages = {
        pa.index: page
        for pa, page in zip(
            analyses,
            _flatten_pages(
                input_path, analyses, dpi, box_color, pad,
                vertical_shift_mm, top_extra_mm, bottom_extra_mm,
            ),
        )
        if pa.index in flatten_idx
    }

    src = fitz.open(input_path)
    out = fitz.open()
    try:
        for pa in analyses:
            if pa.index in flatten_idx:
                img, (w_pts, h_pts) = flat_pages[pa.index]
                img_doc = fitz.open(stream=_image_page_pdf_bytes(img, w_pts, h_pts), filetype="pdf")
                out.insert_pdf(img_doc)
                img_doc.close()
                continue
            tmp = fitz.open()
            tmp.insert_pdf(src, from_page=pa.index, to_page=pa.index)
            _apply_surgical_redactions(tmp[0], pa.boxes, pad)
            out.insert_pdf(tmp)
            tmp.close()
        out.save(output_path, garbage=4, deflate=True)
    finally:
        src.close()
        out.close()
    return sorted(flatten_idx)


def redact_pdf(input_path: str, output_path: str, config: RuleConfig,
               dpi: int = 200, box_color=(0, 0, 0), pad: int = DEFAULT_PAD,
               vertical_shift_mm: float = DEFAULT_VERTICAL_SHIFT_MM,
               top_extra_mm: float = DEFAULT_TOP_EXTRA_MM,
               bottom_extra_mm: float = DEFAULT_BOTTOM_EXTRA_MM,
               mode: str = "surgical") -> dict:
    """Produce a redacted copy of input_path at output_path. Returns an audit report."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")

    _, rule_warnings = resolve_detectors(config)
    analyses = analyze_pdf(input_path, config)

    if mode == "flatten":
        flattened = _redact_flatten(
            input_path, output_path, analyses, dpi, box_color, pad,
            vertical_shift_mm, top_extra_mm, bottom_extra_mm,
        )
    else:
        flattened = _redact_surgical(
            input_path, output_path, analyses, dpi, box_color, pad,
            vertical_shift_mm, top_extra_mm, bottom_extra_mm,
        )

    return _build_report(
        analyses, input_path, output_path,
        mode=mode,
        flattened_pages=flattened,
        warnings=rule_warnings,
    )


def _matches_have_boxes(analyses: list[PageAnalysis]) -> bool:
    boxed = {id(b.match) for pa in analyses for b in pa.boxes}
    return all(id(m) in boxed for pa in analyses for m in pa.matches)


def _extract_residual_text(path: str) -> str:
    residual = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            residual += page.extract_text() or ""
    return residual


def _verify(output_path: str, analyses: list[PageAnalysis],
            mode: str, flattened_pages: list[int]) -> dict:
    """Re-scan the output. Does not treat a missing text layer as visual proof."""
    all_matches = [m for pa in analyses for m in pa.matches]
    matches_have_boxes = _matches_have_boxes(analyses)
    scan_error = None
    residual = ""
    try:
        residual = _extract_residual_text(output_path)
    except Exception:
        scan_error = "Could not re-scan output PDF"
        return {
            "residual_extractable_chars": 0,
            "leaked_values": [],
            "matches_have_boxes": matches_have_boxes,
            "text_layer_clean": False,
            "text_layer_removed": False,
            "mode": mode,
            "flattened_pages": flattened_pages,
            "banner": "fail",
            "passed": False,
            "error": scan_error,
        }

    leaked = sorted({m.value for m in all_matches if m.value and m.value in residual})
    text_layer_clean = len(leaked) == 0
    residual_chars = len(residual.strip())
    fully_flattened = bool(analyses) and set(flattened_pages) == {pa.index for pa in analyses}
    text_layer_removed = fully_flattened and residual_chars == 0

    if not matches_have_boxes or scan_error:
        passed = False
        banner = "fail"
    elif fully_flattened:
        passed = text_layer_removed
        banner = "flatten_ok" if passed else "fail"
    else:
        passed = text_layer_clean
        banner = "surgical_ok" if passed else "fail"

    return {
        "residual_extractable_chars": residual_chars,
        "leaked_values": leaked,
        "matches_have_boxes": matches_have_boxes,
        "text_layer_clean": text_layer_clean,
        "text_layer_removed": text_layer_removed,
        "mode": mode,
        "flattened_pages": flattened_pages,
        "banner": banner,
        "passed": passed,
        "error": scan_error,
    }


def _build_report(analyses: list[PageAnalysis], input_path: str,
                  output_path: str, mode: str,
                  flattened_pages: list[int],
                  warnings: list[str] | None = None) -> dict:
    all_matches: list[Match] = []
    items = []
    for pa in analyses:
        for m in pa.matches:
            all_matches.append(m)
            items.append({
                "page": pa.index + 1, "type": m.type, "label": m.label,
                "severity": m.severity, "validated": m.validated,
                "masked_value": m.masked,
            })

    verification = _verify(output_path, analyses, mode, flattened_pages)
    return {
        "source_file": os.path.basename(input_path),
        "output_file": os.path.basename(output_path),
        "pages": len(analyses),
        "mode": mode,
        "flattened_pages": [i + 1 for i in flattened_pages],
        "total_redactions": len(all_matches),
        "summary": summarize(all_matches),
        "items": items,
        "warnings": warnings or [],
        "verification": verification,
    }


# --------------------------------------------------------------------------- #
#  Preview helpers (for the web UI: before-with-highlights / after)
# --------------------------------------------------------------------------- #
def render_highlight_images(input_path: str, config: RuleConfig,
                            dpi: int = 130, pad: int = DEFAULT_PAD,
                            vertical_shift_mm: float = DEFAULT_VERTICAL_SHIFT_MM,
                            top_extra_mm: float = DEFAULT_TOP_EXTRA_MM,
                            bottom_extra_mm: float = DEFAULT_BOTTOM_EXTRA_MM
                            ) -> list[Image.Image]:
    """Original pages with translucent red boxes over detected PII."""
    scale = dpi / 72.0
    analyses = analyze_pdf(input_path, config)
    doc = pdfium.PdfDocument(input_path)
    out = []
    for pa in analyses:
        base = _render_page_image(doc, pa.index, scale).convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        for b in pa.boxes:
            d.rectangle(
                        _image_rect(
                            b, scale, pad,
                            vertical_shift_mm=vertical_shift_mm,
                            top_extra_mm=top_extra_mm,
                            bottom_extra_mm=bottom_extra_mm,
                        ),
                        fill=(220, 38, 38, 90), outline=(220, 38, 38, 255), width=2)
        out.append(Image.alpha_composite(base, overlay).convert("RGB"))
    doc.close()
    return out


def render_pdf_images(path: str, dpi: int = 130) -> list[Image.Image]:
    """Plain raster of every page of a PDF (used to show the redacted result)."""
    scale = dpi / 72.0
    doc = pdfium.PdfDocument(path)
    out = [_render_page_image(doc, i, scale) for i in range(len(doc))]
    doc.close()
    return out
