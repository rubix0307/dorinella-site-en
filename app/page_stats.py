"""«Pages»: which parts of the site and which pages are read — sections first, then a section's pages
(like Google Analytics' content drill-down). From page_views, for the admin's period."""
from dataclasses import dataclass

from sqlalchemy import case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from .analytics import visitor_key
from .catalog import PRODUCTS
from .learn import GUIDES, SELF_TESTS
from .models import PageView
from .stats import DateRange

# key → (label, icon, path prefix); the order of the table when the numbers tie; '' = everything else
SECTIONS = {
    'home': ('Home', 'house', '/'),
    'products': ('Products', 'gift', '/products/'),
    'guides': ('Guides', 'book-open-text', '/guides/'),
    'tests': ('Tests', 'circle-help', '/tests/'),
    'course': ('Course', 'graduation-cap', '/course/'),
    'links': ('Links', 'link', '/links/'),
    'other': ('Other', 'ellipsis', ''),
}


def _section_of(path):
    """The section a page belongs to, as SQL (so visitors are counted once per section, not per page)."""
    return case(
        (path == '/', literal('home')),
        *((path.like(f'{prefix}%'), literal(key)) for key, (_, _, prefix) in SECTIONS.items() if len(prefix) > 1),
        else_=literal('other'),
    )


@dataclass(frozen=True)
class Row:
    key: str            # section key, or the page path
    label: str
    views: int
    visitors: int
    share: float        # of all views in the period, 0..1
    hint: str = ''      # under the label: the path of a page


async def sections(session: AsyncSession, rng: DateRange) -> tuple[list[Row], int, int]:
    """(rows, all views, all visitors) — every section, also the ones nobody opened."""
    since, until = rng.utc_bounds
    section = _section_of(PageView.path).label('section')
    period = (PageView.created_at >= since, PageView.created_at < until)
    rows = {s: (v, u) for s, v, u in (await session.execute(
        select(section, func.count(), func.count(visitor_key(PageView).distinct())).where(*period).group_by(section)
    )).all()}
    views, visitors = (await session.execute(
        select(func.count(), func.count(visitor_key(PageView).distinct())).where(*period))).one()
    out = [Row(key, label, *rows.get(key, (0, 0)), share=rows.get(key, (0, 0))[0] / views if views else 0)
           for key, (label, _, _) in SECTIONS.items()]
    out.sort(key=lambda r: -r.views)          # stable: equal numbers keep the SECTIONS order
    return out, views, visitors


async def pages(session: AsyncSession, rng: DateRange, section: str) -> tuple[list[Row], int, int]:
    """(this section's pages, the section's views, the section's visitors), most viewed first."""
    since, until = rng.utc_bounds
    where = (PageView.created_at >= since, PageView.created_at < until, _section_of(PageView.path) == section)
    found = (await session.execute(
        select(PageView.path, func.count().label('views'), func.count(visitor_key(PageView).distinct()))
        .where(*where).group_by(PageView.path).order_by(func.count().desc(), PageView.path).limit(500)
    )).all()
    views, visitors = (await session.execute(
        select(func.count(), func.count(visitor_key(PageView).distinct())).where(*where))).one()
    names = await _page_names(session)
    out = [Row(path, names.get(path) or path, v, u, share=v / views if views else 0, hint=path)
           for path, v, u in found]
    return out, views, visitors


async def _page_names(session: AsyncSession) -> dict[str, str]:
    """Human names for known paths: products, guides, tests."""
    names = {'/': 'Home page', '/products/': 'Product catalogue', '/guides/': 'Guide library'}
    for product in PRODUCTS.values():
        names[f'/products/{product.slug}/'] = product.title
    for guide in GUIDES.values():
        names[f'/guides/{guide.slug}/'] = f'Guide: {guide.title}'
    for test in SELF_TESTS.values():
        names[f'/tests/{test.slug}/'] = f'Test: {test.title}'
    return names
