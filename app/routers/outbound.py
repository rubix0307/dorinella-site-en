"""Tracked redirects — /go/ (links on the site), /l/<slug> (admin short links) — and /health."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, seo, shortlinks
from ..analytics import is_speculative, record_click, verify
from ..core.config import settings
from ..core.monitoring import metric
from ..core.templating import absolute_url
from ..models import ShortLink

router = APIRouter(include_in_schema=False)


@router.api_route('/go/', methods=['GET', 'HEAD'], name='outbound')
async def outbound(
    request: Request,
    to: str = Query(max_length=2048),
    placement: str = Query(alias='from', max_length=64),
    sig: str = Query(max_length=64),
    session: AsyncSession | None = Depends(db.get_optional_session),
):
    if not verify(to, placement, sig):
        raise HTTPException(status_code=400, detail='Invalid link')
    return await _tracked_redirect(request, session, to, placement)


@router.api_route('/l/{slug}', methods=['GET', 'HEAD'], name='short_link')
async def short_link(request: Request, slug: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    """Admin short link: count the hit, then redirect (inactive or unknown codes are a 404)."""
    if session is None:
        raise HTTPException(status_code=404)
    link = await session.scalar(select(ShortLink).where(ShortLink.slug == slug.lower(), ShortLink.is_active))
    if link is None:
        metric('short_link.not_found')
        raise HTTPException(status_code=404)
    return await _tracked_redirect(request, session, link.target_url, shortlinks.placement(link.slug), link.id)


async def _tracked_redirect(request: Request, session: AsyncSession | None, to: str, placement: str,
                            short_link_id: int | None = None) -> Response:
    """Record the click under the visitor's id (app/main.py gives a new visitor one on this very response), redirect."""
    if request.method == 'HEAD':           # link previews (messengers) check the target: answer, don't count
        return RedirectResponse(to, status_code=302, headers={'Cache-Control': 'no-store'})
    if is_speculative(request):
        metric('outbound.click.skipped', reason='prefetch')
        # a preload is not a visit: refuse it (uncacheable), so the browser makes the real request when clicked
        return Response(status_code=503, headers={'Cache-Control': 'no-store', 'Vary': 'Sec-Purpose',
                                                  'X-Robots-Tag': 'noindex, nofollow'})
    await record_click(session, request, to, placement, short_link_id=short_link_id)
    return RedirectResponse(to, status_code=302, headers={
        'Cache-Control': 'no-store',          # every click must reach the server
        'X-Robots-Tag': 'noindex, nofollow',
    })


ROBOTS = """User-agent: *
Disallow: /admin
Disallow: /login
Disallow: /account
Disallow: /auth/
Disallow: /api/
Disallow: /go/
Disallow: /l/
"""


def _vary(headers: dict) -> dict:
    """Without SITE_URL the addresses come from the Host of the request: a cache must not hand one host's answer to another."""
    return headers if settings.site_url else {**headers, 'Vary': 'Host'}


@router.api_route('/sitemap.xml', methods=['GET', 'HEAD'], name='sitemap')
async def sitemap(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    """Every public page with the date it last changed, when known (app/seo.py). A database failure gives the pages of the code, for five minutes."""
    base = absolute_url(request, '')
    body, complete = await seo.sitemap_body(base, session)
    return Response(body, media_type='application/xml', headers=_vary({'Cache-Control': 'public, max-age=600' if complete else 'public, max-age=300'}))


@router.api_route('/robots.txt', methods=['GET', 'HEAD'], name='robots')
async def robots(request: Request):
    """Search engines: everything public; not the admin, sign-in, the cabinet, the API or the tracked redirects. The sitemap is named here."""
    return Response(ROBOTS + f"\nSitemap: {absolute_url(request, '/sitemap.xml')}\n", media_type='text/plain', headers=_vary({'Cache-Control': 'public, max-age=86400'}))


@router.get('/health', name='health')
async def health():
    """Liveness + database readiness, used by the Docker healthcheck."""
    if db.engine is None:
        return {'status': 'ok', 'database': 'disabled'}
    try:
        await db.ping()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({'status': 'error', 'database': type(exc).__name__}, status_code=503)
    return {'status': 'ok', 'database': 'ok'}
