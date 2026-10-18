"""Sign-in and the visitor's own cabinet. Anyone can sign in with a Google account; what the account may open in the
admin is decided by its role and rights, not here (an admin lands in the admin, anyone else in the cabinet)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, db, google_login, payments, permissions
from ..analytics import request_visitor
from ..auth import current_admin, find_or_create, login_response, same_origin, sign_in
from ..catalog import listed
from ..core.monitoring import metric
from ..core.templating import render
from ..visitors import link_to_user

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)

NO_STORE = {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow'}
LOGIN_ERRORS = {
    'denied': 'Google did not grant access. Please try again.',
    'off': 'This account has been switched off.',
}


def home_route(user) -> str:
    """Where the sign-in button leads: an admin to the admin, anyone else to the main page."""
    return 'admin_dashboard' if user.role in (permissions.ROOT, permissions.ADMIN) else 'index'


def landing_route(user) -> str:
    """Where /login sends a browser that is signed in already."""
    return 'admin_dashboard' if user.role in (permissions.ROOT, permissions.ADMIN) else 'account'


def sign_in_redirect(request: Request, user, headers: dict[str, str] | None = None, route: str | None = None) -> RedirectResponse:
    """Where a browser goes right after the sign-in (or when it opens /login signed in): to its page."""
    return RedirectResponse(str(request.app.url_path_for(route or home_route(user))), status_code=303, headers=headers or NO_STORE)


async def complete_sign_in(request: Request, session: AsyncSession, email: str, name: str = ''):
    """The account of a verified address (a new address gets a plain one), with what this browser did before signing in
    moved to it. None: the account is switched off."""
    await audit.system_now(session, 'sign-in')      # an account made here has no person yet; stays set for the rest of this request, nothing else writes
    user = await find_or_create(session, email, name)
    if user is None or not user.is_active:
        return None
    visitor = request_visitor(request)
    if visitor is not None and not visitor.new:
        await link_to_user(session, visitor.id, user.id)
    return await sign_in(session, user)


def login_page(request: Request, session, error: str = '', status_code: int = 200):
    response = render(request, 'pages/login.html', status_code=status_code, error=error, unavailable=session is None,
                      google=google_login.configured())
    response.headers.update(NO_STORE)
    return response


@router.get('/login', name='login')
async def login(request: Request, error: str = '', session: AsyncSession | None = Depends(db.get_optional_session)):
    if user := await current_admin(request, session):
        return sign_in_redirect(request, user, {}, landing_route(user))
    return login_page(request, session, LOGIN_ERRORS.get(error, ''))


@router.get('/account/', name='account')
async def account(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await current_admin(request, session)
    if user is None:
        return RedirectResponse(request.app.url_path_for('login'), status_code=303, headers=NO_STORE)
    return await account_page(request, session, user)


async def account_page(request: Request, session: AsyncSession, user):
    staff = user.role in (permissions.ROOT, permissions.ADMIN)
    # what the person can open: a purchase, or (for the admins, who may open everything) the product to preview
    owned = {slug: staff or await payments.has_entitlement(session, user, slug) for slug in listed()}
    response = render(request, 'pages/account.html', user=user, is_admin=staff, products=list(listed().values()), owned=owned,
                      purchases=await payments.purchases_for(session, user))
    response.headers.update(NO_STORE)
    return response


@router.post('/auth/google/one-tap', name='google_one_tap')
async def google_one_tap(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    """Google One Tap: the page sent the ID token Google gave the person. Only our own pages may send it (a foreign page
    must not be able to sign a browser in to an account it chose); the answer is JSON and the page reloads itself."""
    def fail(message: str, status_code: int):
        metric('admin.login', result='failed')
        return JSONResponse({'error': message}, status_code=status_code, headers=NO_STORE)

    if session is None or not google_login.configured():
        return JSONResponse({'error': 'off'}, status_code=503, headers=NO_STORE)
    if not same_origin(request):
        return fail('origin', 403)
    try:
        body = await request.json()
        email, name = await google_login.verify_credential(str(body.get('credential', '')) if isinstance(body, dict) else '')
    except (ValueError, google_login.GoogleError):
        return fail('credential', 401)
    user = await complete_sign_in(request, session, email, name)
    if user is None:
        return fail('off', 403)
    metric('admin.login', result='ok')
    return login_response(JSONResponse({'ok': True}, headers=NO_STORE), request, user)
