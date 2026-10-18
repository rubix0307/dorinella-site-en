"""Analytics of the «Gut Massage» course for the admin.

The period filter is about activity: who did something in that period (from course_answer_log). The funnel, the people table, the
goals and the ticked contraindications describe those people (their whole progress so far); the techniques and the day chart count
what happened inside the period. The data is small, so it is read whole and counted here. Every table is a list of plain objects
sorted by app/admin_tables.py."""
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any, overload
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import course
from .admin_tables import Column, TableSpec, encode_cursor
from .course_progress import COURSE
from .models import AdminUser, CourseAnswer, CourseAnswerLog

STALLED_AFTER = timedelta(days=7)

TECHS_TABLE = TableSpec('techs', default='number', tiebreak=('number',), columns=(
    Column('number', 'Technique', attr='number', numeric=True, desc_first=False),
    Column('viewers', 'Watched', attr='viewers', numeric=True),
    Column('completed', 'To the end', attr='completed', numeric=True),
    Column('rate', 'Finished', attr='rate', numeric=True),
))
GOALS_TABLE = TableSpec('goals', default='-n', tiebreak=('title',), columns=(
    Column('title', 'Goal', attr='title'),
    Column('n', 'Chose', attr='n', numeric=True),
))
CHECKS_TABLE = TableSpec('checks', default='-n', tiebreak=('label',), columns=(
    Column('label', 'Item', attr='label'),
    Column('group', 'Group', attr='group'),
    Column('n', 'Ticked', attr='n', numeric=True),
))
PEOPLE_TABLE = TableSpec('people', default='-last', tiebreak=('email',), row_key=('email',), first_take=10, columns=(
    Column('email', 'Account', attr='email', searchable=True),
    Column('status', 'Status', attr='status_rank', filterable=True),
    Column('next', 'Next step', attr='next_order', filterable=True),
    Column('done', 'Steps', attr='done', numeric=True),
    Column('techs', 'Techniques', attr='techs', numeric=True),
    Column('started', 'Started', attr='started', desc_first=True),
    Column('last', 'Last activity', attr='last', desc_first=True),
))

STATUS_LABELS = {'finished': 'Finished', 'progress': 'In progress', 'stalled': 'Stalled'}


@overload
def _aware(moment: datetime) -> datetime: ...
@overload
def _aware(moment: None) -> None: ...
def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)      # SQLite hands datetimes back without a zone
    return moment


def span(seconds: float | None) -> str:
    if seconds is None:
        return '—'
    seconds = round(seconds)
    if seconds < 60:
        return f'{seconds} s'
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} min {rest:02d} s' if rest else f'{minutes} min'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours} h {minutes:02d} min'
    days, hours = divmod(hours, 24)
    return f'{days} d {hours} h'


def _safety_labels() -> dict[str, tuple[str, str]]:
    out = {}
    block = next(b for b in course.PAGE_BY_SLUG['safety'].blocks if b.kind == 'safety')
    for group, items in (('Do not massage', block.d['absolute']), ('Be careful', block.d['relative'])):
        for item_id, label, _hint in items:
            out[item_id] = (group, label)
    return out


