"""Aggregations for the admin dashboard. Days are calendar days in the admin's own time zone (the browser's)."""
import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Boolean, Date, DateTime, Integer, String, and_, bindparam, case, cast, exists, func, literal, null, or_, select, text, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_tables import Column, Page, TableSpec, TableView, fetch_page
from .analytics import visitor_key
from .i18n import Message
from .models import FavoriteLink, OutboundClick, PageView, ShortLink

DEFAULT_TZ = ZoneInfo('UTC')
# The admin's own time zone (the browser's, see use_timezone): "today", day/hour buckets, shown times.
_tz: ContextVar[ZoneInfo] = ContextVar('admin_tz', default=DEFAULT_TZ)


def tz() -> ZoneInfo:
    return _tz.get()


# Browsers still report some renamed zones by their old names; the database may know only the new ones.
_RENAMED = {
    'Europe/Kiev': 'Europe/Kyiv', 'Europe/Uzhgorod': 'Europe/Kyiv', 'Europe/Zaporozhye': 'Europe/Kyiv',   # the admin may sit in Kyiv
    'Asia/Calcutta': 'Asia/Kolkata', 'Asia/Saigon': 'Asia/Ho_Chi_Minh', 'Asia/Katmandu': 'Asia/Kathmandu',
    'Asia/Rangoon': 'Asia/Yangon', 'Asia/Ulan_Bator': 'Asia/Ulaanbaatar', 'America/Godthab': 'America/Nuuk',
    'Atlantic/Faeroe': 'Atlantic/Faroe', 'Pacific/Enderbury': 'Pacific/Kanton', 'Pacific/Truk': 'Pacific/Chuuk',
    'Pacific/Ponape': 'Pacific/Pohnpei', 'America/Buenos_Aires': 'America/Argentina/Buenos_Aires',
}
_db_zones: frozenset[str] | None = None


async def known_zones(session: AsyncSession) -> frozenset[str] | None:
    """Zone names the database understands (PostgreSQL, read once per process); None = no restriction."""
    global _db_zones
    if session.get_bind().dialect.name != 'postgresql':
        return None
    if _db_zones is None:
        _db_zones = frozenset((await session.scalars(text('SELECT name FROM pg_timezone_names'))).all())
    return _db_zones


def use_timezone(name: str | None, known: frozenset[str] | None = None) -> ZoneInfo:
    """Use the browser's IANA zone (e.g. 'Europe/Kyiv') for this request: renamed zones are mapped to
    their current names, and anything the server or the database doesn't know falls back to UTC."""
    zone = DEFAULT_TZ
    if name and len(name) <= 64 and re.fullmatch(r'[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}', name):
        name = _RENAMED.get(name, name)
        try:
            candidate = ZoneInfo(name)
            if known is None or name in known:
                zone = candidate
        except (ZoneInfoNotFoundError, ValueError):
            pass
    _tz.set(zone)
    return zone


def tz_label() -> str:
    """'America/New_York, UTC-04:00' — shown next to the period."""
    offset = datetime.now(tz()).strftime('%z')
    return f'{tz().key}, UTC{offset[:3]}:{offset[3:]}'

DEFAULT_DAYS = 30
MAX_DAYS = 731
PRESETS = (1, 7, 30, 90)   # 1 = today, shown in 15-minute steps
SLOT_MINUTES = 15          # a one-day chart's step
RANGE_STEP_MINUTES = 240   # the step of a chart over several days: 4 hours (6 points a day)


@dataclass(frozen=True)
class DateRange:
    start: date      # inclusive
    end: date        # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def utc_bounds(self) -> tuple[datetime, datetime]:
        """[from, to) as aware datetimes for the WHERE clause."""
        return (datetime.combine(self.start, time.min, tz()).astimezone(UTC),
                datetime.combine(self.end + timedelta(days=1), time.min, tz()).astimezone(UTC))

    def preset(self) -> int | None:
        """1/7/30/90 when the range is "last N days up to today", else None (custom)."""
        today = datetime.now(tz()).date()
        return self.days if self.end == today and self.days in PRESETS else None


