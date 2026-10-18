"""Search engines: the sitemap (sitemap.xml) and the schema.org markup of the public pages.

The sitemap lists only what the site shows to everybody: the main pages, the products, the guides and the tests. Never the
admin, the cabinet, the sign-in, the closed course or the tracked redirects (robots.txt says the same).

A page written in code has no edit date in the database, so it gets the date of the last git commit of the files it is made of
(its template and its data); without a git checkout (a deployed image) it reads app/lastmod.json (baked by scripts/bake_lastmod.py
at deploy); without that too the sitemap leaves `lastmod` out: a date is never invented.
"""
import asyncio
import json
import logging
import subprocess
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from markupsafe import Markup
from sqlalchemy.ext.asyncio import AsyncSession

from . import legal
from .catalog import Product, ProductType, listed
from .core.config import settings
from .learn import GUIDES, SELF_TESTS
from .site import AUTHOR_JOB, AUTHOR_NAME, BRAND, CURRENCY, LOCALE

log = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent
SITE_NAME = BRAND
AUTHOR = {'@type': 'Person', '@id': '#author', 'name': AUTHOR_NAME, 'jobTitle': AUTHOR_JOB}


LASTMOD_FILE = APP_DIR / 'lastmod.json'


@lru_cache(maxsize=1)
def _baked_days() -> dict[str, date]:
    """app/lastmod.json (scripts/bake_lastmod.py, run by deploy.sh before the image build): path under app/ with forward slashes -> day of its last commit. Missing or broken: empty."""
    try:
        raw = json.loads(LASTMOD_FILE.read_text(encoding='utf-8'))
        return {str(name).replace('\\', '/'): date.fromisoformat(day) for name, day in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _baked(files: tuple[str, ...]) -> date | None:
    days = _baked_days()
    found = [days[key] for key in (name.replace('\\', '/') for name in files) if key in days]
    return max(found) if found else None


@lru_cache(maxsize=256)
def _modified(*files: str) -> date | None:
    """The day of the last commit that touched any of the files; None when there is no git checkout, git fails or the files are not committed."""
    existing = [str(APP_DIR / name) for name in files if (APP_DIR / name).is_file()]
    if not existing:
        return None
    if not (APP_DIR.parent / '.git').exists():
        return _baked(files)
    try:
        out = subprocess.run(['git', '-C', str(APP_DIR.parent), 'log', '-1', '--format=%cs', '--', *existing], capture_output=True, text=True, timeout=10, check=True).stdout.strip()  # noqa: S603, S607 — fixed argv, git from PATH, no shell
        return date.fromisoformat(out) if out else None
    except Exception:  # noqa: BLE001 — a missing git is not a reason to fail the sitemap
        return _baked(files)


def _page(template: str, *data: str) -> date | None:
    return _modified(f'templates/{template}', *data)


def _head() -> list[tuple[str, date | None]]:
    """The pages written in code (git is asked here: call it in a thread)."""
    learn = ('learn.py',)
    out: list[tuple[str, date | None]] = [
        ('/', _page('pages/index.html', 'content.py')),
        ('/products/', _page('pages/products/index.html', 'catalog.py')),
    ]
    out += [(f'/products/{p.slug}/', _page(p.template, 'catalog.py', 'content.py')) for p in listed().values()]
    out.append(('/guides/', _page('pages/learn/index.html', *learn)))
    out += [(f'/guides/{slug}/', _page('pages/learn/guide.html', *learn)) for slug in GUIDES]
    out += [(f'/tests/{slug}/', _page('pages/tests/test.html', *learn)) for slug in SELF_TESTS]
    out.append(('/links/', _page('pages/links.html')))
    if not legal.SELLER.placeholders:      # the legal pages are noindex while they still print brackets (pages/privacy.html), so they stay out of the sitemap too
        out += [(f'/{name}/', _page(f'pages/{name}.html')) for name in ('privacy', 'terms', 'refund')]
    return out


async def entries(session: AsyncSession | None) -> list[tuple[str, date | None]]:
    """(path, lastmod) of every public page, in the order the site shows them (lastmod None: not known)."""
    return await asyncio.to_thread(_head)


def sitemap_xml(base: str, pages: list[tuple[str, date | None]]) -> str:
    urls = ''.join(f'<url><loc>{escape(base + path)}</loc>' + (f'<lastmod>{stamp.isoformat()}</lastmod>' if stamp else '') + '</url>' for path, stamp in pages)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


SITEMAP_TTL = 60
SITEMAP_MAX = 8                                     # without SITE_URL the base comes from the Host header: made-up hosts must not grow the memory
_SITEMAP: dict[str, tuple[float, str]] = {}         # base address -> (valid until, the body): a crawler asking often does not make the database work
_LOCKS: dict[str, asyncio.Lock] = {}                # base address -> the lock of the build in progress (no stampede on an empty cache); dropped when idle
_generation = 0                                     # grows with every invalidate(): a build that began before it must not store its (stale) body


def invalidate() -> None:
    """The sitemap is made again at the next request (a product, guide or other catalog page changed)."""
    global _generation
    _generation += 1
    _SITEMAP.clear()


def _keep(base: str, body: str) -> None:
    """Store the body; at most SITEMAP_MAX bases: the expired go first, then the oldest."""
    now = time.monotonic()
    for key in [key for key, (until, _) in _SITEMAP.items() if until <= now]:
        del _SITEMAP[key]
    _SITEMAP.pop(base, None)
    while len(_SITEMAP) >= SITEMAP_MAX:
        del _SITEMAP[min(_SITEMAP, key=lambda key: _SITEMAP[key][0])]
    _SITEMAP[base] = (now + SITEMAP_TTL, body)


def _fresh(base: str) -> str | None:
    cached = _SITEMAP.get(base)
    return cached[1] if cached and cached[0] > time.monotonic() else None


async def sitemap_body(base: str, session: AsyncSession | None) -> tuple[str, bool]:
    """(the xml, complete). When the database fails the answer holds the pages of the code only (complete False) and is not kept."""
    if (body := _fresh(base)) is not None:
        return body, True
    lock = _LOCKS.setdefault(base, asyncio.Lock())
    try:
        async with lock:
            if (body := _fresh(base)) is not None:           # another request built it while this one waited
                return body, True
            started = _generation
            try:
                body = sitemap_xml(base, await entries(session))
            except Exception:
                log.warning('sitemap: the database failed, answering with the pages of the code only', exc_info=True)
                return sitemap_xml(base, await entries(None)), False
            if started == _generation:                       # invalidate() ran during the build: the body may be stale, answer with it but do not keep it
                _keep(base, body)
            return body, True
    finally:
        if _LOCKS.get(base) is lock and not lock.locked():
            _LOCKS.pop(base, None)


# ---- schema.org

def script(*nodes: dict | None) -> Markup:
    """The nodes as one JSON-LD script (a graph). `<` is escaped, so a text can never close the script."""
    graph = [node for node in nodes if node]
    if not graph:
        return Markup('')
    data = json.dumps({'@context': 'https://schema.org', '@graph': graph}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return Markup('<script type="application/ld+json">') + Markup(data) + Markup('</script>')  # noqa: S704 — JSON-LD with < > & escaped above


def _base(request) -> str:
    return settings.site_url or f'{request.url.scheme}://{request.url.netloc}'


def site(request) -> list[dict]:
    """The site and its author: on every page, so a search engine knows whose pages these are."""
    base = _base(request)
    return [
        {'@type': 'Organization', '@id': f'{base}/#org', 'name': SITE_NAME, 'url': f'{base}/', 'logo': f'{base}/static/img/favicon.svg'},
        {'@type': 'WebSite', '@id': f'{base}/#site', 'url': f'{base}/', 'name': SITE_NAME, 'inLanguage': LOCALE, 'publisher': {'@id': f'{base}/#org'}},
        {**AUTHOR, '@id': f'{base}/#author', 'url': f'{base}/'},
    ]


def crumbs(request, *trail: tuple[str, str]) -> dict:
    """BreadcrumbList: (name, path) from the main page down to this one."""
    base = _base(request)
    items = [('Home', '/'), *trail]
    return {'@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': n, 'name': name, 'item': base + path} for n, (name, path) in enumerate(items, 1)]}


