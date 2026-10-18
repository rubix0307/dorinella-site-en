"""The extra blocks of the analytics dashboard: where visitors come from, the most read pages, and the funnels (guides, tests, the course).

A funnel step is the number of different people who did that thing in the period. The steps are counted on their own (a person who opened a test
page on Monday and finished one on Tuesday counts in both), so a step can be larger than the one before it when the visits and the results are recorded
by different means; the point is where the numbers drop.
"""
from dataclasses import dataclass

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import catalog
from .analytics import visitor_key
from .course import PUBLIC_PAGES
from .course_progress import COURSE
from .models import CourseAnswerLog, PageView, TestAttempt
from .stats import DateRange

DIRECT = 'Direct or unknown'    # no referrer and no utm_source: typed address, bookmark, an app that hides it


@dataclass(frozen=True)
class SourceRow:
    name: str           # a host or a utm_source; DIRECT when there is none
    views: int
    visitors: int
    share: float        # of all page views of the period, 0..1


async def sources(session: AsyncSession, rng: DateRange, limit: int = 8) -> list[SourceRow]:
    """Where page views came from: the utm_source of the link when it has one, else the site that sent the visitor."""
    since, until = rng.utc_bounds
    where = (PageView.created_at >= since, PageView.created_at < until)
    name = func.coalesce(func.nullif(PageView.utm_source, ''), PageView.referrer_host, literal(DIRECT)).label('name')
    rows = (await session.execute(
        select(name, func.count().label('views'), func.count(visitor_key(PageView).distinct()))
        .where(*where).group_by(name).order_by(func.count().desc(), name).limit(limit))).all()
    total = await session.scalar(select(func.count()).where(*where)) or 0
    return [SourceRow(n, v, u, v / total if total else 0.0) for n, v, u in rows]


@dataclass(frozen=True)
class Step:
    label: str
    people: int
    share: float        # of the first step, 0..1


@dataclass(frozen=True)
class Funnel:
    title: str
    steps: list[Step]


def _steps(labels: list[str], counts: list[int]) -> list[Step]:
    top = counts[0] if counts else 0
    return [Step(label, n, n / top if top else 0.0) for label, n in zip(labels, counts, strict=True)]


async def _visitors(session: AsyncSession, rng: DateRange, *conditions) -> int:
    since, until = rng.utc_bounds
    return await session.scalar(
        select(func.count(visitor_key(PageView).distinct()))
        .where(PageView.created_at >= since, PageView.created_at < until, *conditions)) or 0


async def funnels(session: AsyncSession, rng: DateRange) -> list[Funnel]:
    since, until = rng.utc_bounds
    everyone = await _visitors(session, rng)

    guide_products = [f'/products/{p.slug}/' for p in catalog.PRODUCTS.values() if p.type == catalog.ProductType.GUIDE]
    course_products = [f'/products/{p.slug}/' for p in catalog.PRODUCTS.values() if p.type == catalog.ProductType.COURSE]
    guides = _steps(['Visitors', 'Opened a guide', 'Looked at a guide product page'], [
        everyone,
        await _visitors(session, rng, PageView.path.like('/guides/_%')),
        await _visitors(session, rng, PageView.path.in_(guide_products)) if guide_products else 0,
    ])

    finished_tests = {vid or attempt for attempt, vid in (await session.execute(
        select(TestAttempt.id, TestAttempt.visitor_id).where(TestAttempt.created_at >= since, TestAttempt.created_at < until))).all()}
    tests = _steps(['Visitors', 'Opened a test', 'Finished a test'], [
        everyone,
        await _visitors(session, rng, PageView.path.like('/tests/_%')),
        len(finished_tests),
    ])

    in_course = (CourseAnswerLog.course == COURSE, CourseAnswerLog.at >= since, CourseAnswerLog.at < until)
    started = await session.scalar(select(func.count(CourseAnswerLog.user_id.distinct())).where(*in_course)) or 0
    done = await session.scalar(select(func.count(CourseAnswerLog.user_id.distinct()))
                                .where(*in_course, CourseAnswerLog.key == f'done.{PUBLIC_PAGES[-1].slug}')) or 0
    course = _steps(['Looked at the course page', 'Started the course', 'Finished the course'], [
        await _visitors(session, rng, PageView.path.in_(course_products)) if course_products else 0,
        started,
        done,
    ])
    return [Funnel('Guides', guides), Funnel('Tests', tests), Funnel('Course', course)]