def parse_range(start: str | None, end: str | None) -> DateRange:
    """Range from ?from=YYYY-MM-DD&to=YYYY-MM-DD; falls back to the last 30 days on bad input."""
    today = datetime.now(tz()).date()
    try:
        d_end = date.fromisoformat(end) if end else today
        d_start = date.fromisoformat(start) if start else d_end - timedelta(days=DEFAULT_DAYS - 1)
    except ValueError:
        d_end, d_start = today, today - timedelta(days=DEFAULT_DAYS - 1)
    d_end = min(d_end, today)
    if d_start > d_end:
        d_start, d_end = d_end, d_start
    d_start = max(d_start, d_end - timedelta(days=MAX_DAYS - 1))
    return DateRange(d_start, d_end)


def last_days(n: int) -> DateRange:
    today = datetime.now(tz()).date()
    return DateRange(today - timedelta(days=n - 1), today)


def step_of(rng: DateRange) -> int:
    """Minutes per chart point: 15 for one day, 4 hours for a longer range."""
    return SLOT_MINUTES if rng.days == 1 else RANGE_STEP_MINUTES


def _local_bucket(session: AsyncSession, column, step: int):
    """Start of the `step`-minute bucket (a local timestamp) of a timestamptz column in the admin's
    time zone (UTC on SQLite, used by tests)."""
    if session.get_bind().dialect.name == 'postgresql':
        return func.date_bin(text(f"interval '{step} minutes'"), func.timezone(tz().key, column),
                             text("timestamp '2000-01-01'"))
    minute = cast(func.strftime('%H', column), Integer) * 60 + cast(func.strftime('%M', column), Integer)
    start = (minute / step) * step
    return (func.strftime('%Y-%m-%d ', column).op('||')(func.printf('%02d:%02d', start / 60, start % 60))
            .op('||')(':00'))


def _bucket_key(value, step: int) -> tuple[date, int]:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment.date(), (moment.hour * 60 + moment.minute) // step


def _points(rng: DateRange, step: int) -> list[tuple[str, tuple[date, int]]]:
    """(label, key) of every point of a chart: each day of the range cut into `step`-minute buckets;
    today only up to the current one — time that hasn't come yet is not zeros (the axis keeps it)."""
    now = datetime.now(tz())
    out = []
    for i in range(rng.days):
        day = rng.start + timedelta(days=i)
        if day > now.date():
            break
        last = (now.hour * 60 + now.minute) // step if day == now.date() else 24 * 60 // step - 1
        for n in range(last + 1):
            minutes = n * step
            out.append((f'{day.isoformat()}T{minutes // 60:02d}:{minutes % 60:02d}', (day, n)))
    return out


def _local_day(session: AsyncSession, column):
    """Calendar day of a timestamptz column in the admin's time zone (UTC day on SQLite, used by tests)."""
    if session.get_bind().dialect.name == 'postgresql':
        return cast(func.timezone(tz().key, column), Date)
    return func.date(column)


async def history_start(session: AsyncSession) -> date | None:
    """The first day (in the admin's zone) anything was recorded — the one-day chart scrolls no further back."""
    first = min((t for t in [await session.scalar(select(func.min(PageView.created_at))),
                             await session.scalar(select(func.min(OutboundClick.created_at)))] if t is not None),
                default=None)
    if first is None:
        return None
    if first.tzinfo is None:                      # SQLite (tests): naive UTC
        first = first.replace(tzinfo=UTC)
    return first.astimezone(tz()).date()


@dataclass(frozen=True)
class DayPoint:
    day: date
    views: int
    visitors: int


async def daily_visits(session: AsyncSession, rng: DateRange) -> list[DayPoint]:
    since, until = rng.utc_bounds
    day = _local_day(session, PageView.created_at).label('day')
    rows = (await session.execute(
        select(day, func.count(), func.count(visitor_key(PageView).distinct()))
        .where(PageView.created_at >= since, PageView.created_at < until)
        .group_by(day)
    )).all()
    by_day = {(r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0]))): (r[1], r[2]) for r in rows}
    return [DayPoint(d, *by_day.get(d, (0, 0)))
            for d in (rng.start + timedelta(days=i) for i in range(rng.days))]


async def unique_visitors(session: AsyncSession, rng: DateRange) -> int:
    """Distinct visitors over the whole period (one person on 7 days is 1, not 7)."""
    since, until = rng.utc_bounds
    return await session.scalar(
        select(func.count(visitor_key(PageView).distinct()))
        .where(PageView.created_at >= since, PageView.created_at < until)) or 0


