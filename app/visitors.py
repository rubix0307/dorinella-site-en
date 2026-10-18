"""Visitor id: every visitor gets a UUIDv7 on the first page and keeps it — the key of every metric and of
the saved test attempts, and what moves into an account when the person signs in.

The id lives in a signed cookie, `v1.<uuid hex>.<day>.<state>.<signature>`:
- the HMAC means only ids this server issued are accepted, so the database can keep the raw uuid — a leaked
  row is not a working cookie, and nobody can make up someone else's id;
- `day` (days since 1970) is when the cookie was last written: it is written again once that is a week old,
  so an active visitor never reaches the browsers' 400-day limit — the id is theirs for good;
- `state` is `n` until the cookie comes back once (then the visitor gets a row in `visitors`), `c` after.
  Bots and cookie-less clients therefore never get a row, and no request needs a database read to know.

The cookie is `__Host-dori_vid` on the https site (host-only, Secure — a subdomain can't plant an id) and
plain `dori_vid` on http (local development). The old `dori_vid` cookie (a random token, before UUIDs) is
upgraded once: a new id is issued and remembers the old one's hash, so the history comes along.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import Request, Response
from sqlalchemy import CursorResult, func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .core.config import settings
from .models import TestAttempt, Visitor as VisitorRow, VisitorAccount

log = logging.getLogger(__name__)

LEGACY_COOKIE = 'dori_vid'
COOKIE = '__Host-dori_vid' if settings.site_url.startswith('https://') else LEGACY_COOKIE
MAX_AGE = 60 * 60 * 24 * 400        # browsers cap a cookie's life at about 400 days anyway
RENEW_AFTER_DAYS = 7
_LEGACY = re.compile(r'^[A-Za-z0-9_-]{16,64}$')   # secrets.token_urlsafe — never has a dot
# Not for these: files, health checks, the admin, JSON clients (an API caller's own cookie is still read).
_UNTRACKED = ('/static/', '/media/', '/health', '/robots.txt', '/favicon', '/admin', '/login', '/account', '/auth/', '/api/', '/sitemap', '/checkout', '/stripe/')


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, version, 74 random bits — time-ordered, no collisions."""
    value = (time.time_ns() // 1_000_000) << 80 | int.from_bytes(os.urandom(10)) & ((1 << 80) - 1)
    value = value & ~(0xF << 76) | 0x7 << 76          # version 7
    value = value & ~(0x3 << 62) | 0x2 << 62          # RFC 4122 variant
    return uuid.UUID(int=value)


uuid7 = getattr(uuid, 'uuid7', uuid7)   # Python 3.14 has its own


def created_at(vid: uuid.UUID) -> datetime:
    """When the id was issued (its first visit): the time is part of a UUIDv7."""
    return datetime.fromtimestamp((vid.int >> 80) / 1000, UTC)


def _keys() -> tuple[bytes, ...]:
    if settings.visitor_keys:
        return tuple(k.encode() for k in settings.visitor_keys)
    return (hmac.new(settings.secret_key.encode(), b'visitor-cookie-key', hashlib.sha256).digest(),)


def _sign(key: bytes, payload: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:16]).decode().rstrip('=')


