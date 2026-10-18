"""Admin area: login and the analytics dashboard."""
import asyncio
from datetime import date, timedelta
from urllib.parse import parse_qsl, unquote, urlencode

from fastapi import APIRouter, Depends, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, google_login, live, page_stats, permissions, shortlinks, stats
from ..admin_tables import TRANSIENT_PARAMS, page_state, table_view
from ..auth import authorize, current_admin, login_response, logout_response, same_origin
from ..core.monitoring import metric
from ..core.templating import absolute_url, render
from ..models import FavoriteLink, OutboundClick, ShortLink
from .account import LOGIN_ERRORS, complete_sign_in, login_page, sign_in_redirect

router = APIRouter(prefix='/admin', default_response_class=HTMLResponse, include_in_schema=False)

def stats_presets():
    return {n: stats.last_days(n) for n in stats.PRESETS}


NO_INDEX = {'X-Robots-Tag': 'noindex, nofollow', 'Cache-Control': 'no-store'}


def _page(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    response = render(request, template, status_code=status_code, **context)
    response.headers.update(NO_INDEX)
    return response


def _to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(request.app.url_path_for('admin_login'), status_code=303, headers=NO_INDEX)


@router.get('/login', name='admin_login')
async def login_form(request: Request):
    """The sign-in page is public now (/login); old links and the admin's redirects land here and go on."""
    return RedirectResponse(request.app.url_path_for('login'), status_code=303, headers=NO_INDEX)


@router.get('/login/google', name='admin_login_google')
async def login_google(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    """Send the browser to Google; the signed flow cookie remembers what must come back."""
    if session is None or not google_login.configured():
        return login_page(request, session, 'Вхід через Google не налаштований.', 503)
    url, cookie = google_login.start(request)
    response = RedirectResponse(url, status_code=303, headers=NO_INDEX)
    response.set_cookie(google_login.FLOW_COOKIE, cookie, max_age=google_login.FLOW_TTL, httponly=True, samesite='lax',
                        secure=request.url.scheme == 'https', path='/admin/login/google')
    return response


@router.get('/login/google/callback', name='admin_login_google_callback')
async def login_google_callback(request: Request, code: str = '', state: str = '', error: str = '',
                                session: AsyncSession | None = Depends(db.get_optional_session)):
    """Google sends the person back here. The account of that address is found (an unknown address gets a plain account)."""
    if session is None or not google_login.configured():
        return login_page(request, session, 'Вхід через Google не налаштований.', 503)

    def back(message: str, status_code: int = 401):
        response = login_page(request, session, message, status_code)
        response.delete_cookie(google_login.FLOW_COOKIE, path='/admin/login/google')
        return response

    if error or not code:
        metric('admin.login', result='failed')
        return back(LOGIN_ERRORS['denied'])
    try:
        email, name = await google_login.finish(request, code, state)
    except google_login.GoogleError as problem:
        metric('admin.login', result='failed')
        return back(str(problem))
    user = await complete_sign_in(request, session, email, name)
    if user is None:
        metric('admin.login', result='failed')
        return back(LOGIN_ERRORS['off'], 403)
    metric('admin.login', result='ok')
    response = login_response(sign_in_redirect(request, user, NO_INDEX), request, user)
    response.delete_cookie(google_login.FLOW_COOKIE, path='/admin/login/google')
    return response


@router.post('/logout', name='admin_logout')
async def logout(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    if not same_origin(request):
        return RedirectResponse(request.app.url_path_for('index'), status_code=303)
    if session is not None and (user := await current_admin(request, session)):
        user.session_version = (user.session_version or 0) + 1       # a copied cookie dies with this sign-out
        await session.commit()
    return logout_response(RedirectResponse(request.app.url_path_for('index'), status_code=303, headers=NO_INDEX))


TZ_COOKIE = 'admin_tz'   # the browser's IANA time zone, set by admin.js
CHART_COOKIE = 'admin_chart'   # which tile the chart shows: views / visitors / clicks (admin.js)
DEVICES_COOKIE = 'admin_devices'   # «Пристрої» left open (admin.js, data-remember)


NOTICES = {
    'created': 'Посилання /l/{slug} створено.',
    'updated': 'Зміни в /l/{slug} збережено.',
    'enabled': '/l/{slug} знову працює.',
    'disabled': '/l/{slug} вимкнено: за ним відкриється сторінка 404.',
    'deleted': '/l/{slug} видалено. Його переходи залишились у статистиці.',
}


async def _analytics(request: Request, session: AsyncSession, user, *, status_code: int = 200,
                     error: str | None = None, form: dict | None = None, dialog: str | None = None):
    """The one admin page: stats and ONE links table — site links and short links (with their dialogs)."""
    asked = unquote(request.cookies.get(TZ_COOKIE, ''))           # the browser's zone (admin.js) — before any date math
    zone = stats.use_timezone(asked, await stats.known_zones(session))
    rng = stats.parse_range(request.query_params.get('from'), request.query_params.get('to'))
    links_view = table_view(request, stats.LINKS_TABLE)
    days = await stats.daily_visits(session, rng)                 # chronological, for the tiles
    page = await stats.outbound_links(session, rng, links_view)   # one page: filtered, sorted, cursor-paginated
    chart = await stats.timeline(session, rng, links_view)        # the same filters, per day (per hour for one day)
    visits = await stats.visits_timeline(session, rng)            # views & visitors, same buckets
    devices = {'visits': await stats.visits_by_device(session, rng),
               'clicks': await stats.clicks_by_device(session, rng, links_view)}   # the link filters apply, as above
    chart_metric = request.cookies.get(CHART_COOKIE, 'views')      # the tile the admin picked (admin.js)
    chart_metric = chart_metric if chart_metric in stats.CHARTS else 'views'
    placements = await stats.link_placements(session, rng)        # options of the placement filter
    sources = await stats.link_sources(session, rng)              # options of the source filter
    statuses = await stats.link_statuses(session, rng)            # options of the status filter

    notice = request.query_params.get('notice')
    slug = request.query_params.get('slug', '')
    slug = slug if shortlinks.SLUG_RE.match(slug) else ''
    notice_link = None
    if slug:    # the link a notice is about (may be on a later page of the feed)
        notice_link = await session.scalar(select(ShortLink).where(ShortLink.slug == slug))

    keep = page_state([links_view])
    return _page(
        request, 'admin/dashboard.html', status_code=status_code, user=user, rng=rng, page_state=keep,
        presets=stats_presets(),
        preset_query={n: urlencode([('from', r.start), ('to', r.end), *keep]) for n, r in stats_presets().items()},
        # clicks
        page=page, links=page.rows, links_view=links_view,
        placements=placements, sources=sources, source_labels=stats.SOURCE_LABELS,
        statuses=statuses, status_labels=stats.STATUS_LABELS,
        chart_data={'step': stats.step_of(rng), 'slots': rng.days * 1440 // stats.step_of(rng),
                    'history_start': (start.isoformat() if (start := await stats.history_start(session)) else None),
                    'views': [{'d': p['d'], 'v': p['v']} for p in visits],
                    'visitors': [{'d': p['d'], 'v': p['u']} for p in visits],
                    'clicks': chart},
        devices=devices, devices_open=request.cookies.get(DEVICES_COOKIE) == '1',
        chart_metric=chart_metric, hourly=rng.days == 1, visitor_ids_since=await stats.visitor_ids_since(session),
        totals={'views': sum(d.views for d in days), 'visitors': await stats.unique_visitors(session, rng),
                'clicks': sum(point['c'] for point in chart)},   # follows the link filters, like the chart and the table
        placement_label=stats.placement_label,
        # short links (rows of the same table) and their dialogs
        placement=shortlinks.placement,
        error=error, form=form or {}, dialog=dialog,
        notice=NOTICES[notice].format(slug=slug) if notice in NOTICES and slug else None,
        notice_kind=notice if notice in NOTICES and slug else None,
        notice_link=notice_link,
        highlight=slug if notice in ('created', 'updated', 'enabled', 'disabled') else '',
        # helpers
        tz_name=zone.key, tz_asked=asked, tz_label=stats.tz_label(),
        local_datetime=stats.local_datetime, short_datetime=stats.short_datetime,
        short_base=absolute_url(request, shortlinks.PREFIX),
    )


MAX_CHART_DAYS_BACK = 366


@router.get('/chart-day', name='admin_chart_day')
async def chart_day(request: Request, session: AsyncSession = Depends(db.get_session)):
    """One more day for the one-day chart, scrolled back to (admin.js): its 15-minute points, the
    clicks under the same link filters as the page."""
    user = await authorize(request, session, 'analytics.view', api=True)
    if user is None:
        return JSONResponse({'detail': 'login'}, status_code=401, headers=NO_INDEX)
    stats.use_timezone(unquote(request.cookies.get(TZ_COOKIE, '')), await stats.known_zones(session))
    today = stats.last_days(1).start
    try:
        day = date.fromisoformat(request.query_params.get('day', ''))
    except ValueError:
        return JSONResponse({'detail': 'day'}, status_code=400, headers=NO_INDEX)
    if not today - timedelta(days=MAX_CHART_DAYS_BACK) <= day <= today:
        return JSONResponse({'detail': 'day'}, status_code=400, headers=NO_INDEX)
    rng = stats.DateRange(day, day)
    visits = await stats.visits_timeline(session, rng)
    clicks = await stats.timeline(session, rng, table_view(request, stats.LINKS_TABLE))
    return JSONResponse({'day': day.isoformat(), 'step': stats.step_of(rng),
                         'views': [{'d': p['d'], 'v': p['v']} for p in visits],
                         'visitors': [{'d': p['d'], 'v': p['u']} for p in visits],
                         'clicks': clicks}, headers=NO_INDEX)


@router.get('/', name='admin_dashboard')
async def dashboard(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session)
    if user is None:
        return _to_login(request)
    if not user.can('analytics.view'):          # the way in for accounts without analytics: their first allowed section
        route = permissions.first_section(user.perms) or 'admin_profile'
        return RedirectResponse(request.app.url_path_for(route), status_code=303, headers=NO_INDEX)
    return await _analytics(request, session, user)


@router.get('/pages/', name='admin_pages')
async def pages_report(request: Request, session: AsyncSession = Depends(db.get_session)):
    """«Сторінки»: views by section, then (?section=…) the section's pages — with the analytics' period."""
    user = await authorize(request, session, 'analytics.view')
    if user is None:
        return _to_login(request)
    asked = unquote(request.cookies.get(TZ_COOKIE, ''))
    zone = stats.use_timezone(asked, await stats.known_zones(session))
    rng = stats.parse_range(request.query_params.get('from'), request.query_params.get('to'))
    section = request.query_params.get('section', '')
    section = section if section in page_stats.SECTIONS else ''
    if section:
        rows, views, visitors = await page_stats.pages(session, rng, section)
    else:
        rows, views, visitors = await page_stats.sections(session, rng)
    keep = [('section', section)] if section else []
    period = [('from', rng.start), ('to', rng.end)]
    return _page(
        request, 'admin/pages.html', user=user, rng=rng, page_state=keep, presets=stats_presets(),
        preset_query={n: urlencode([('from', r.start), ('to', r.end), *keep]) for n, r in stats_presets().items()},
        hide_new_link=True, rows=rows, views=views, visitors=visitors, section=section,
        sections=page_stats.SECTIONS,
        section_href=lambda key: '?' + urlencode([*period, ('section', key)]),
        all_href='?' + urlencode(period),
        tz_name=zone.key, tz_asked=asked, tz_label=stats.tz_label(),
    )


# ---------------------------------------------------------------- short links (actions; they live on the analytics page)

def _back(request: Request, notice: str, slug: str) -> RedirectResponse:
    """Back to the analytics page with its period / sorting / search, plus a notice to show.

    admin.js posts every form to `action + location.search`, so the page state rides on the POST URL;
    the target is always our own GET page — never taken from the Referer.
    """
    query = [(k, v) for k, v in parse_qsl(request.url.query) if k not in TRANSIENT_PARAMS]
    if slug:
        query += [('notice', notice), ('slug', slug)]
    path = request.app.url_path_for('admin_dashboard')
    return RedirectResponse(f'{path}?{urlencode(query)}' if query else path, status_code=303)


async def _save(session: AsyncSession) -> bool:
    """Commit, then a live-update event; False when the slug was taken concurrently."""
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    live.published('link')
    return True


@router.post('/links/', name='admin_links_create')
async def links_create(
    request: Request,
    target_url: str = Form('', max_length=2048),
    slug: str = Form('', max_length=64),
    title: str = Form('', max_length=200),
    allow_duplicate: str = Form(''),
    session: AsyncSession = Depends(db.get_session),
):
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    slug, target_url, title = shortlinks.normalize_slug(slug), target_url.strip(), title.strip()
    form: dict[str, str | bool] = {'target_url': target_url, 'slug': slug, 'title': title}

    async def again(error: str):
        await session.refresh(user)       # a rollback expires it
        return await _analytics(request, session, user, status_code=400, error=error, form=form, dialog='new')

    error = shortlinks.validate(slug, target_url)
    if not error and slug and await shortlinks.slug_taken(session, slug):
        error = shortlinks.taken_message(slug)
    if not error and not allow_duplicate and (same := await shortlinks.duplicate(session, target_url)):
        error, form['duplicate'] = shortlinks.duplicate_message(same), True
    if error:
        return await again(error)
    new_slug = slug or await shortlinks.free_auto_slug(session)
    session.add(ShortLink(slug=new_slug, target_url=target_url, title=title, created_by=user.id))
    if not await _save(session):          # someone took the same code between the check and the insert
        return await again(shortlinks.taken_message(new_slug))
    return _back(request, 'created', new_slug)


@router.post('/links/{link_id:int}', name='admin_links_update')
async def links_update(
    request: Request,
    link_id: int,
    target_url: str = Form('', max_length=2048),
    slug: str = Form('', max_length=64),
    title: str = Form('', max_length=200),
    active: str = Form(''),
    allow_duplicate: str = Form(''),
    session: AsyncSession = Depends(db.get_session),
):
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    link = await session.get(ShortLink, link_id)
    if link is None:
        return _back(request, 'deleted', '')
    slug = shortlinks.normalize_slug(slug) or link.slug
    new_url, new_title, is_active = target_url.strip(), title.strip(), active == 'on'
    form: dict[str, str | bool | int] = {'id': link_id, 'target_url': new_url, 'slug': slug, 'title': new_title,
                                         'active': is_active}

    async def again(error: str):
        await session.refresh(user)
        return await _analytics(request, session, user, status_code=400, error=error, form=form, dialog='edit')

    error = shortlinks.validate(slug, new_url)
    if not error and slug != link.slug and await shortlinks.slug_taken(session, slug, except_id=link_id):
        error = shortlinks.taken_message(slug)
    moved = shortlinks.same_url_key(new_url) != shortlinks.same_url_key(link.target_url)
    if not error and moved and not allow_duplicate and (same := await shortlinks.duplicate(session, new_url, link_id)):
        error, form['duplicate'] = shortlinks.duplicate_message(same), True   # only when the address is changed to it
    if error:
        return await again(error)
    if slug != link.slug:             # keep the link's history under its new name in «Аналітика»
        await session.execute(update(OutboundClick).where(OutboundClick.short_link_id == link_id)
                              .values(placement=shortlinks.placement(slug)))
    link.slug, link.target_url, link.title, link.is_active = slug, new_url, new_title, is_active
    if not await _save(session):
        return await again(shortlinks.taken_message(slug))
    return _back(request, 'updated', slug)


@router.post('/links/{link_id:int}/toggle', name='admin_links_toggle')
async def links_toggle(request: Request, link_id: int, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    link = await session.get(ShortLink, link_id)
    if link is None:
        return _back(request, 'deleted', '')
    link.is_active = not link.is_active
    slug, active = link.slug, link.is_active
    if not await _save(session):
        return _back(request, 'deleted', '')
    return _back(request, 'enabled' if active else 'disabled', slug)


@router.post('/links/{link_id:int}/delete', name='admin_links_delete')
async def links_delete(request: Request, link_id: int, session: AsyncSession = Depends(db.get_session)):
    """The link goes; its clicks stay (short_link_id → NULL, placement keeps 'short:<slug>')."""
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    link = await session.get(ShortLink, link_id)
    if link is None:
        return _back(request, 'deleted', '')
    slug = link.slug
    # its clicks stay, marked as a deleted link, so a new link with the same code starts from zero
    await session.execute(update(OutboundClick).where(OutboundClick.short_link_id == link_id)
                          .values(placement=shortlinks.deleted_placement(slug), short_link_id=None))   # not left to the FK
    await session.delete(link)
    await _save(session)
    return _back(request, 'deleted', slug)


# ---------------------------------------------------------------- favorites (★)

def _starred(request: Request, favorite: bool):
    """admin.js toggles the star in place (JSON); without JS the page reloads with its state."""
    if 'application/json' in request.headers.get('accept', ''):
        return JSONResponse({'favorite': favorite})
    return _back(request, '', '')


@router.post('/favorites', name='admin_favorite_toggle')
async def favorite_toggle(
    request: Request,
    target_url: str = Form(max_length=2048),
    placement: str = Form(max_length=64),
    session: AsyncSession = Depends(db.get_session),
):
    """Star / unstar one row of the clicks table (a destination in a placement)."""
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    found = await session.scalar(select(FavoriteLink).where(FavoriteLink.target_url == target_url,
                                                            FavoriteLink.placement == placement))
    if found:
        await session.delete(found)
    else:
        session.add(FavoriteLink(target_url=target_url, placement=placement))
    await _save(session)               # a double click racing itself: the unique key keeps one row
    return _starred(request, found is None)


@router.post('/links/{link_id:int}/favorite', name='admin_links_favorite')
async def links_favorite(request: Request, link_id: int, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'links.manage', post=True)
    if user is None:
        return _to_login(request)
    link = await session.get(ShortLink, link_id)
    if link is None:
        return _back(request, 'deleted', '')
    link.is_favorite = not link.is_favorite
    favorite = link.is_favorite
    await _save(session)
    return _starred(request, favorite)


@router.websocket('/ws', name='admin_ws')
async def live_updates(websocket: WebSocket):
    """Pushes {"type": "click" | "view" | "link" | "recipes" | "attempt" | "course"} whenever new data lands; the page
    re-renders itself if it shows that kind (data-live on its results block). The message is only the kind."""
    if not same_origin(websocket):                       # no cross-site sockets
        await websocket.close(code=1008)
        return
    if db.SessionLocal is None:
        await websocket.close(code=1011)
        return
    if not await _still_admin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = live.hub.subscribe()
    metric('admin.live_connections', live.hub.listeners, kind='gauge')
    receive = asyncio.create_task(websocket.receive_text())   # notices the client going away
    get = asyncio.create_task(queue.get())
    recheck = asyncio.get_running_loop().time() + RECHECK_SECONDS
    try:
        while True:
            timeout = max(0.0, recheck - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait({get, receive}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if get in done:
                kind = get.result()
                get = asyncio.create_task(queue.get())
                if not kind.startswith(live.SUPPORT_PREFIX):          # the chats have sockets of their own
                    await websocket.send_json({'type': kind})
            if receive in done:
                receive.result()                                  # raises WebSocketDisconnect
                receive = asyncio.create_task(websocket.receive_text())
            if asyncio.get_running_loop().time() >= recheck:      # signing out or switching the account off ends the socket too
                if not await _still_admin(websocket):
                    await websocket.close(code=1008)
                    return
                recheck = asyncio.get_running_loop().time() + RECHECK_SECONDS
    except WebSocketDisconnect:
        pass
    finally:
        live.hub.unsubscribe(queue)
        metric('admin.live_connections', live.hub.listeners, kind='gauge')
        get.cancel()
        receive.cancel()


RECHECK_SECONDS = 120
LIVE_PERMS = frozenset({'analytics.view', 'tests.view', 'telegram.posts'})   # the pages that update live: analytics, «Тести»


async def _still_admin(websocket: WebSocket) -> bool:
    async with db.new_session() as session:
        user = await current_admin(websocket, session)
        return user is not None and bool(LIVE_PERMS & await permissions.load(session, user))