def page(request, name: str, description: str, kind: str = 'WebPage', **extra) -> dict:
    base = _base(request)
    url = base + request.url.path
    return {'@type': kind, '@id': f'{url}#page', 'url': url, 'name': name, 'description': description, 'inLanguage': LOCALE,
            'isPartOf': {'@id': f'{base}/#site'}, 'author': {'@id': f'{base}/#author'}, **extra}


def faq(pairs) -> dict | None:
    """Questions and answers: (question, answer) pairs, or dicts with `q` and `a` (an article keeps them so)."""
    pairs = [(p['q'], p['a']) if isinstance(p, dict) else p for p in pairs or []]
    if not pairs:
        return None
    return {'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in pairs]}


def item_list(request, items: list[tuple[str, str]]) -> dict:
    base = _base(request)
    return {'@type': 'ItemList', 'itemListElement': [{'@type': 'ListItem', 'position': n, 'name': name, 'url': base + path} for n, (name, path) in enumerate(items, 1)]}


def product(request, product: Product) -> dict:
    """A guide is a Product, a course is a Course; plans become offers (the price in dollars) while the product is on sale. Reviews are not marked up: a seller may not rate itself."""
    base = _base(request)
    url = f'{base}/products/{product.slug}/'
    offers = [{'@type': 'Offer', 'name': f'{plan.label}: {plan.title}', 'price': plan.price, 'priceCurrency': CURRENCY, 'url': url,
               'availability': 'https://schema.org/InStock'} for plan in product.plans if product.on_sale]     # a price is published only once the product is on sale
    node: dict[str, Any] = {'@type': 'Course' if product.type == ProductType.COURSE else 'Product', '@id': f'{url}#product', 'url': url, 'name': product.title,
            'description': product.summary, 'image': f'{base}/static/{product.og_image}', 'inLanguage': LOCALE, 'offers': offers or None}
    if product.type == ProductType.COURSE:
        node['provider'] = {'@id': f'{base}/#org'}
    else:
        node['brand'] = {'@id': f'{base}/#org'}
    return {k: v for k, v in node.items() if v}


def guide(request, guide_) -> dict:
    return page(request, guide_.title, guide_.subtitle, 'Article', headline=guide_.title)


def graph(request, *nodes: dict | None) -> Markup:
    """The site and its author plus this page's own nodes, as one script."""
    return script(*site(request), *nodes)


def generic(request, name: str, description: str, kind: str = 'WebPage') -> Markup:
    """A plain page: itself and its breadcrumbs (name = the page's title without the site's name)."""
    short = name.split(' | ', maxsplit=1)[0]
    return graph(request, page(request, short, description, kind), crumbs(request, (short, request.url.path)))


def products() -> list[tuple[str, str]]:
    return [(p.title, f'/products/{p.slug}/') for p in listed().values()]
