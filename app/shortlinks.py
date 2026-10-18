"""Admin short links: /l/<slug> → target URL, every hit recorded as an outbound click."""
import re
import secrets
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ShortLink

PREFIX = '/l/'
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')   # custom codes: up to 32 chars
_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'          # no 0/o, 1/l/i — easy to read and retype
AUTO_LENGTH = 5


def placement(slug: str) -> str:
    return f'short:{slug}'


def deleted_placement(slug: str) -> str:
    """Clicks of a deleted link: still 'short', but never merged with a new link that reuses the code."""
    return f'short:{slug}:deleted'


def normalize_slug(raw: str) -> str:
    return raw.strip().lower()


def validate(slug: str, target_url: str) -> str | None:
    """Error message or None."""
    if slug and not SLUG_RE.fullmatch(slug):
        return 'Код: лише латинські літери, цифри та дефіс, до 32 символів.'
    if not re.fullmatch(r'https?://[^\s/]+\.[^\s]+', target_url) or len(target_url) > 2048:
        return 'Вкажіть повну адресу, що починається з https://'
    return None


def taken_message(slug: str) -> str:
    return f'Код «{slug}» вже зайнятий — оберіть інший.'


async def free_auto_slug(session: AsyncSession) -> str:
    for length in (AUTO_LENGTH, AUTO_LENGTH + 1, AUTO_LENGTH + 2):
        for _ in range(10):
            slug = ''.join(secrets.choice(_ALPHABET) for _ in range(length))
            if not await session.scalar(select(ShortLink.id).where(ShortLink.slug == slug)):
                return slug
    raise RuntimeError('could not find a free short-link code')


async def slug_taken(session: AsyncSession, slug: str, except_id: int | None = None) -> bool:
    stmt = select(ShortLink.id).where(ShortLink.slug == slug)
    if except_id is not None:
        stmt = stmt.where(ShortLink.id != except_id)
    return await session.scalar(stmt) is not None


def same_url_key(url: str) -> str:
    """What two addresses must share to count as the same: no www., any case of the host, no trailing slash
    or #fragment ('https://www.Site.com/a/' == 'https://site.com/a')."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower().removeprefix('www.')
    port = f':{parts.port}' if parts.port else ''
    return f'{host}{port}{parts.path.rstrip("/")}' + (f'?{parts.query}' if parts.query else '')


async def duplicate(session: AsyncSession, target_url: str, except_id: int | None = None) -> ShortLink | None:
    """An existing short link to the same address, if any (two links to one place split its clicks)."""
    key = same_url_key(target_url)
    for link in (await session.scalars(select(ShortLink).order_by(ShortLink.id))).all():
        if link.id != except_id and same_url_key(link.target_url) == key:
            return link
    return None


def duplicate_message(link: ShortLink) -> str:
    title = f' («{link.title}»)' if link.title else ''
    return f'На цю адресу вже веде {PREFIX}{link.slug}{title}.'
