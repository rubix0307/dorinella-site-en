"""Jinja2 environment and template helpers (static(), url(), icon(), micon(), absolute_url())."""
import base64
import functools
import hashlib
import json
import mimetypes
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import i18n
from ..analytics import outbound_url
from .config import settings

mimetypes.add_type('image/webp', '.webp')  # missing from the registry on some systems (e.g. Windows)

APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / 'static'

templates = Jinja2Templates(directory=APP_DIR / 'templates')


@lru_cache(maxsize=256)
def _fingerprint(path: str, mtime_ns: int) -> str:
    return hashlib.md5((STATIC_DIR / path).read_bytes(), usedforsecurity=False).hexdigest()[:10]


def static(path: str) -> str:
    """URL of a static file with a content hash for cache busting."""
    file = STATIC_DIR / path
    if not file.is_file():
        return f'/static/{path}'
    return f'/static/{path}?v={_fingerprint(path, file.stat().st_mtime_ns)}'


def absolute_url(request: Request, path: str) -> str:
    if path.startswith(('https://', 'http://')):   # already absolute, e.g. media on a bucket
        return path
    base = settings.site_url or f'{request.url.scheme}://{request.url.netloc}'
    return base + path


def icon(name: str, css_class: str = '') -> Markup:
    """Lucide icon from the inline sprite in templates/includes/icons.html."""
    classes = f'icon {css_class}'.strip()
    return Markup(
        '<svg class="{}" width="24" height="24" aria-hidden="true" focusable="false">'
        '<use href="#i-{}"></use></svg>'
    ).format(escape(classes), escape(name))


MORPH_ICONS: dict[str, str] = json.loads((Path(__file__).with_name('morph_icons.json')).read_text(encoding='utf-8'))


def micon(name: str, css_class: str = '', open: str | None = None) -> Markup:
    """Icon that can morph into another in place (morphicons, see static/js/morph.js).

    Rendered as one real <path> with Lucide's data, so it looks identical before the script loads.
    `open`: the icon to morph into while the enclosing popover / <details> is open.
    """
    classes = f'icon morph-icon {css_class}'.strip()
    if open is not None:
        MORPH_ICONS[open]
    return Markup(
        '<svg class="{}" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" '
        'data-micon="{}"{}><path data-morph d="{}"/></svg>'
    ).format(escape(classes), escape(name),
             Markup(' data-micon-open="{}"').format(open) if open else '', MORPH_ICONS[name])


def minutes(total: int) -> str:
    """25 → «25 min», 90 → «1 h 30 min», 600 → «10 h»."""
    hours, mins = divmod(int(total), 60)
    return ' '.join(part for part in (f'{hours} h' if hours else '', f'{mins} min' if mins or not hours else '') if part)


templates.env.filters['minutes'] = minutes


def local_time(value: datetime, fmt: str = '%d.%m.%Y %H:%M') -> str:
    """A stored UTC time in the zone of the request (the admin's browser zone, UTC elsewhere)."""
    from .. import stats
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(stats.tz()).strftime(fmt)


templates.env.filters['local'] = local_time


MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')


def en_date(value: datetime | None) -> str:
    """«5 November 2026», in the zone of the site."""
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    from .. import stats
    value = value.astimezone(stats.tz())
    return f'{value.day} {MONTHS[value.month - 1]} {value.year}'


templates.env.filters['en_date'] = en_date


def setup(app: FastAPI) -> None:
    def url(name: str, **params) -> str:
        return str(app.url_path_for(name, **params))

    from .. import seo
    i18n.install(templates.env)
    templates.env.globals.update(
        seo=seo, static=static, url=url, icon=icon, micon=micon, absolute_url=absolute_url, now=datetime.now,
        out=outbound_url,  # {{ out('https://…', 'placement') }} — tracked external link
        media=lambda path: f'{settings.media_url}/{path}',  # a file of the media bucket (or /media/ locally)
    )


def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    context.setdefault('lang', i18n.pick(request))       # the language of the admin (the public pages are English only)
    return templates.TemplateResponse(request, template, context, status_code=status_code)


# Fonts the first screen needs at once (the text of the site): preloaded, so the text doesn't wait for the CSS
# to name them. File names carry a content hash (static/fonts, see the @font-face rules in styles.css).
FIRST_FONTS = ('manrope', 'cormorant-garamond')   # the italic loads when used


def font_preloads() -> Markup:
    files = sorted((STATIC_DIR / 'fonts').glob('*.woff2'))
    links = [f'<link rel="preload" href="/static/fonts/{f.name}" as="font" type="font/woff2" crossorigin>'
             for stem in FIRST_FONTS for f in files if re.fullmatch(rf'{stem}-[0-9a-f]{{10}}.woff2', f.name)]
    return Markup('\n    '.join(links))  # noqa: S704 — names of our own font files


# ES modules import each other by plain path (morph.js → ../vendor/morphicons/dom.js, ./morph-icons.js);
# the import map gives those imports the fingerprinted URLs, so they are cached for good and the module
# preloaded by <link rel=modulepreload> is the very one that is imported (not fetched twice).
MODULES = ('vendor/morphicons/dom.js', 'js/morph-icons.js')


def import_map() -> Markup:
    imports = {f'/static/{path}': static(path) for path in MODULES}
    return Markup('<script type="importmap">{}</script>').format(Markup(json.dumps({'imports': imports})))  # noqa: S704 — json of our own static paths


templates.env.globals.update(font_preloads=font_preloads, import_map=import_map)


# The site's stylesheet, in the page itself on a first visit: no request holds back the first paint.
# The page also loads the file (not blocking) so the browser caches it, and remembers that in a cookie;
# later pages then link the cached file instead of carrying the CSS again (templates/includes/styles.html).
STYLES = 'css/styles.css'
STYLES_COOKIE = 'css'
_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
_SPACE = re.compile(r'\s*([{};,>])\s*|\s+')    # not ':' — '.a :hover' needs its space


@functools.cache
def _inline_css(version: str) -> str:
    css = _COMMENT.sub('', (STATIC_DIR / STYLES).read_text(encoding='utf-8'))
    return _SPACE.sub(lambda m: m.group(1) or ' ', css).replace(';}', '}').strip()


def styles_version() -> str:
    return static(STYLES).rsplit('v=', 1)[-1]


def inline_styles() -> Markup:
    return Markup(_inline_css(styles_version()).replace('</', '<\\/'))  # noqa: S704 — our own stylesheet


templates.env.globals.update(inline_styles=inline_styles, styles_version=styles_version, STYLES_COOKIE=STYLES_COOKIE)


@functools.cache
def _data_uri(path: str, version: str) -> str:
    data = (STATIC_DIR / path).read_bytes()
    kind = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    return f'data:{kind};base64,{base64.b64encode(data).decode()}'


def inline_image(path: str) -> str:
    """A tiny image (a blurred preview) as a data: URI — in the page itself, shown before anything loads."""
    return _data_uri(path, static(path))


templates.env.globals.update(inline_image=inline_image)
