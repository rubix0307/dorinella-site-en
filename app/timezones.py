"""The time zone of an admin: an IANA name from ZONES, or BROWSER (the zone the browser reports).

The default is UTC. A moment is stored in UTC; what an admin types for a schedule is read in the zone shown next to the field, and that zone is always
written out (`label`), so that "14:00" never stands alone. In the BROWSER mode the zone comes from the cookie that admin.js refreshes on every admin page;
a missing or unknown value falls back to the default.
"""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT = 'UTC'
BROWSER = 'browser'
COOKIE = 'admin_tz'          # set by admin.js on every admin page (the IANA zone of the browser)

ZONES: tuple[str, ...] = (
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Toronto', 'Europe/London', 'Europe/Dublin',
    'Europe/Kyiv', 'Europe/Warsaw', 'Europe/Berlin', 'Europe/Prague', 'Europe/Vienna', 'Europe/Rome', 'Europe/Paris', 'Europe/Madrid',
    'Europe/Lisbon', 'Europe/Athens', 'Europe/Bucharest', 'Europe/Sofia', 'Europe/Chisinau', 'Europe/Riga', 'Europe/Vilnius',
    'Europe/Tallinn', 'Europe/Helsinki', 'Europe/Istanbul', 'Europe/Moscow', 'Asia/Tbilisi', 'Asia/Dubai', 'Asia/Jerusalem',
    'Australia/Sydney',
)


def zone(name: str | None) -> ZoneInfo:
    """The zone of a name; anything unknown is the default."""
    try:
        return ZoneInfo(name) if name else ZoneInfo(DEFAULT)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(DEFAULT)


def is_valid_setting(value: str) -> bool:
    return value == BROWSER or value in ZONES


def resolve(setting: str | None, cookie: str | None) -> ZoneInfo:
    """The zone to use for an admin: their choice, or what the browser reports when the choice is BROWSER."""
    if setting == BROWSER:
        return zone(cookie) if cookie and len(cookie) <= 64 and cookie.isascii() else zone(None)
    return zone(setting)


def label(tz: ZoneInfo, moment: datetime | None = None) -> str:
    """'America/New_York (UTC−4)': the name and the offset at that moment."""
    moment = (moment or datetime.now(UTC)).astimezone(tz)
    offset = moment.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = '+' if minutes >= 0 else '−'
    hours, rest = divmod(abs(minutes), 60)
    return f"{tz.key} (UTC{sign}{hours}{f':{rest:02d}' if rest else ''})"