async def visitor_ids_since(session: AsyncSession) -> date | None:
    """The day page views started carrying visitor ids: before it, a visitor is counted per day (IP hash)."""
    first = await session.scalar(select(func.min(PageView.created_at)).where(PageView.visitor_id.isnot(None)))
    if first is None:
        return None
    if isinstance(first, str):              # SQLite (tests)
        first = datetime.fromisoformat(first)
    return (first if first.tzinfo else first.replace(tzinfo=UTC)).astimezone(tz()).date()


async def visits_timeline(session: AsyncSession, rng: DateRange) -> list[dict]:
    """The views / visitors charts: page views and unique visitors per 15 minutes (one day) or 4 hours."""
    since, until = rng.utc_bounds
    step = step_of(rng)
    bucket = _local_bucket(session, PageView.created_at, step).label('bucket')
    rows = (await session.execute(
        select(bucket, func.count(), func.count(visitor_key(PageView).distinct()))
        .where(PageView.created_at >= since, PageView.created_at < until)
        .group_by(bucket)
    )).all()
    found = {_bucket_key(r[0], step): (r[1], r[2]) for r in rows}
    return [{'d': label, 'v': found.get(key, (0, 0))[0], 'u': found.get(key, (0, 0))[1]}
            for label, key in _points(rng, step)]


CHARTS = ('views', 'visitors', 'clicks')   # what the tiles can chart; 'views' by default


@dataclass(frozen=True)
class LinkRow:
    """One row of the links table: a site link in one placement, or a short link (link_id set)."""
    target_url: str
    placement: str
    source: str
    clicks: int
    visitors: int
    last_click: datetime | None
    favorite: str
    link_id: int | None = None       # short links only, from here on
    slug: str = ''
    title: str = ''
    is_active: bool = True
    created_at: datetime | None = None
    status: str = 'active'

    @property
    def is_short(self) -> bool:
        return self.link_id is not None

    @property
    def is_deleted(self) -> bool:
        """Clicks of a short link that no longer exists (placement 'short:<slug>:deleted')."""
        return self.link_id is None and self.placement.startswith('short:') and self.placement.endswith(':deleted')

    @property
    def deleted_slug(self) -> str:
        return self.placement.removeprefix('short:').removesuffix(':deleted') if self.is_deleted else ''

    @property
    def host(self) -> str:
        return urlsplit(self.target_url).hostname or self.target_url

    @property
    def path(self) -> str:
        parts = urlsplit(self.target_url)
        return parts.path + (f'?{parts.query}' if parts.query else '') if parts.path not in ('', '/') or parts.query else ''


# 'site' = a link on the website, 'short' = an admin short link (/l/<slug>, e.g. shared on Instagram)
SOURCE_LABELS = {'site': 'Site', 'short': 'Short link'}
_source = case((OutboundClick.placement.like('short:%'), 'short'), else_='site')   # by placement: a deleted short link stays 'short'

# The period is bound per query (:since / :until), so the union below is built once.
_SINCE = bindparam('since', type_=DateTime(timezone=True))
_UNTIL = bindparam('until', type_=DateTime(timezone=True))
_IN_RANGE = and_(OutboundClick.created_at >= _SINCE, OutboundClick.created_at < _UNTIL, OutboundClick.device != 'bot')

# Site links (and clicks of deleted short links): grouped clicks of the period.
_site_rows = (
    select(
        OutboundClick.target_url.label('target_url'),
        OutboundClick.placement.label('placement'),
        _source.label('source'),
        func.count().label('clicks'),
        func.count(visitor_key(OutboundClick).distinct()).label('visitors'),
        func.max(OutboundClick.created_at).label('last_click'),
        case((exists().where(FavoriteLink.target_url == OutboundClick.target_url,
                             FavoriteLink.placement == OutboundClick.placement), '1'), else_='0').label('favorite'),
        cast(null(), Integer).label('link_id'),
        literal('', String).label('slug'),
        literal('', String).label('title'),
        literal(True, Boolean).label('is_active'),
        cast(null(), DateTime(timezone=True)).label('created_at'),
        case((OutboundClick.placement.like('short:%:deleted'), 'deleted'), else_='active').label('status'),
    )
    .where(_IN_RANGE, OutboundClick.short_link_id.is_(None))
    .group_by(OutboundClick.target_url, OutboundClick.placement)
)
# Every short link — also one nobody clicked yet, so it can always be found and managed here.
_short_rows = (
    select(
        ShortLink.target_url.label('target_url'),
        (literal('short:', String) + ShortLink.slug).label('placement'),
        literal('short', String).label('source'),
        func.count(OutboundClick.id).label('clicks'),
        func.count(visitor_key(OutboundClick).distinct()).label('visitors'),
        func.max(OutboundClick.created_at).label('last_click'),
        case((ShortLink.is_favorite, '1'), else_='0').label('favorite'),
        ShortLink.id.label('link_id'),
        ShortLink.slug.label('slug'),
        ShortLink.title.label('title'),
        ShortLink.is_active.label('is_active'),
        ShortLink.created_at.label('created_at'),
        case((ShortLink.is_active, 'active'), else_='off').label('status'),
    )
    .outerjoin(OutboundClick, and_(OutboundClick.short_link_id == ShortLink.id, _IN_RANGE))
    .group_by(ShortLink.id)
)
ROWS = union_all(_site_rows, _short_rows).subquery('link_rows')

