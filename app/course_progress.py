"""Progress and answers of the «Gut Massage» course, kept per person in the database (course_answers, with every change
in course_answer_log). Pages open one after another: a page opens when every page before it has been passed, and the safety
page is passed only with the confirmation «I have read the contraindications» for the current text version."""
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import course, live
from .models import CourseAnswer, CourseAnswerLog

COURSE = 'abdominal'
SAFETY_PAGE = 'safety'
_ID = re.compile(r'^[a-z0-9_-]{1,40}$')


def clean(key: str, value):
    """The value to store for an answer key, or None when the key or the value is not one the course knows."""
    if key == 'safety':
        if isinstance(value, dict) and isinstance(value.get('ack'), bool):
            return {'ack': value['ack'], 'ver': course.SAFETY_VERSION}
    elif key == 'checks':           # ticked items of the safety lists
        if isinstance(value, list) and len(value) <= 40 and all(isinstance(v, str) and _ID.match(v) for v in value):
            return sorted(set(value))
    elif key == 'goal':
        if isinstance(value, str) and value in {g['id'] for g in course.GOALS}:
            return value
    elif key.startswith('list.') and _ID.match(key[5:]) and isinstance(value, list) and len(value) <= 200 and all(isinstance(v, str) and _ID.match(v) for v in value):
        return sorted(set(value))
    return None


async def load(session: AsyncSession, user_id: int) -> dict[str, dict]:
    rows = await session.scalars(select(CourseAnswer).where(CourseAnswer.user_id == user_id, CourseAnswer.course == COURSE))
    return {r.key: {'value': r.value, 'at': r.updated_at} for r in rows}


async def save(session: AsyncSession, user_id: int, key: str, value, log_value=None) -> bool:
    """Store an answer (and log it) when it changed; False when the same value was already there."""
    try:
        return await _save(session, user_id, key, value, log_value)
    except IntegrityError:          # two requests inserted the same key at once: the second one now updates
        await session.rollback()
        return await _save(session, user_id, key, value, log_value)


async def _save(session: AsyncSession, user_id: int, key: str, value, log_value=None) -> bool:
    now = datetime.now(UTC)
    row = await session.scalar(select(CourseAnswer).where(CourseAnswer.user_id == user_id, CourseAnswer.course == COURSE,
                                                         CourseAnswer.key == key))
    if row is not None and row.value == value:
        return False
    if row is None:
        session.add(CourseAnswer(user_id=user_id, course=COURSE, key=key, value=value, created_at=now, updated_at=now))
    else:
        row.value, row.updated_at = value, now
    session.add(CourseAnswerLog(user_id=user_id, course=COURSE, key=key, value=value if log_value is None else log_value, at=now))
    await session.commit()
    live.published('course')            # the admin Course page refreshes itself
    return True


def safety_ok(state: dict) -> bool:
    s = (state.get('safety') or {}).get('value')
    return bool(isinstance(s, dict) and s.get('ack') and s.get('ver') == course.SAFETY_VERSION)


def is_done(state: dict, slug: str) -> bool:
    if 'done.' + slug not in state:
        return False
    return safety_ok(state) if slug == SAFETY_PAGE else True


def done_slugs(state: dict) -> set[str]:
    return {p.slug for p in course.PUBLIC_PAGES if is_done(state, p.slug)}


def first_open(state: dict) -> course.Page:
    """The page the person has to pass next (the last one when everything is passed)."""
    for p in course.PUBLIC_PAGES:
        if not is_done(state, p.slug):
            return p
    return course.PUBLIC_PAGES[-1]


OPEN_ANYTIME = frozenset({'author'})      # the page about the author does not wait for the rest


def unlocked(state: dict, slug: str, *, to_view: bool = True) -> bool:
    """A page is open when every page before it has been passed (`to_view=False`: the strict order, for passing it)."""
    if to_view and slug in OPEN_ANYTIME:
        return True
    for p in course.PUBLIC_PAGES:
        if p.slug == slug:
            return True
        if not is_done(state, p.slug):
            return False
    return False


def public_state(state: dict) -> dict:
    """What the page script gets: the answers only (the times stay in the database)."""
    return {k: {'value': v['value']} for k, v in state.items()}


VIEWED_AFTER = 5.0       # seconds of real playing after which a technique counts as watched


async def track(session: AsyncSession, user_id: int, key: str, seconds: float, completed: bool, plays: int) -> bool:
    """Add a watching report of one technique (key m1..m8) to its total; every report goes to the log as it came."""
    if key not in course.TECH_BY_KEY or not (seconds > 0 or completed or plays):
        return False
    row = await session.scalar(select(CourseAnswer).where(CourseAnswer.user_id == user_id, CourseAnswer.course == COURSE,
                                                         CourseAnswer.key == 'tech.' + key))
    cur = row.value if row is not None and isinstance(row.value, dict) else {}
    total = round(min(float(cur.get('seconds', 0)) + seconds, 36000.0), 1)
    value = {'seconds': total, 'plays': int(cur.get('plays', 0)) + plays, 'completed': bool(cur.get('completed')) or completed,
             'viewed': bool(cur.get('viewed')) or total >= VIEWED_AFTER}
    return await save(session, user_id, 'tech.' + key, value,
                      log_value={'seconds': round(seconds, 1), 'completed': completed, 'plays': plays})
