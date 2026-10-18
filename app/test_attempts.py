"""Saved test attempts: every completed test, with every answer, under the visitor's id (and, once they sign
in, their account — see visitors.link_to_user).

A test's text can change (a typo fixed, a question rewritten), so each attempt names the version of the test
it answered: a hash of the test's content. The first attempt on a version stores a snapshot of that content
in `test_versions`, so old answers can always be read against the questions people actually saw.

Saving never breaks the result page: a broken database is logged and the person still gets their result.
"""
import dataclasses
import hashlib
import json
import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import db, live
from .analytics import device_of, request_visitor
from .learn import SelfTest
from .models import TestAttempt, TestVersion
from .visitors import ensure_row, uuid7

log = logging.getLogger(__name__)

REPEAT_WINDOW = 10          # seconds: the same answers again (a double submit) are one attempt
HOURLY_LIMIT = 30           # attempts per visitor (or IP, without an id) per hour; the rest aren't saved
_recent: dict[tuple, float] = {}
_hourly: dict[str, deque] = defaultdict(deque)
_versions_saved: set[tuple[str, str]] = set()


def snapshot(test: SelfTest) -> dict:
    return dataclasses.asdict(test)


def version(test: SelfTest) -> str:
    """Content hash of the test: changes whenever a question, an option, its points or a result text does."""
    raw = json.dumps(snapshot(test), ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def source_of(request: Request) -> str:
    if request.url.path.startswith('/api/'):
        return 'api'
    return 'form' if request.headers.get('sec-fetch-mode') == 'navigate' else 'js'


def _allowed(who: str, slug: str, answers: list[int]) -> bool:
    now = time.monotonic()
    key = (who, slug, tuple(answers))
    if now - _recent.get(key, -REPEAT_WINDOW) < REPEAT_WINDOW:
        return False
    hour = _hourly[who]
    while hour and now - hour[0] > 3600:
        hour.popleft()
    if len(hour) >= HOURLY_LIMIT:
        return False
    hour.append(now)
    _recent[key] = now
    if len(_recent) > 10_000:       # forget the old double-submit keys
        for k in [k for k, t in _recent.items() if now - t >= REPEAT_WINDOW]:
            del _recent[k]
    return True


async def record_attempt(request: Request, test: SelfTest, answers: list[int],
                         sessions: async_sessionmaker | None = None) -> None:
    """Save a valid, scored attempt. Never raises."""
    sessions = sessions or db.SessionLocal
    device = device_of(request.headers.get('user-agent', ''))
    if sessions is None or device == 'bot':
        return
    visitor = request_visitor(request)
    # an id handed out by this very request belongs to a client that sent no cookie: it would never be seen again
    visitor_id = visitor.id if visitor is not None and not visitor.new else None
    who = visitor_id.hex if visitor_id else f'ip:{request.client.host if request.client else ""}'
    if not _allowed(who, test.slug, answers):
        return
    if test.scales:
        score, band_key = None, None
        scales = {r.scale.key: r.score for r in test.scale_results(answers)}
    else:
        score = test.score(answers)
        band_key, scales = test.band_for(score).key, None
    try:
        ver = version(test)
        async with sessions() as session:
            if (test.slug, ver) not in _versions_saved and await session.get(TestVersion, (test.slug, ver)) is None:
                session.add(TestVersion(slug=test.slug, version=ver, snapshot=snapshot(test)))
                await session.flush()
            if visitor_id is not None:
                await ensure_row(session, visitor_id)       # the attempt's foreign key
            session.add(TestAttempt(id=uuid7(), visitor_id=visitor_id, test_slug=test.slug, test_version=ver,
                                    answers=list(answers), score=score, band_key=band_key, scales=scales,
                                    source=source_of(request), device=device))
            await session.commit()
        _versions_saved.add((test.slug, ver))
        live.published('attempt')           # the admin «Тести» pages refresh themselves
    except Exception:
        log.exception('could not save the test attempt')
