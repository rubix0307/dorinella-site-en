"""Site analytics: page views and outbound-link clicks.

Every external link on the site points at /go/?to=<url>&from=<placement>&sig=<hmac>. The redirect
records the click and sends the visitor on. The HMAC signature means only links the site itself
generated are followed — /go/ can't be used as an open redirect to arbitrary sites.
"""
import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Request
from sqlalchemy import String, case, cast, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import live
from .core.config import settings
from .core.monitoring import metric
from .models import OutboundClick, PageView, Visitor

log = logging.getLogger(__name__)

_PLACEMENT = re.compile(r'^[a-z0-9:_-]{1,64}$')
_BOT = re.compile(r'bot|crawler|spider|crawling|preview|facebookexternalhit|telegrambot|whatsapp|headless|lighthouse'
                  r'|python-requests|python-urllib|httpx|aiohttp|curl/|wget/|go-http-client|java/|libwww|scrapy'
                  r'|axios/|node-fetch|slurp|embedly|vkshare|skypeuripreview|bytespider|gptbot|ccbot|petalbot', re.IGNORECASE)
_TABLET = re.compile(r'ipad|tablet|(android(?!.*mobile))', re.IGNORECASE)
_MOBILE = re.compile(r'mobi|iphone|android', re.IGNORECASE)


def _signature(url: str, placement: str) -> str:
    message = f'{url}\n{placement}'.encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()[:20]


def outbound_url(url: str, placement: str) -> str:
    """Tracked, signed link to an external URL. Use it for every external href."""
    if not _PLACEMENT.match(placement):
        raise ValueError(f'bad placement {placement!r}: use [a-z0-9:_-], up to 64 chars')
    return '/go/?' + urlencode({'to': url, 'from': placement, 'sig': _signature(url, placement)})


def verify(url: str, placement: str, signature: str) -> bool:
    if not url.startswith(('https://', 'http://')) or not _PLACEMENT.match(placement):
        return False
    return hmac.compare_digest(_signature(url, placement).encode(), signature.encode('utf-8', 'replace'))


def device_of(user_agent: str) -> str:
    if _BOT.search(user_agent):
        return 'bot'
    if _TABLET.search(user_agent):
        return 'tablet'
    if _MOBILE.search(user_agent):
        return 'mobile'
    return 'desktop'


def visitor_hash(ip: str, user_agent: str) -> str:
    """Anonymous visitor id: stable for one day, not reversible to an IP."""
    day = datetime.now(UTC).date().isoformat()
    raw = f'{day}\n{settings.secret_key}\n{ip}\n{user_agent}'.encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def cookie_visitor_hash(vid: str) -> str:
    """How the old token cookie (before visitor ids) was stored; app/visitors.py upgrades it."""
    raw = f'vid\n{settings.secret_key}\n{vid}'.encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _external_referrer(request: Request) -> str | None:
    """Host of another site that sent the visitor (e.g. l.instagram.com), else None."""
    referer = urlsplit(request.headers.get('referer', ''))
    if referer.netloc and referer.netloc != request.url.netloc:
        return (referer.hostname or '')[:255] or None
    return None


def _source_page(request: Request) -> tuple[str | None, dict[str, str]]:
    """Path and UTM tags of the page the click came from (same-origin Referer)."""
    referer = request.headers.get('referer', '')
    if not referer:
        return None, {}
    parts = urlsplit(referer)
    if parts.netloc and parts.netloc != request.url.netloc:
        return None, {}
    query = parse_qs(parts.query)
    utm: dict[str, str] = {k: query[k][0][:128] for k in ('utm_source', 'utm_medium', 'utm_campaign') if k in query}
    return (parts.path or '/')[:512], utm


REPEAT_WINDOW = 3   # seconds: a double tap or an in-app browser double request is one click; a real repeat counts


def is_speculative(request: Request) -> bool:
    """Browser prefetch / prerender (Chrome preloads a URL while it's being typed or hovered)."""
    purpose = (request.headers.get('sec-purpose') or request.headers.get('purpose')
               or request.headers.get('x-moz') or request.headers.get('x-purpose') or '')
    return any(word in purpose.lower() for word in ('prefetch', 'prerender', 'preview'))