# default order: short links first ('short' < 'site'), then by clicks — the owner's own links on top
LINKS_TABLE = TableSpec('links', default='source', tiebreak=('-clicks', '-last', 'link', 'placement'),
                        row_key=('target_url', 'placement'), columns=(
    Column('link', 'Link', expr=ROWS.c.target_url, searchable=True, field='target_url'),
    Column('source', 'Source', expr=ROWS.c.source, filterable=True, field='source'),
    Column('placement', 'Placement', expr=ROWS.c.placement, filterable=True, searchable=True,
           field='placement'),   # 'short:q83we' — so a short link's code finds it
    Column('clicks', 'Clicks', expr=ROWS.c.clicks, numeric=True, field='clicks'),
    Column('visitors', 'Unique', expr=ROWS.c.visitors, numeric=True, field='visitors'),
    Column('last', 'Last', expr=ROWS.c.last_click, desc_first=True, field='last_click'),
    # search-only: the name the owner gave a short link ("story about breakfast")
    Column('title', 'Name', expr=ROWS.c.title, searchable=True, sortable=False),
    Column('fav', 'Favorites', expr=ROWS.c.favorite, filterable=True, sortable=False, field='favorite'),   # ?links_fav=1
    Column('status', 'Status', expr=ROWS.c.status, filterable=True, sortable=False, field='status',
           default=('active',)),     # ?links_status=off — without it only working links are listed
))
STATUS_LABELS = {'active': 'Active', 'off': 'Off', 'deleted': 'Deleted'}


async def link_statuses(session: AsyncSession, rng: 'DateRange') -> list[tuple[str, int]]:
    """Rows of the links table per status — the options of the status filter (always all three, in order)."""
    counts: dict[str, int] = dict((await session.execute(
        select(ROWS.c.status, func.count()).group_by(ROWS.c.status), _period(rng))).all())
    return [(status, counts.get(status, 0)) for status in STATUS_LABELS]


def _period(rng: 'DateRange') -> dict:
    since, until = rng.utc_bounds
    return {'since': since, 'until': until}


async def outbound_links(session: AsyncSession, rng: DateRange, view: TableView) -> Page:
    """One page (cursor-paginated) of the links table: site links + short links, filtered and sorted by `view`."""
    return await fetch_page(session, select(ROWS), view, lambda m: LinkRow(**m), params=_period(rng))


def _shown_clicks(session: AsyncSession, view: TableView):
    """The clicks of the period behind exactly the rows the links table shows under its filters."""
    shown = view.where(select(ROWS.c.target_url, ROWS.c.placement, ROWS.c.link_id), session.get_bind().dialect.name).subquery()
    return and_(_IN_RANGE, or_(
        OutboundClick.short_link_id.in_(select(shown.c.link_id).where(shown.c.link_id.isnot(None))),
        and_(OutboundClick.short_link_id.is_(None),
             tuple_(OutboundClick.target_url, OutboundClick.placement).in_(
                 select(shown.c.target_url, shown.c.placement).where(shown.c.link_id.is_(None)))),
    ))


async def timeline(session: AsyncSession, rng: DateRange, view: TableView) -> list[dict]:
    """The chart: (clicks, unique visitors) of exactly the rows the links table shows under its filters —
    per day, or per hour when the range is a single day. Every bucket is present (zeros included)."""
    step = step_of(rng)
    bucket = _local_bucket(session, OutboundClick.created_at, step).label('bucket')
    stmt = (
        select(bucket, func.count(), func.count(visitor_key(OutboundClick).distinct()))
        .where(_shown_clicks(session, view))
        .group_by(bucket)
    )
    rows = (await session.execute(stmt, _period(rng))).all()
    found = {_bucket_key(r[0], step): (r[1], r[2]) for r in rows}
    return [{'d': label, 'c': found.get(key, (0, 0))[0], 'u': found.get(key, (0, 0))[1]}
            for label, key in _points(rng, step)]


