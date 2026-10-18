"""Admin sessions: the signed session cookie (the way in is a Google account, see google_login.py)."""
import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from . import audit, permissions
from .core.config import settings
from .models import AdminUser

SESSION_COOKIE = 'dori_admin'
SESSION_TTL = 7 * 24 * 3600

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalize_email(email: str) -> str:
    return email.strip().lower()


# Accounts have no password any more (the way in is a Google account, app/google_login.py). The column stays, holding
# this marker: it can never be a stored scrypt hash, and it is part of the session signature below.
NO_PASSWORD = '!'  # noqa: S105 — a marker, not a password


async def find_or_create(session: AsyncSession, email: str, name: str = '') -> AdminUser:
    """The account of this e-mail address — how a Google sign-in finds the existing accounts. A new address gets a plain
    account (role «user»: it can sign in, has a profile, and opens nothing in the admin until someone gives it rights)."""
    email = normalize_email(email)
    user = await session.scalar(select(AdminUser).where(func.lower(AdminUser.email) == email))
    if user is None:
        user = AdminUser(email=email, name=name or None, password_hash=NO_PASSWORD, role='user', session_version=0, onboarding_state='pending')
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:                  # the same person pressed the button twice
            await session.rollback()
            user = await session.scalar(select(AdminUser).where(func.lower(AdminUser.email) == email))
    if user is not None and name and not user.name:        # accounts made before the name was asked for
        user.name = name
    if user is None:                        # the other press created it, yet it is not found: cannot happen
        raise RuntimeError('account not found after a duplicate insert')
    return user


async def sign_in(session: AsyncSession, user: AdminUser) -> AdminUser:
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user


# ---------------------------------------------------------------- session cookie

def _session_signature(user: AdminUser, expires: int) -> str:
    # The session version is part of the message: signing out, switching the account off or changing its access bumps
    # it and ends every session, and an old cookie never comes back to life.
    message = f'admin-session\n{user.id}\n{expires}\n{user.password_hash}\n{user.session_version or 0}'.encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def session_cookie(user: AdminUser) -> tuple[str, int]:
    """(the cookie's value, its lifetime in seconds)."""
    expires = int(time.time()) + SESSION_TTL
    return f'{user.id}.{expires}.{_session_signature(user, expires)}', SESSION_TTL


def login_response(response: Response, request: Request, user: AdminUser) -> Response:
    value, ttl = session_cookie(user)
    response.set_cookie(SESSION_COOKIE, value, max_age=ttl, httponly=True, samesite='lax',
                        secure=request.url.scheme == 'https', path='/')
    return response


def logout_response(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE, path='/')
    return response


async def current_admin(request: HTTPConnection, session: AsyncSession | None) -> AdminUser | None:
    raw = request.cookies.get(SESSION_COOKIE, '')
    if session is None or raw.count('.') != 2:
        return None
    user_id, expires, signature = raw.split('.')
    if not (user_id.isdigit() and expires.isdigit()) or int(expires) < time.time():
        return None
    user = await session.get(AdminUser, int(user_id))
    if user is None or not user.is_active:
        return None
    if not hmac.compare_digest(_session_signature(user, int(expires)), signature):
        return None
    await audit.use_actor(session, 'user', user.id)        # whatever this request changes is logged as this person's doing
    return user


class Forbidden(Exception):
    """Signed in, but the account lacks the permission: main.py turns it into the 403 page (or JSON for fetch calls)."""

    def __init__(self, user: AdminUser, api: bool = False):
        super().__init__('forbidden')
        self.user, self.api = user, api


async def authorize(request: Request, session: AsyncSession | None, perm: str | None = None, *,
                    post: bool = False, api: bool = False) -> AdminUser | None:
    """The account behind the request with its permissions loaded; None = send to the login page.

    Raises Forbidden when `perm` is missing. The permission is checked before anything about the target of the
    request is looked up, so a refused account learns nothing about which ids exist.
    """
    user = await current_admin(request, session)
    if user is None or session is None or (post and not same_origin(request)):    # no session: current_admin gave None
        return None
    user.perms = await permissions.load(session, user)
    if perm is not None and perm not in user.perms:
        raise Forbidden(user, api)
    return user


# ---------------------------------------------------------------- request hygiene

def same_origin(request: HTTPConnection) -> bool:
    """POSTs (and the live socket) must come from our own pages; SameSite=Lax on the cookie as well."""
    origin = request.headers.get('origin') or request.headers.get('referer')
    if not origin:
        return False  # every current browser sends Origin on POST; no header = not our page
    return origin.split('/')[2:3] == [request.url.netloc]
