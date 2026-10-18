"""Admin «Тести»: what people answer in the self-tests and guide checklists."""
from urllib.parse import unquote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, stats, test_stats
from ..auth import authorize
from ..learn import SELF_TESTS
from .admin import TZ_COOKIE, _page, _to_login, stats_presets

router = APIRouter(prefix='/admin/tests', default_response_class=HTMLResponse, include_in_schema=False)

PERM = 'tests.view'


async def _period(request: Request, session: AsyncSession):
    asked = unquote(request.cookies.get(TZ_COOKIE, ''))
    zone = stats.use_timezone(asked, await stats.known_zones(session))
    rng = stats.parse_range(request.query_params.get('from'), request.query_params.get('to'))
    return rng, {'rng': rng, 'page_state': [], 'presets': stats_presets(), 'hide_new_link': True,
                     'preset_query': {n: urlencode([('from', r.start), ('to', r.end)]) for n, r in stats_presets().items()},
                     'period_query': urlencode([('from', rng.start), ('to', rng.end)]),
                     'tz_name': zone.key, 'tz_asked': asked, 'tz_label': stats.tz_label()}


@router.get('/', name='admin_tests')
async def tests_overview(request: Request, session: AsyncSession = Depends(db.get_session)):  # noqa: PT028 — a route, not a test
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    rng, period = await _period(request, session)
    return _page(request, 'admin/tests.html', user=user, tests=await test_stats.overview(session, rng), **period)


def _test(slug: str):
    test = SELF_TESTS.get(slug)
    if test is None:
        raise HTTPException(status_code=404)
    return test


@router.get('/{slug}/', name='admin_test')
async def test_detail(request: Request, slug: str, session: AsyncSession = Depends(db.get_session)):  # noqa: PT028 — a route, not a test
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    test = _test(slug)
    rng, period = await _period(request, session)
    return _page(request, 'admin/test_detail.html', user=user, test=test, **await test_stats.detail(session, rng, test),
                 **period)
