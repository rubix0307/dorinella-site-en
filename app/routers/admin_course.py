"""Admin «Course»: analytics of the «Gut Massage» course: period filter, sortable and filterable tables, updated live."""
from urllib.parse import unquote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import course, course_stats, db, stats
from ..admin_tables import page_state, table_view
from ..auth import authorize
from .admin import TZ_COOKIE, _page, _to_login, stats_presets

router = APIRouter(prefix='/admin/course', default_response_class=HTMLResponse, include_in_schema=False)

PERM = 'course.view'


@router.get('/', name='admin_course')
async def course_overview(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    asked = unquote(request.cookies.get(TZ_COOKIE, ''))
    zone = stats.use_timezone(asked, await stats.known_zones(session))
    rng = stats.parse_range(request.query_params.get('from'), request.query_params.get('to'))
    views = {name: table_view(request, spec) for name, spec in (
        ('techs', course_stats.TECHS_TABLE), ('goals', course_stats.GOALS_TABLE),
        ('checks', course_stats.CHECKS_TABLE), ('people', course_stats.PEOPLE_TABLE))}
    data = await course_stats.overview(session, rng, zone, views['people'])
    for name in ('techs', 'goals', 'checks'):
        data[name] = views[name].sort(data[name])
    keep = page_state(list(views.values()))
    pages = {p.slug: p.nav for p in course.PUBLIC_PAGES}
    return _page(request, 'admin/course.html', user=user, rng=rng, page_state=keep, presets=stats_presets(), hide_new_link=True,
                 preset_query={n: urlencode([('from', r.start), ('to', r.end), *keep]) for n, r in stats_presets().items()},
                 tz_name=zone.key, tz_asked=asked, tz_label=stats.tz_label(), views=views,
                 status_labels=course_stats.STATUS_LABELS, step_names=pages, **data)
