"""Build the site's fonts: one woff2 per face with Cyrillic *and* Latin (digits, punctuation, the logo), so
each face is one request that can be preloaded — Google's split subsets made the first screen fetch two
files per face, one after the other.

    pip install fonttools brotli
    python scripts/build_fonts.py <dir with the Google Fonts TTFs>

The TTFs (OFL) are in github.com/google/fonts: ofl/manrope/Manrope[wght].ttf,
ofl/cormorantgaramond/CormorantGaramond[wght].ttf and CormorantGaramond-Italic[wght].ttf.
Prints the @font-face rules for the top of static/css/styles.css.
"""
import hashlib
import io
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'fonts'
# Basic Latin + Latin-1, Ukrainian/Russian Cyrillic, typographic punctuation, №, €, arrows, minus
UNICODES = ('U+0020-007E,U+00A0-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DC,U+0301,U+0400-045F,U+0490-0491,'
            'U+2010-2027,U+2030-203A,U+20AC,U+2116,U+2122,U+2190-2193,U+2212')
FACES = [   # (source, family, style, weights the site uses, file stem)
    ('Manrope[wght].ttf', 'Manrope', 'normal', (400, 700), 'manrope'),
    ('CormorantGaramond[wght].ttf', 'Cormorant Garamond', 'normal', (500, 600), 'cormorant-garamond'),
    ('CormorantGaramond-Italic[wght].ttf', 'Cormorant Garamond', 'italic', (500, 600), 'cormorant-garamond-italic'),
]


def build(source: Path, weights: tuple[int, int]) -> bytes:
    font = TTFont(source)
    options = subset.Options()
    options.flavor = 'woff2'
    options.layout_features = ['kern', 'liga', 'calt', 'ccmp', 'locl', 'mark', 'mkmk', 'case', 'lnum', 'pnum']
    options.hinting = False                       # screens today render without it; saves a lot
    options.desubroutinize = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(UNICODES))
    subsetter.subset(font)                                                       # the characters first,
    font = instancer.instantiateVariableFont(font, {'wght': weights})            # then only the weights in use
    out = io.BytesIO()
    font.flavor = 'woff2'
    font.save(out)
    return out.getvalue()


def main(src: Path) -> None:
    for old in OUT.glob('*.woff2'):
        old.unlink()
    for file, family, style, (low, high), stem in FACES:
        data = build(src / file, (low, high))
        name = f'{stem}-{hashlib.sha256(data).hexdigest()[:10]}.woff2'
        (OUT / name).write_bytes(data)
        print(f'@font-face {{ font-family: "{family}"; font-style: {style}; font-weight: {low} {high}; '
              f'font-display: swap; src: url("/static/fonts/{name}") format("woff2"); }}   /* {len(data) // 1024} KB */')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
