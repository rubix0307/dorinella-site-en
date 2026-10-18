"""The «Масаж живота» course: for the admins only while COURSE_ACCESS=admin (see app/course.py).

Whoever may not see a page gets *exactly* the answer of a page that doesn't exist: a plain HTTPException(404) raised
before anything about the requested path is looked up, for every method (an unknown path under /course answers 404 to
a POST too, there is no 405 that would give the route away), no redirect from /x to /x/, no 422 from a typed
parameter. Nothing public lists the course: no registry shared with the learn pages, no sitemap, robots.txt stays silent.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import course, course_mark, course_progress as progress, db, payments
from ..auth import current_admin, same_origin
from ..core.templating import render

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

ALL_METHODS = ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
PRIVATE = {'Cache-Control': 'private, no-store', 'Vary': 'Cookie', 'X-Robots-Tag': 'noindex, nofollow'}


async def _may_enter(request: Request, session: AsyncSession | None) -> bool:
    mode = course.access_mode()
    if mode == 'public':
        return True
    try:
        user = await current_admin(request, session)
    except Exception:
        # a database that is down must look like any other missing page, but not go unnoticed
        log.exception('course: the admin check failed')
        return False
    if user is not None and course.is_staff(user):
        request.state.course_admin = user
        return _accepted(user)
    if mode == 'paid' and user is not None and session is not None and await payments.has_entitlement(session, user):
        request.state.course_admin = user
        return _accepted(user)
    return False


def _accepted(user) -> bool:
    """Whoever may enter the course (a hook for conditions on top of the access mode)."""
    return True


async def gate(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)) -> None:
    if not await _may_enter(request, session):
        raise HTTPException(status_code=404)


def _private(response, *, admin_preview: bool):
    if admin_preview:
        response.headers.update(PRIVATE)
    return response


def _render(request: Request, template: str, **context) -> HTMLResponse:
    preview = course.access_mode() != 'public'
    who = getattr(request.state, 'course_admin', None)
    mark = course_mark.css_background(who.id) if who is not None else None       # a faint code of the signed-in person on the background
    response = render(request, template, course_preview=preview, course=course, course_mark=mark, **context)
    return _private(response, admin_preview=preview)


async def _state(request: Request, session: AsyncSession | None) -> dict | None:
    """The answers of the signed-in person; None when nobody is signed in (public mode): then nothing is stored or locked."""
    user = getattr(request.state, 'course_admin', None)
    if user is None or session is None:
        return None
    return await progress.load(session, user.id)


def _progress_context(state: dict | None) -> dict:
    if state is None:
        return {'course_state': None, 'course_done': set(), 'course_open': None, 'course_unlocked': None}
    return {'course_state': progress.public_state(state), 'course_done': progress.done_slugs(state),
            'course_open': progress.first_open(state).slug,
            'course_unlocked': {p.slug for p in course.PUBLIC_PAGES if progress.unlocked(state, p.slug)}}


@router.post(f'{course.BASE}state', name='course_state', dependencies=[Depends(gate)])
async def save_answer(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    user = getattr(request.state, 'course_admin', None)
    if user is None or session is None:
        return JSONResponse({'ok': True, 'saved': False})
    if not same_origin(request):
        raise HTTPException(status_code=404)
    try:
        body = await request.json()
        key, value = body['key'], body['value']
    except Exception:  # noqa: BLE001 — any unreadable body is a 400
        return JSONResponse({'ok': False}, status_code=400)
    cleaned = progress.clean(key, value) if isinstance(key, str) else None
    if cleaned is None:
        return JSONResponse({'ok': False}, status_code=400)
    state = await progress.load(session, user.id)
    if key == 'safety' and progress.safety_ok(state) and isinstance(cleaned, dict) and not cleaned['ack']:
        return JSONResponse({'ok': True, 'saved': False})        # a confirmation is not taken back
    if not progress.unlocked(state, 'tekhniky') and key.startswith('list.'):
        return JSONResponse({'ok': False}, status_code=400)
    changed = await progress.save(session, user.id, key, cleaned)
    return JSONResponse({'ok': True, 'saved': changed})


@router.post(f'{course.BASE}track', name='course_track', dependencies=[Depends(gate)])
async def track_watching(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    """A report of how long a technique was watched (the player sends it when it switches, ends or the page is left)."""
    user = getattr(request.state, 'course_admin', None)
    if user is None or session is None:
        return JSONResponse({'ok': True})
    if not same_origin(request):
        raise HTTPException(status_code=404)
    try:
        body = await request.json()
        key = str(body['key'])
        seconds = max(0.0, min(float(body.get('seconds', 0)), 600.0))
        completed, plays = bool(body.get('completed')), 1 if body.get('plays') else 0
    except Exception:  # noqa: BLE001 — any unreadable body is a 400
        return JSONResponse({'ok': False}, status_code=400)
    state = await progress.load(session, user.id)
    if progress.unlocked(state, 'tekhniky', to_view=False):
        await progress.track(session, user.id, key, seconds, completed, plays)
    return JSONResponse({'ok': True})


@router.post(f'{course.BASE}{{page}}/complete', name='course_complete')
async def complete(request: Request, page: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    current = course.PAGE_BY_SLUG.get(page)
    if not await _may_enter(request, session) or current is None:
        raise HTTPException(status_code=404)
    user = getattr(request.state, 'course_admin', None)
    if user is None or session is None:      # nobody to keep progress for: just move on
        following = course.neighbours(current)[1]
        return RedirectResponse(f'{course.BASE}{following.slug if following else ""}', status_code=303)
    if not same_origin(request):
        raise HTTPException(status_code=404)
    state = await progress.load(session, user.id)
    if not progress.unlocked(state, page, to_view=False):
        return RedirectResponse(f'{course.BASE}{progress.first_open(state).slug}/', status_code=303)
    if page == progress.SAFETY_PAGE:
        form = await request.form()
        if form.get('ack'):
            await progress.save(session, user.id, 'safety', {'ack': True, 'ver': course.SAFETY_VERSION})
            state = await progress.load(session, user.id)
        if not progress.safety_ok(state):
            return RedirectResponse(f'{course.BASE}{page}/?need=ack#ack', status_code=303)
    await progress.save(session, user.id, f'done.{page}', {'page': page})
    following = course.neighbours(current)[1]
    return RedirectResponse(f'{course.BASE}{following.slug}/' if following else course.BASE, status_code=303)


@router.api_route('/course', methods=ALL_METHODS, dependencies=[Depends(gate)])
async def bare() -> None:
    raise HTTPException(status_code=404)


async def _sales_only_user(request: Request, session: AsyncSession | None):
    """A signed-in person without the course who may buy it (COURSE_ACCESS=paid, payments public): they see the buy card on the home page, nothing else."""
    if course.access_mode() != 'paid' or session is None:
        return None
    try:
        user = await current_admin(request, session)
    except Exception:
        log.exception('course: the admin check failed')
        return None
    return user if user is not None and payments.shown_to(user) else None


@router.get(f'{course.BASE}', name='course_home')
@router.head(f'{course.BASE}')
async def home(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    if not await _may_enter(request, session):
        buyer = await _sales_only_user(request, session)
        if buyer is None or session is None:      # (a buyer exists only with a database)
            raise HTTPException(status_code=404)
        pay = await payments.offer_for(session, buyer)
        if pay is None:
            raise HTTPException(status_code=404)
        return _render(request, 'pages/course/home.html', first=course.PUBLIC_PAGES[0], my_review=None, pay=pay, sales_only=True, **_progress_context(None))
    state = await _state(request, session)
    first = progress.first_open(state) if state is not None else course.PUBLIC_PAGES[0]
    user = getattr(request.state, 'course_admin', None)
    my_review = None
    pay = await payments.offer_for(session, user) if user is not None and session is not None else None
    return _render(request, 'pages/course/home.html', first=first, my_review=my_review, pay=pay, **_progress_context(state))


@router.get(course.DATA_URL, name='course_data', dependencies=[Depends(gate)])
@router.head(course.DATA_URL, dependencies=[Depends(gate)])
async def data(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    state = await _state(request, session)
    if state is not None and not progress.unlocked(state, 'tekhniky'):
        raise HTTPException(status_code=404)
    response = FileResponse(course.DATA_FILE, media_type='application/json')
    return _private(response, admin_preview=course.access_mode() != 'public')


@router.get(f'{course.BASE}{{page}}/', name='course_page')
@router.head(f'{course.BASE}{{page}}/')
async def page(request: Request, page: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    current = course.PAGE_BY_SLUG.get(page)
    if not await _may_enter(request, session) or current is None:
        raise HTTPException(status_code=404)
    state = await _state(request, session)
    if state is not None and not progress.unlocked(state, current.slug):
        return RedirectResponse(f'{course.BASE}{progress.first_open(state).slug}/', status_code=303)
    previous, following = course.neighbours(current)
    return _render(request, 'pages/course/page.html', page=current, previous=previous, following=following,
                   pages=course.PUBLIC_PAGES, groups=course.GROUPS, techniques=course.TECHNIQUES,
                   need_ack=request.query_params.get('need') == 'ack', **_progress_context(state))


@router.api_route('/course/{rest:path}', methods=ALL_METHODS, dependencies=[Depends(gate)])
async def unknown(rest: str) -> None:
    raise HTTPException(status_code=404)