# ---------------------------------------------------------------- devices

DEVICES = (('mobile', 'smartphone', 'Phone'), ('desktop', 'monitor', 'Computer'), ('tablet', 'tablet', 'Tablet'))


@dataclass(frozen=True)
class DeviceShare:
    key: str
    icon: str
    label: str
    people: int         # unique visitors on this device (one person on a phone and a laptop counts in both)
    hits: int           # page views / clicks
    total: int          # people over all devices: the share's base

    @property
    def share(self) -> float:
        return self.people / self.total if self.total else 0.0


def _shares(rows) -> list[DeviceShare]:
    found = {device: (people, hits) for device, people, hits in rows}
    total = sum(people for people, _ in found.values())
    return [DeviceShare(key, icon, label, *found.get(key, (0, 0)), total) for key, icon, label in DEVICES]


async def visits_by_device(session: AsyncSession, rng: DateRange) -> list[DeviceShare]:
    """Site visitors (and their page views) per device in the period."""
    since, until = rng.utc_bounds
    return _shares((await session.execute(
        select(PageView.device, func.count(visitor_key(PageView).distinct()), func.count())
        .where(PageView.created_at >= since, PageView.created_at < until, PageView.device != 'bot')
        .group_by(PageView.device))).all())


async def clicks_by_device(session: AsyncSession, rng: DateRange, view: TableView) -> list[DeviceShare]:
    """Who clicked the links (and how often) per device — under the links table's filters, like the chart."""
    return _shares((await session.execute(
        select(OutboundClick.device, func.count(visitor_key(OutboundClick).distinct()), func.count())
        .where(_shown_clicks(session, view))
        .group_by(OutboundClick.device), _period(rng))).all())


async def link_sources(session: AsyncSession, rng: DateRange) -> list[tuple[str, int]]:
    """Clicks per source (site / short links) in the range — options of the source filter."""
    since, until = rng.utc_bounds
    rows = (await session.execute(
        select(_source, func.count())
        .where(OutboundClick.created_at >= since, OutboundClick.created_at < until, OutboundClick.device != 'bot')
        .group_by(_source).order_by(_source.desc())
    )).all()
    return [(r[0], r[1]) for r in rows]


async def link_placements(session: AsyncSession, rng: DateRange) -> list[tuple[str, int]]:
    """Every placement clicked in the range with its click count — the options of the table filter."""
    since, until = rng.utc_bounds
    rows = (await session.execute(
        select(OutboundClick.placement, func.count())
        .where(OutboundClick.created_at >= since, OutboundClick.created_at < until, OutboundClick.device != 'bot')
        .group_by(OutboundClick.placement)
        .order_by(func.count().desc(), OutboundClick.placement)
    )).all()
    return [(r[0], r[1]) for r in rows]


# Human-readable names for link placements (anything unknown is shown as-is): the area is the part before the colon.
_AREAS = {
    'header': 'Header', 'menu': 'Mobile menu', 'footer': 'Footer', 'contacts': 'Home page · contacts', 'privacy': 'Privacy policy', 'terms': 'Terms',
    'links': 'Links page', 'test-result': 'Test result', 'faq': 'FAQ', 'short': 'Short link',
}


def placement_label(placement: str) -> str:
    """'test-result:gut' -> «Test result · gut»; a Message, so the admin's language translates the area."""
    area, _, detail = placement.partition(':')
    if area == 'short' and detail.endswith(':deleted'):
        detail = Message('%(slug)s (deleted)', slug=detail.removesuffix(':deleted'))
    name = _AREAS.get(area)
    return Message('%(area)s · %(detail)s', area=Message(name), detail=detail) if name and detail else Message(name or placement)


def local_datetime(value: datetime) -> str:
    """'02.10.2026, 14:51' in the admin's time zone (SQLite hands back naive UTC datetimes)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz()).strftime('%d.%m.%Y, %H:%M')


def short_datetime(value: datetime) -> str:
    """'02.10, 14:51' for this year, '02.10.2025, 14:51' otherwise — fits a phone card."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone(tz())
    return local.strftime('%d.%m, %H:%M' if local.year == datetime.now(tz()).year else '%d.%m.%Y, %H:%M')
