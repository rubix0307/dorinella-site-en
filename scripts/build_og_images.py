"""Open Graph images for recipes (1200×630): the title and a few facts on the left, the video's
poster on the right. Written next to the videos as media/recipes/<reel>-og.jpg (with them to the bucket).

Run (in the web container, which has the media folder; Pillow is a dev-only dependency):
    pip install -r requirements-dev.txt
    python scripts/build_og_images.py .og-build/fonts            # only missing / outdated images
    python scripts/build_og_images.py .og-build/fonts --force    # all of them

Fonts — the site's own, from Google Fonts (OFL), not committed:
    .og-build/fonts/CormorantGaramond.ttf  https://github.com/google/fonts/raw/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf
    .og-build/fonts/CormorantGaramond-Italic.ttf  …/cormorantgaramond/CormorantGaramond-Italic%5Bwght%5D.ttf (the logo)
    .og-build/fonts/Manrope.ttf            https://github.com/google/fonts/raw/main/ofl/manrope/Manrope%5Bwght%5D.ttf
"""
import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, recipes  # noqa: E402
from app.core.templating import minutes  # noqa: E402
from app.recipes import store  # noqa: E402

W, H = 1200, 630
BG = (243, 239, 233)           # --bg
INK = (31, 42, 31)             # --ink
SOFT = (76, 87, 76)            # --ink-soft
ACCENT = (47, 74, 44)          # --green-700
LINE = (222, 215, 204)
PAD = 72
VERSION = 'og-2'               # bump to rebuild every image after a design change


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


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius, fill=255)
    out = Image.new('RGBA', img.size)
    out.paste(img, (0, 0), mask)
    return out


def render(recipe: recipes.Recipe, poster: Path, fonts: Path) -> Image.Image:
    serif = fonts / 'CormorantGaramond.ttf'
    sans = fonts / 'Manrope.ttf'
    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)

    # right: the poster, 9:16, rounded, with a soft shadow and a play button
    ph = H - 2 * 48
    pw = round(ph * 9 / 16)
    px, py = W - PAD - pw, 48
    shot = Image.open(poster).convert('RGB')
    scale = max(pw / shot.width, ph / shot.height)
    shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.Resampling.LANCZOS)
    shot = shot.crop(((shot.width - pw) // 2, (shot.height - ph) // 2, (shot.width - pw) // 2 + pw, (shot.height - ph) // 2 + ph))
    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((px, py + 14, px + pw, py + ph + 14), 32, fill=(31, 42, 31, 70))
    img.paste(shadow.filter(ImageFilter.GaussianBlur(22)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(22)))
    card = rounded(shot, 32)
    img.paste(card, (px, py), card)
    cx, cy, r = px + pw // 2, py + ph // 2, 42
    disc = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 235))
    img.paste(disc, (0, 0), disc)
    draw.polygon([(cx - 12, cy - 18), (cx - 12, cy + 18), (cx + 20, cy)], fill=ACCENT)

    # left: eyebrow, title (fitted), facts, signature
    left_w = px - PAD - 56
    eyebrow = font(sans, 22, 700)
    draw.text((PAD, 78), f'РЕЦЕПТ  ·  {recipe.category_label.upper()}', font=eyebrow, fill=ACCENT)

    for size in (84, 76, 68, 60, 54):
        title_font = font(serif, size, 600)
        lines = wrap(draw, recipe.title, title_font, left_w)
        if len(lines) <= 3:
            break
    y = 128
    for line in lines:
        draw.text((PAD, y), line, font=title_font, fill=INK)
        y += round(size * 1.08)

    facts = font(sans, 26, 600)
    y += 30
    parts = [minutes(recipe.total_minutes), recipes.DIFFICULTY[recipe.difficulty]]
    if recipe.diet:
        parts.append(recipes.DIETS[recipe.diet[0]][0])
    draw.text((PAD, y), '   ·   '.join(parts), font=facts, fill=SOFT)

    draw.line((PAD, H - 118, PAD + 64, H - 118), fill=ACCENT, width=3)
    draw.text((PAD, H - 100), 'dorinellla', font=font(fonts / 'CormorantGaramond-Italic.ttf', 46, 500), fill=INK)
    return img


async def _all_recipes() -> list[recipes.Recipe]:
    """Every recipe in the database, drafts too (their image is ready when they're published)."""
    async with db.new_session() as session:
        return [recipes.parse(store.to_raw(r)) for r in await store.all_records(session)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('fonts', type=Path)
    parser.add_argument('--media', type=Path, default=ROOT / 'media' / 'recipes')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    made = 0
    for recipe in asyncio.run(_all_recipes()):
        poster = args.media / f'{recipe.reel}.webp'
        out = args.media / f'{recipe.reel}-og.jpg'
        stamp = args.media / f'{recipe.reel}-og.txt'   # what it was drawn from: redraw when that changes
        key = hashlib.sha256(f'{VERSION}|{recipe.title}|{recipe.category}|{recipe.total_minutes}|'
                             f'{recipe.difficulty}|{recipe.diet}|{poster.stat().st_size}'.encode()).hexdigest()
        if not args.force and out.exists() and stamp.exists() and stamp.read_text() == key:
            continue
        render(recipe, poster, args.fonts).save(out, 'JPEG', quality=86, optimize=True, progressive=True)
        stamp.write_text(key)
        made += 1
    print(f'og images: {made} drawn')
    return 0


if __name__ == '__main__':
    sys.exit(main())
