#!/usr/bin/env python3
"""
create_slides.py - Create a .pptx from a JSON content file with Google Images (via SerpAPI).

Usage:
    python skills/pptx/scripts/create_slides.py content.json --out output.pptx

JSON format:
    {
        "title": "Presentation Title",
        "subtitle": "Optional subtitle",
        "color": "0A3D62",           # optional hex, default dark blue
        "slides": [
            {
                "title": "Slide Title",
                "bullets": ["Point 1", "Point 2", "Point 3"],
                "image_keyword": "鄭麗文習近平握手會面",
                "notes": "Optional speaker notes"
            }
        ]
    }

Requirements: pip install python-pptx
              SERP_API_KEY in .env
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
import tempfile
from pathlib import Path

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
except ImportError:
    print("Error: python-pptx not installed. Run: pip install python-pptx", file=sys.stderr)
    sys.exit(1)


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_env():
    for d in [Path.cwd()] + list(Path.cwd().parents)[:4]:
        env_file = d / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break


def _to_jpeg(img_bytes: bytes) -> bytes | None:
    """Convert image bytes to JPEG. Handles WEBP and other formats via Pillow."""
    # Detect WEBP: starts with RIFF....WEBP
    is_webp = img_bytes[:4] == b'RIFF' and img_bytes[8:12] == b'WEBP'
    # Detect non-JPEG/PNG by checking magic bytes
    is_jpeg = img_bytes[:2] == b'\xff\xd8'
    is_png  = img_bytes[:8] == b'\x89PNG\r\n\x1a\n'
    if is_jpeg or is_png:
        return img_bytes  # already supported, no conversion needed
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        out = io.BytesIO()
        img.convert('RGB').save(out, format='JPEG', quality=85)
        converted = out.getvalue()
        if is_webp:
            print(f"  [webp→jpeg converted]")
        return converted
    except Exception as e:
        print(f"  [convert error] {e}", file=sys.stderr)
        return None


def fetch_image(query: str) -> tuple[bytes, str]:
    """Return (image_bytes, credit_string) using SerpAPI Google Images."""
    api_key = os.environ.get("SERP_API_KEY", "")
    if not api_key:
        print(f"  [skip image] SERP_API_KEY not set", file=sys.stderr)
        return None, ""

    params = urllib.parse.urlencode({
        "engine": "google_images",
        "q": query,
        "api_key": api_key,
        "num": 5,
        "safe": "active",
    })
    req = urllib.request.Request(
        f"https://serpapi.com/search.json?{params}",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        results = data.get("images_results", [])
        if not results:
            print(f"  [no image] no results for '{query}'", file=sys.stderr)
            return None, ""
        # Try each result until one downloads and converts successfully
        for photo in results[:8]:
            img_url = photo.get("original") or photo.get("thumbnail", "")
            if not img_url:
                continue
            source = photo.get("source", "Google Images")
            credit = f"via Google / {source}"
            try:
                img_req = urllib.request.Request(
                    img_url, headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(img_req, timeout=15) as r:
                    raw = r.read()
                if len(raw) < 1000:
                    continue
                img_bytes = _to_jpeg(raw)
                if img_bytes:
                    print(f"  [image] '{query}' → {credit}")
                    return img_bytes, credit
            except Exception:
                continue
        print(f"  [no image] all downloads failed for '{query}'", file=sys.stderr)
        return None, ""
    except Exception as e:
        print(f"  [image error] {e}", file=sys.stderr)
        return None, ""


def hex_to_rgb(hex_str: str) -> RGBColor:
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ── Slide builders ─────────────────────────────────────────────────────────────

SLIDE_W = Inches(10)
SLIDE_H = Inches(7.5)
TEXT_W  = Inches(4.8)   # left column width


def add_title_slide(prs: Presentation, title: str, subtitle: str, accent: RGBColor, img_bytes, credit: str):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Background rect (full slide, dark)
    bg = slide.shapes.add_shape(1, 0, 0, SLIDE_W, SLIDE_H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = accent
    bg.line.fill.background()

    # Right-half image
    if img_bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(img_bytes)
            tmp = f.name
        pic = slide.shapes.add_picture(tmp, Inches(5), 0, Inches(5), SLIDE_H)
        slide.shapes._spTree.remove(pic._element)
        slide.shapes._spTree.insert(3, pic._element)
        os.unlink(tmp)
        # Semi-transparent dark overlay on right half
        ov = slide.shapes.add_shape(1, Inches(5), 0, Inches(5), SLIDE_H)
        ov.fill.solid()
        ov.fill.fore_color.rgb = RGBColor(0, 0, 0)
        ov.line.fill.background()
        from pptx.util import Pt as _Pt
        ov.fill.fore_color.theme_color  # access to force solid
        # Opacity via xml (python-pptx workaround)
        from lxml import etree
        spPr = ov._element.spPr
        solidFill = spPr.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill')
        if solidFill is not None:
            srgb = solidFill.find('{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr')
            if srgb is not None:
                alpha = etree.SubElement(srgb, '{http://schemas.openxmlformats.org/drawingml/2006/main}alpha')
                alpha.set('val', '55000')  # ~55% opacity

    # Title text
    tx = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(4.5), Inches(1.5))
    tf = tx.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.runs[0].font.size = Pt(36)
    p.runs[0].font.bold = True
    p.runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # Subtitle
    if subtitle:
        tx2 = slide.shapes.add_textbox(Inches(0.5), Inches(4.2), Inches(4.5), Inches(0.8))
        tf2 = tx2.text_frame
        p2 = tf2.paragraphs[0]
        p2.text = subtitle
        p2.runs[0].font.size = Pt(16)
        p2.runs[0].font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

    _add_credit(slide, credit, Inches(5.1))


def add_content_slide(prs: Presentation, title: str, bullets: list, accent: RGBColor, img_bytes, credit: str):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Title bar
    bar = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(1.1))
    bar.fill.solid()
    bar.fill.fore_color.rgb = accent
    bar.line.fill.background()

    # Title text
    tx = slide.shapes.add_textbox(Inches(0.3), Inches(0.1), Inches(9.4), Inches(0.9))
    tf = tx.text_frame
    p = tf.paragraphs[0]
    p.text = title
    p.runs[0].font.size = Pt(28)
    p.runs[0].font.bold = True
    p.runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # Right-half image
    if img_bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(img_bytes)
            tmp = f.name
        pic = slide.shapes.add_picture(tmp, Inches(5.1), Inches(1.15), Inches(4.7), Inches(6.2))
        slide.shapes._spTree.remove(pic._element)
        slide.shapes._spTree.insert(3, pic._element)
        os.unlink(tmp)
        _add_credit(slide, credit, Inches(5.15))

    # Bullet text (left half)
    content_top = Inches(1.3)
    content_h   = Inches(5.8)
    tx2 = slide.shapes.add_textbox(Inches(0.3), content_top, TEXT_W, content_h)
    tf2 = tx2.text_frame
    tf2.word_wrap = True
    for i, bullet in enumerate(bullets):
        p2 = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
        p2.text = f"• {bullet}"
        p2.runs[0].font.size = Pt(15)
        p2.runs[0].font.color.rgb = RGBColor(0x22, 0x22, 0x22)
        p2.space_after = Pt(8)


def _add_credit(slide, credit: str, left_offset):
    if not credit:
        return
    tx = slide.shapes.add_textbox(left_offset, Inches(7.25), Inches(4.8), Inches(0.22))
    tf = tx.text_frame
    p = tf.paragraphs[0]
    p.text = credit
    p.runs[0].font.size = Pt(7)
    p.runs[0].font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    load_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("content", help="Path to JSON content file")
    parser.add_argument("--out", default="output.pptx", help="Output .pptx path")
    args = parser.parse_args()

    data = json.loads(Path(args.content).read_text())
    accent = hex_to_rgb(data.get("color", "0A3D62"))

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    # Title slide
    title    = data.get("title", "Presentation")
    subtitle = data.get("subtitle", "")
    kw       = data.get("image_keyword", title)
    print(f"[title slide] '{title}'")
    img, credit = fetch_image(kw)
    add_title_slide(prs, title, subtitle, accent, img, credit)

    # Content slides — accept both "slides" and "content" as key
    slide_list = data.get("slides") or data.get("content") or []
    if not slide_list:
        print("ERROR: JSON has no 'slides' array. Add a 'slides' key with a list of slide objects.", file=sys.stderr)
        sys.exit(1)
    for i, slide_data in enumerate(slide_list, 1):
        stitle   = slide_data.get("title", f"Slide {i}")
        bullets  = slide_data.get("bullets", [])
        keyword  = slide_data.get("image_keyword", stitle)
        print(f"[slide {i}] '{stitle}' — searching '{keyword}'")
        img, credit = fetch_image(keyword)
        add_content_slide(prs, stitle, bullets, accent, img, credit)

    out_path = Path(args.out)
    prs.save(out_path)
    print(f"\nSaved: {out_path}  ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