def _today() -> int:
    return int(time.time() // 86400)


def encode(vid: uuid.UUID, state: str, day: int | None = None) -> str:
    payload = f'v1.{vid.hex}.{_today() if day is None else day}.{state}'
    return f'{payload}.{_sign(_keys()[0], payload)}'


@dataclass(frozen=True)
class Parsed:
    id: uuid.UUID
    day: int
    state: str              # 'n' — not seen back yet, 'c' — has come back
    current_key: bool       # signed with the first key (False: an older one, re-sign it)


def decode(value: str) -> Parsed | None:
    """The cookie's content, or None when it is not one this server signed."""
    parts = value.split('.')
    if len(parts) != 5 or parts[0] != 'v1' or parts[3] not in ('n', 'c'):
        return None
    payload, signature = '.'.join(parts[:4]), parts[4]
    for n, key in enumerate(_keys()):
        if hmac.compare_digest(_sign(key, payload), signature):
            try:
                return Parsed(uuid.UUID(hex=parts[1]), int(parts[2]), parts[3], current_key=n == 0)
            except ValueError:
                return None
    return None


@dataclass
class Visitor:
    """Who this request is: on request.state.visitor (see app/main.py)."""
    id: uuid.UUID
    new: bool                       # issued by this very request
    returned: bool = False          # the cookie came back for the first time: give the visitor a row now
    legacy_hash: str | None = None  # an old dori_vid upgraded by this request
    write_cookie: bool = False
    state: str = 'n'


def wants_cookie(request: Request) -> bool:
    """Pages people open get an id; files, the admin, API clients, HEAD probes, browser preloads (the real
    navigation that follows gets it) and bots don't."""
    from .analytics import device_of, is_speculative
    if request.method not in ('GET', 'POST') or request.url.path.startswith(_UNTRACKED) or is_speculative(request):
        return False
    return device_of(request.headers.get('user-agent', '')) != 'bot'


def resolve(request: Request, issue: bool) -> Visitor | None:
    """The visitor of this request; a new id when there is none and `issue` is set."""
    raw = request.cookies.get(COOKIE, '')
    parsed = decode(raw) if raw else None
    if parsed is not None:
        returned = parsed.state == 'n'
        stale = _today() - parsed.day >= RENEW_AFTER_DAYS or not parsed.current_key
        return Visitor(parsed.id, new=False, returned=returned, write_cookie=returned or stale, state='c')
    if not issue:
        return None
    legacy = request.cookies.get(LEGACY_COOKIE, '')
    if _LEGACY.match(legacy):
        from .analytics import cookie_visitor_hash
        # someone who has been here before: no need to wait for the cookie to come back
        return Visitor(uuid7(), new=True, returned=True, legacy_hash=cookie_visitor_hash(legacy),
                       write_cookie=True, state='c')
    return Visitor(uuid7(), new=True, write_cookie=True)


async def ensure_row(session: AsyncSession, vid: uuid.UUID, legacy_hash: str | None = None) -> None:
    """The visitor's row in `visitors`, made if it isn't there yet (the caller commits)."""
    if await session.get(VisitorRow, vid) is None:
        session.add(VisitorRow(id=vid, legacy_hash=legacy_hash))
        await session.flush()


async def confirm(sessions: async_sessionmaker | None, visitor: Visitor) -> None:
    """The cookie came back (or an old dori_vid was upgraded): give the visitor a row, . Never raises — a broken database must not break the page."""
    if sessions is None or not visitor.returned:
        return
    try:
        async with sessions() as session:
            await ensure_row(session, visitor.id, visitor.legacy_hash)
            await session.commit()
    except Exception:
        log.exception('could not record the visitor row')


async def link_to_user(session: AsyncSession, visitor_id: uuid.UUID, user_id: int) -> int:
    """The visitor signed in: everything saved under their id that belongs to no account yet moves to this one.

    Called on every sign-in (the caller commits). Rows already moved stay with their account, so after a
    sign-out, a new test and a sign-in again only the new test moves; a test taken on a shared laptop after
    someone else signed out goes to whoever signs in next on it. The visitor id itself is never removed.
    Page views and clicks keep only the visitor id: visitor_accounts says whose they are. Returns how many
    test attempts moved."""
    await ensure_row(session, visitor_id)
    link = await session.get(VisitorAccount, (visitor_id, user_id))
    if link is None:
        session.add(VisitorAccount(visitor_id=visitor_id, user_id=user_id))
    else:
        link.last_signed_in_at = func.now()
    # an UPDATE gives a CursorResult (it has rowcount); execute() is typed with the general Result
    moved = cast(CursorResult, await session.execute(update(TestAttempt).where(
        TestAttempt.visitor_id == visitor_id, TestAttempt.user_id.is_(None)).values(user_id=user_id)))
    await session.flush()
    return moved.rowcount or 0


def write(response: Response, request: Request, visitor: Visitor) -> None:
    """Set the cookie when this request has to (a new id, a first return, a week-old cookie)."""
    if not visitor.write_cookie:
        return
    response.set_cookie(COOKIE, encode(visitor.id, visitor.state), max_age=MAX_AGE, httponly=True,
                        samesite='lax', secure=COOKIE.startswith('__Host-') or request.url.scheme == 'https',
                        path='/')
    if visitor.legacy_hash and COOKIE != LEGACY_COOKIE:
        response.delete_cookie(LEGACY_COOKIE, path='/')
    # a response that hands out an id must never be stored by a shared cache and given to someone else
    cache = response.headers.get('cache-control', '')
    if 'private' not in cache and 'no-store' not in cache:
        cache = ', '.join(p for p in (part.strip() for part in cache.split(',')) if p and p != 'public')
        response.headers['cache-control'] = f'private, {cache}' if cache else 'private'
