"""dorinellla — site on FastAPI.

Run locally:  uvicorn app.main:app --reload --reload-dir app

URL scheme:
    HTML  /                        home
          /products/               catalog
          /products/{slug}/        product page (guide or course)
    JSON  /api/v1/...              versioned API (see routers/api_v1.py)
"""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from . import analytics, db, legal, live, payments as payments_module, site, visitors
from .auth import Forbidden
from .catalog import PRODUCTS, listed
from .core import templating
from .core.config import settings
from .core.monitoring import init_sentry
from .core.templating import STATIC_DIR, render
from .routers import (
    account,
    admin,
    admin_course,
    admin_groups,
    admin_links_page,
    admin_orders,
    admin_profile,
    admin_tests,
    admin_users,
    api_v1,
    course,
    outbound,
    pages,
    payments,
)
from .routers.admin import NO_INDEX

init_sentry()   # before the app exists, so the FastAPI integration hooks in (no-op without SENTRY_DSN)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    live.start()
    payments_module.warn_misconfigured()
    if not settings.site_url:
        logging.getLogger(__name__).warning('SITE_URL is empty: the sitemap, canonical addresses and schema.org links are built from the Host of each request')
    yield
    await live.stop()
    if db.engine is not None:
        await db.get_engine().dispose()


app = FastAPI(title='dorinellla', docs_url='/api/docs', redoc_url=None, openapi_url='/api/openapi.json',
              redirect_slashes=False,  # trailing-slash redirects are permanent, see http_error()
              lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


# a content hash in the file name itself: fonts/manrope-cyrillic-c268b459a9.woff2, vendor chunks like spring-CFHloqPP.js
HASHED_NAME = re.compile(r'-[A-Za-z0-9_]{8,}\.(?:woff2|js)$')


class CachedStaticFiles(StaticFiles):
    """Static files with long-lived caching for fingerprinted (?v=…, or a hash in the name) URLs."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            if b'v=' in scope.get('query_string', b'') or HASHED_NAME.search(path):
                response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            else:
                response.headers['Cache-Control'] = 'public, max-age=3600'
        return response


class MediaFiles(StaticFiles):
    """Recipe videos and posters: named by the reel code, never change → cache for a year."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response


app.mount('/static', CachedStaticFiles(directory=STATIC_DIR), name='static')
if settings.media_url == '/media' and Path(settings.media_dir).is_dir():
    # recipe videos when they aren't on a bucket yet (local development); Range requests work
    app.mount('/media', MediaFiles(directory=settings.media_dir), name='media')
app.include_router(pages.router)
app.include_router(course.router)
app.include_router(account.router)
app.include_router(api_v1.router)
app.include_router(outbound.router)
app.include_router(admin.router)
app.include_router(admin_tests.router)
app.include_router(admin_course.router)
app.include_router(admin_links_page.router)
app.include_router(admin_profile.router)
app.include_router(admin_users.router)
app.include_router(admin_groups.router)
app.include_router(admin_orders.router)
app.include_router(payments.router)
templating.setup(app)
templating.templates.env.globals['catalog'] = PRODUCTS  # footer product links
templating.templates.env.globals['listed'] = listed     # only the products that are shown
templating.templates.env.globals['site'] = site          # the brand, the author, the language (app/site.py)
templating.templates.env.globals['legal'] = legal.context()   # the seller, the version date and the refund days of the legal pages (app/legal.py)


@app.middleware('http')
async def security_headers(request: Request, call_next):
    # Who this is, before the page runs: a new visitor's very first view is stored under their new id,
    # and the routes (recipe history, test attempts) read request.state.visitor.
    visitor = visitors.resolve(request, issue=visitors.wants_cookie(request))
    request.state.visitor = visitor
    if visitor is not None:
        await visitors.confirm(db.SessionLocal, visitor)
    response = await call_next(request)
    if analytics.is_trackable_view(request, response.status_code, response.headers.get('content-type', '')):
        await analytics.record_page_view(db.SessionLocal, request)
    if visitor is not None:
        visitors.write(response, request, visitor)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


def _is_api(request: Request) -> bool:
    return request.url.path.startswith('/api/')


def _route_exists(request: Request, path: str) -> bool:
    scope = {**request.scope, 'path': path}
    return any(route.matches(scope)[0] == Match.FULL for route in request.app.router.routes)


@app.exception_handler(Forbidden)
async def forbidden(request: Request, exc: Forbidden):
    if exc.api:
        return JSONResponse({'detail': 'forbidden'}, status_code=403, headers=NO_INDEX)
    response = render(request, 'admin/forbidden.html', status_code=403, user=exc.user)
    response.headers.update(NO_INDEX)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    if exc.status_code == 404 and request.method in ('GET', 'HEAD') and not path.endswith('/') \
            and not _is_api(request) and not path.startswith(('/static/', '/media/', '/course')) \
            and _route_exists(request, path + '/'):
        query = f'?{request.url.query}' if request.url.query else ''
        return RedirectResponse(path + '/' + query, status_code=301)
    if _is_api(request) or path.startswith(('/static/', '/media/')):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    if exc.status_code == 404:
        return render(request, 'pages/404.html', status_code=404)
    return _error_page(request, exc.status_code)


ERRORS = {
    500: ('Something went wrong', 'That was our mistake, not yours, and we can see it in our logs. Try refreshing the page in a minute, or head back to the home page.'),
    405: ("That doesn't work here", 'This address does not accept that kind of request. Head back to the home page and try again.'),
}
ERROR_DEFAULT = ("We couldn't open that page", 'Something went wrong. Head back to the home page and try again.')


def _error_page(request: Request, status_code: int) -> Response:
    heading, lead = ERRORS.get(status_code, ERROR_DEFAULT)
    try:
        response = render(request, 'pages/error.html', status_code=status_code, code=status_code, heading=heading, lead=lead)
    except Exception:       # the page itself must never be the reason for a second failure
        logging.getLogger(__name__).exception('error page failed')
        response = HTMLResponse(heading, status_code=status_code)
    response.headers.update(NO_INDEX)
    return response


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """Any bug nobody caught: a page in the site's style (JSON for the API) instead of the bare «Internal Server Error».
    Starlette still re-raises it afterwards, so the server log keeps the traceback."""
    if _is_api(request) or request.url.path.startswith(('/static/', '/media/')):
        return JSONResponse({'detail': 'server error'}, status_code=500, headers=NO_INDEX)
    return _error_page(request, 500)
