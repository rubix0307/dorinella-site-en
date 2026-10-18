"""A faint tiled mark on the background of the course pages: a short code that belongs to the signed-in person.

If a screenshot or a recording of the course ever leaks, the code on its background says whose account it came from. The code is
derived from the user id with the server secret (no table, nothing personal on the page); `resolve` finds the owner of a code.
It is only a deterrent and a trace for the lazy leak, a retyped text carries nothing.
"""
import hashlib
import hmac
from urllib.parse import quote

from .core.config import settings

ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'          # no 0/O, 1/I/L: a code read from a screenshot is not misread


def _number(user_id: int) -> int:
    digest = hmac.new(settings.secret_key.encode(), f'course-mark:{user_id}'.encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:5], 'big')


def code_for(user_id: int) -> str:
    return code_of(_number(user_id))


def code_of(number: int) -> str:
    """The text code of the 40-bit number (it is the number modulo 31**7: the bars carry the whole number, the letters its tail)."""
    letters = []
    for _ in range(7):
        number, rest = divmod(number, len(ALPHABET))
        letters.append(ALPHABET[rest])
    text = ''.join(letters)
    return f'{text[:3]}-{text[3:]}'


BARS = 40                                              # the same 40-bit number as the text, drawn as bars
TILE_W, TILE_H = 360, 210                              # one tile of the background
BAR_X, BAR_STEP, BAR_W, BAR_Y, BAR_H = 48, 6, 5, 168, 11   # the row of bars: where it starts, the pitch, a bar's width, its top and height
SYNC = '1011'                                          # marks where the row starts and which way it runs


def bits_for(user_id: int) -> str:
    return SYNC + format(_number(user_id), f'0{BARS}b')


def resolve(code: str, user_ids) -> int | None:
    """The id (out of `user_ids`) whose code this is; tolerant to case and a missing dash."""
    wanted = code.strip().upper().replace('-', '')
    for uid in user_ids:
        if code_for(uid).replace('-', '') == wanted:
            return uid
    return None


def css_background(user_id: int) -> str:
    """`url("data:image/svg+xml,…")` of one tile: the code twice as large tilted letters and once as a row of bars (a bar is a 1, a gap a 0),
    all in a barely visible grey-green. Big shapes and a plain row survive a JPEG or a rescale far better than small letters."""
    text = code_for(user_id)
    bars = ''.join(f'<rect x="{BAR_X + i * BAR_STEP}" y="{BAR_Y}" width="{BAR_W}" height="{BAR_H}"/>' for i, bit in enumerate(bits_for(user_id)) if bit == '1')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{TILE_W}" height="{TILE_H}" viewBox="0 0 {TILE_W} {TILE_H}">'
           '<g fill="#1f361e" fill-opacity=".01" font-family="Arial,Helvetica,sans-serif" font-size="32" font-weight="700" letter-spacing="3">'
           f'<text x="18" y="125" transform="rotate(-24 18 125)">{text}</text>'
           f'<text x="188" y="125" transform="rotate(-24 188 125)">{text}</text>'
           f'{bars}</g></svg>')
    return f'url("data:image/svg+xml,{quote(svg, safe="")}")'