def visitor_key(model):
    """Who a page view / click counts as, for unique-visitor numbers: the visitor id once that visitor has a
    row (their cookie came back), else the daily IP + user-agent hash. A browser that drops cookies gets a new
    id on every request; counting those ids would make each of its hits a new visitor."""
    return case((model.visitor_id.in_(select(Visitor.id)), cast(model.visitor_id, String)),
                else_=model.visitor_hash)


def _daily_hash(request: Request, user_agent: str) -> str:
    return visitor_hash(request.client.host if request.client else '', user_agent)


def request_visitor(request: Request):
    """The visitor app/main.py resolved for this request (app/visitors.Visitor), or None."""
    return getattr(request.state, 'visitor', None)


async def record_click(session: AsyncSession | None, request: Request, url: str, placement: str,
                       short_link_id: int | None = None) -> None:
    """Store the click; never raises — a broken database must not break the link."""
    if session is None or is_speculative(request):
        return
    user_agent = request.headers.get('user-agent', '')
    # low-cardinality tags only
    tags = {'source': 'short' if placement.startswith('short:') else 'site',
            'area': placement.split(':', 1)[0], 'device': device_of(user_agent)}
    page_path, utm = _source_page(request)
    daily = _daily_hash(request, user_agent)
    visitor = request_visitor(request)
    same_visitor = OutboundClick.visitor_id == visitor.id if visitor else OutboundClick.visitor_hash == daily
    try:
        # a double tap, a reload or an in-app browser retry is still one click
        repeat = await session.scalar(select(OutboundClick.id).where(
            same_visitor, OutboundClick.placement == placement, OutboundClick.target_url == url,
            OutboundClick.created_at >= datetime.now(UTC) - timedelta(seconds=REPEAT_WINDOW)).limit(1))
        if repeat is not None:
            metric('outbound.click.skipped', reason='repeat', source=tags['source'], area=tags['area'], device=tags['device'])
            return
        session.add(OutboundClick(
            target_url=url,
            target_host=(urlsplit(url).hostname or '')[:255],
            placement=placement,
            page_path=page_path,
            utm_source=utm.get('utm_source'),
            utm_medium=utm.get('utm_medium'),
            utm_campaign=utm.get('utm_campaign'),
            visitor_hash=daily,
            visitor_id=visitor.id if visitor else None,
            device=device_of(user_agent),
            user_agent=user_agent[:512] or None,
            short_link_id=short_link_id,
            referrer_host=_external_referrer(request),
        ))
        await session.commit()
        live.published('click')
        metric('outbound.click', source=tags['source'], area=tags['area'], device=tags['device'])
    except Exception:
        log.exception('could not record outbound click to %s', url)
        await session.rollback()


# ---------------------------------------------------------------- page views

# Only public pages count as visits.
_UNTRACKED_PREFIXES = ('/admin', '/login', '/account', '/auth/', '/api/', '/static/', '/go/', '/health', '/course/')


def is_trackable_view(request: Request, status_code: int, content_type: str) -> bool:
    if request.method != 'GET' or status_code != 200 or not content_type.startswith('text/html'):
        return False
    if request.url.path.startswith(_UNTRACKED_PREFIXES):
        return False
    if is_speculative(request):
        return False
    return device_of(request.headers.get('user-agent', '')) != 'bot'


async def record_page_view(sessions: async_sessionmaker | None, request: Request) -> None:
    """Store a page view; never raises."""
    if sessions is None:
        return
    user_agent = request.headers.get('user-agent', '')
    referer = urlsplit(request.headers.get('referer', ''))
    external = referer.hostname if referer.netloc and referer.netloc != request.url.netloc else None
    query = request.query_params
    try:
        async with sessions() as session:
            session.add(PageView(
                path=request.url.path[:512],
                referrer_host=(external or '')[:255] or None,
                utm_source=(query.get('utm_source') or '')[:128] or None,
                utm_medium=(query.get('utm_medium') or '')[:128] or None,
                utm_campaign=(query.get('utm_campaign') or '')[:128] or None,
                visitor_hash=_daily_hash(request, user_agent),
                visitor_id=visitor.id if (visitor := request_visitor(request)) else None,
                device=device_of(user_agent),
            ))
            await session.commit()
            live.published('view')
            metric('page.view', device=device_of(user_agent))
    except Exception:
        log.exception('could not record page view of %s', request.url.path)
