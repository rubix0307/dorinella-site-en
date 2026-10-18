"""The Open Graph images of the site (1200x630): the default one and those of the Gut Massage Guide and the course, written to app/static/img.

Run (Pillow is a dev-only dependency):
    pip install -r requirements-dev.txt
    python scripts/build_og_images.py <folder with the fonts>

Fonts: the site's own, from Google Fonts (OFL), not committed. The folder holds
    CormorantGaramond.ttf, CormorantGaramond-Italic.ttf (the logo), Manrope.ttf
(https://github.com/google/fonts/tree/main/ofl/cormorantgaramond and /manrope).
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.site import BRAND  # noqa: E402

IMG = ROOT / 'app' / 'static' / 'img'
W, H = 1200, 630
BG = (243, 239, 233)           # --bg
INK = (31, 42, 31)             # --ink
SOFT = (76, 87, 76)            # --ink-soft
ACCENT = (47, 74, 44)          # --green-700
SAGE = (223, 231, 218)         # --sage-100
PAD = 72

IMAGES = {
    'og-default.jpg': ('GUT HEALTH, MADE GENTLE', 'Gentle gut health, step by step', 'Abdominal self-massage, a Bristol stool scale test and practical guides.'),
    'og-gut-guide.jpg': ('GUIDE', 'Gut Massage Guide', 'Step-by-step abdominal self-massage and clear safety guidance to look after your gut.'),
    'og-course.jpg': ('ONLINE COURSE', 'Abdominal Massage Course', 'Abdominal self-massage step by step, with animated demonstrations and safe preparation.'),
}


def font(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size)
    f.set_variation_by_axes([weight])
    return f


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ''
    for word in text.split():
        trial = f'{line} {word}'.strip()
        if draw.textlength(trial, font=f) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line]


def render(eyebrow: str, title: str, lead: str, fonts: Path) -> Image.Image:
    serif, sans = fonts / 'CormorantGaramond.ttf', fonts / 'Manrope.ttf'
    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)

    # right: the gut illustration on a sage disc
    r = 250
    cx, cy = W - PAD - r + 30, H // 2
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=SAGE)
    art = Image.open(IMG / 'guts-hero-900.webp').convert('RGBA')
    art.thumbnail((int(r * 1.5), int(r * 1.5)), Image.Resampling.LANCZOS)
    img.paste(art, (cx - art.width // 2, cy - art.height // 2), art)

    left_w = cx - r - PAD - 40
    # the footer (rule + name) is fixed at the bottom; the text block must end above it, so pick the largest title that fits
    rule_y, name_y = H - 118, H - 100
    top, bottom = 70, rule_y - 36
    lead_font = font(sans, 26, 500)
    lead_lines = wrap(draw, lead, lead_font, left_w)[:3]
    for size in (80, 72, 64, 56, 50):
        title_font = font(serif, size, 600)
        lines = wrap(draw, title, title_font, left_w)
        step = round(size * 1.08)
        block = 22 + 30 + len(lines) * step + 22 + len(lead_lines) * 40
        if len(lines) <= 3 and block <= bottom - top:
            break
    y = top + (bottom - top - block) // 2
    draw.text((PAD, y), eyebrow, font=font(sans, 22, 700), fill=ACCENT)
    y += 22 + 30
    for line in lines:
        draw.text((PAD, y), line, font=title_font, fill=INK)
        y += step
    y += 22
    for line in lead_lines:
        draw.text((PAD, y), line, font=lead_font, fill=SOFT)
        y += 40

    draw.line((PAD, rule_y, PAD + 64, rule_y), fill=ACCENT, width=3)
    draw.text((PAD, name_y), BRAND, font=font(fonts / 'CormorantGaramond-Italic.ttf', 46, 500), fill=INK)
    return img


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('fonts', type=Path)
    args = parser.parse_args()
    for name, (eyebrow, title, lead) in IMAGES.items():
        render(eyebrow, title, lead, args.fonts).save(IMG / name, 'JPEG', quality=86, optimize=True, progressive=True)
        print('written', name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