class Row:
    """One line of a table; every column of the table spec names one of these attributes."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    if TYPE_CHECKING:                        # the columns are dynamic: tell the checkers that any attribute may be read and set
        def __getattr__(self, name: str) -> Any: ...
        def __setattr__(self, name: str, value: Any) -> None: ...


def _bounds(rng, zone: ZoneInfo) -> tuple[datetime, datetime]:
    return (datetime.combine(rng.start, time.min, tzinfo=zone), datetime.combine(rng.end + timedelta(days=1), time.min, tzinfo=zone))


async def overview(session: AsyncSession, rng, zone: ZoneInfo, people_view) -> dict:
    start, end = _bounds(rng, zone)
    users = {u.id: u for u in await session.scalars(select(AdminUser))}
    answers = (await session.scalars(select(CourseAnswer).where(CourseAnswer.course == COURSE))).all()
    events = [(e.user_id, e.key, e.value, _aware(e.at)) for e in await session.scalars(
        select(CourseAnswerLog).where(CourseAnswerLog.course == COURSE, CourseAnswerLog.at >= start.astimezone(UTC),
                                      CourseAnswerLog.at < end.astimezone(UTC)))]
    by_user: dict[int, dict[str, CourseAnswer]] = defaultdict(dict)
    for a in answers:
        by_user[a.user_id][a.key] = a
    pages = course.PUBLIC_PAGES
    now = datetime.now(UTC)

    active = {uid for uid, *_ in events}
    first_seen = {uid: min(_aware(r.created_at) for r in rows.values()) for uid, rows in by_user.items()}
    new_people = {uid for uid, at in first_seen.items() if start <= at < end}

    # ---- KPIs of the period
    steps_done = sum(1 for _, key, _, _ in events if key.startswith('done.'))
    finished_now = sum(1 for uid, key, _, _ in events if key == f'done.{pages[-1].slug}')
    watch_seconds = 0.0
    views = 0
    for _, key, value, _ in events:
        if key.startswith('tech.') and isinstance(value, dict):
            watch_seconds += float(value.get('seconds', 0))
            views += 1 if value.get('plays') else 0
    kpis = {'people': len(by_user), 'active': len(active), 'new': len(new_people), 'steps': steps_done, 'finished': finished_now,
            'views': views, 'watch': span(watch_seconds)}

    # ---- the day chart (by local day; by week when the period is long)
    bucket = 1 if rng.days <= 45 else 7
    days: dict[date, dict] = {}
    day = rng.start
    while day <= rng.end:
        days[day] = {'people': set(), 'steps': 0}
        day += timedelta(days=bucket)
    for uid, key, _, at in events:
        local = at.astimezone(zone).date()
        slot = rng.start + timedelta(days=((local - rng.start).days // bucket) * bucket)
        if slot in days:
            days[slot]['people'].add(uid)
            days[slot]['steps'] += 1 if key.startswith('done.') else 0
    peak = max([1] + [max(len(d['people']), d['steps']) for d in days.values()])
    unit = next(u for u in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000) if peak <= u * 4)
    ticks = list(range(0, (-(-peak // unit)) * unit + 1, unit))
    top = ticks[-1] or 1
    chart = [{'label': d.strftime('%d.%m'),
              'when': d.strftime('%d.%m.%Y') if bucket == 1 else f"{d.strftime('%d.%m')} – {min(d + timedelta(days=bucket - 1), rng.end).strftime('%d.%m')}",
              'people': len(v['people']), 'steps': v['steps'],
              'people_h': round(len(v['people']) * 100 / top, 1), 'steps_h': round(v['steps'] * 100 / top, 1)}
             for d, v in days.items()]
    axis = [{'value': t, 'at': round(t * 100 / top, 1)} for t in ticks]

    # ---- the people active in the period: funnel, goals, ticked items, the table
    scope = {uid: by_user[uid] for uid in active if uid in by_user}
    reached = {p.slug: 0 for p in pages}
    gaps: dict[str, list[float]] = {p.slug: [] for p in pages}
    people = []
    for uid, rows in scope.items():
        done = [p for p in pages if f'done.{p.slug}' in rows]
        for p in done:
            reached[p.slug] += 1
        before = first_seen[uid]
        for p in pages:
            row = rows.get(f'done.{p.slug}')
            if row is None:
                continue
            moment = _aware(row.created_at)
            if (moment - before).total_seconds() >= 0:
                gaps[p.slug].append((moment - before).total_seconds())
            before = max(before, moment)
        last = max(_aware(r.updated_at) for r in rows.values())
        techs = sum(1 for k, r in rows.items() if k.startswith('tech.') and isinstance(r.value, dict) and r.value.get('viewed'))
        watch = sum(float(r.value.get('seconds', 0)) for k, r in rows.items() if k.startswith('tech.') and isinstance(r.value, dict))
        finished = len(done) == len(pages)
        status = 'finished' if finished else ('stalled' if now - last > STALLED_AFTER else 'progress')
        upcoming = next((p for p in pages if f'done.{p.slug}' not in rows), None)
        email = users[uid].email if uid in users else f'#{uid}'
        people.append(Row(email=email, status=status, status_rank=['finished', 'progress', 'stalled'].index(status), done=len(done),
                          percent=round(len(done) * 100 / len(pages)), techs=techs, watch=watch, watch_text=span(watch) if watch else '—',
                          started=first_seen[uid], last=last, next=upcoming.slug if upcoming else '',
                          next_order=(pages.index(upcoming) + 1) if upcoming else 99,
                          next_nav=upcoming.nav if upcoming else 'Completed'))
    scope_n = len(scope)
    steps_rows = []
    for i, p in enumerate(pages):
        n = reached[p.slug]
        steps_rows.append(Row(order=i + 1, nav=p.nav, slug=p.slug, passed=n, percent=round(n * 100 / scope_n) if scope_n else 0,
                              lost=(reached[pages[i - 1].slug] - n) if i else 0,
                              median=median(gaps[p.slug]) if gaps[p.slug] else None,
                              median_text=span(median(gaps[p.slug])) if gaps[p.slug] else '—'))

    goals: Counter[str] = Counter()
    ticked: Counter[str] = Counter()
    for rows in scope.values():
        goal = rows.get('goal')
        if goal is not None and isinstance(goal.value, str):
            goals[goal.value] += 1
        checks = rows.get('checks')
        if checks is not None and isinstance(checks.value, list):
            ticked.update(checks.value)
    goal_rows = [Row(title=g['title'], n=goals.get(str(g['id']), 0)) for g in course.GOALS]
    labels = _safety_labels()
    check_rows = [Row(label=labels[k][1], group=labels[k][0], n=ticked.get(k, 0)) for k in labels]

    # ---- techniques inside the period (from the log: every report is one event)
    seen: dict[str, dict] = {t['key']: {'viewers': set(), 'completed': set(), 'seconds': 0.0} for t in course.TECHNIQUES}
    for uid, key, value, _ in events:
        if not key.startswith('tech.') or not isinstance(value, dict) or key[5:] not in seen:
            continue
        slot = seen[key[5:]]
        slot['seconds'] += float(value.get('seconds', 0))
        if value.get('plays') or float(value.get('seconds', 0)) >= 5:
            slot['viewers'].add(uid)
        if value.get('completed'):
            slot['completed'].add(uid)
    tech_rows = []
    for t in course.TECHNIQUES:
        s = seen[t['key']]
        viewers = len(s['viewers'])
        completed = len(s['completed'] & s['viewers']) if viewers else 0
        tech_rows.append(Row(number=t['number'], name=t['name'], length=t['time'], viewers=viewers, completed=completed,
                             rate=round(completed * 100 / viewers) if viewers else 0, seconds=s['seconds'], seconds_text=span(s['seconds']),
                             avg=(s['seconds'] / viewers) if viewers else None, avg_text=span(s['seconds'] / viewers) if viewers else '—'))

    # ---- the people table: search, filters, sort (in memory)
    status_options = [(k, sum(1 for p in people if p.status == k)) for k in STATUS_LABELS]
    next_options = [(p.slug, sum(1 for x in people if x.next == p.slug)) for p in pages]
    next_options = [(s, n) for s, n in next_options if n]
    for key, values in people_view.choices:
        if values and key == 'status':
            people = [p for p in people if p.status in values]
        elif values and key == 'next':
            people = [p for p in people if p.next in values]
    for needle in people_view.searches[:1]:
        people = [p for p in people if needle.casefold() in p.email.casefold()]
    people = people_view.sort(people)
    for p in people:
        p.started_at = p.started.astimezone(zone).strftime('%d.%m.%Y %H:%M')
        p.last_at = p.last.astimezone(zone).strftime('%d.%m.%Y %H:%M')
    total = len(people)
    first = 0
    if people_view.after:                      # keyset: the page that follows the last e-mail the browser already has
        known = [p.email for p in people]
        first = known.index(people_view.after[0]) + 1 if people_view.after[0] in known else total
    shown = people[first:first + people_view.take]
    more = encode_cursor([shown[-1].email]) if shown and first + len(shown) < total else None
    return {'kpis': kpis, 'chart': chart, 'chart_axis': axis, 'chart_step': bucket, 'scope_n': scope_n, 'steps': steps_rows, 'techs': tech_rows, 'goals': goal_rows,
            'checks': check_rows, 'people': shown, 'people_total': total, 'people_next': more, 'status_options': status_options,
            'next_options': next_options, 'total_pages': len(pages)}
