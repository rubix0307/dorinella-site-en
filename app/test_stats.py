"""Aggregations of the saved test attempts for the admin «Тести» page.

Attempts are few (thousands, not millions), so the answers are read once and counted in Python: the JSON of
answers is the same on SQLite and Postgres that way.
"""
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .learn import GUIDES, SELF_TESTS, SelfTest
from .models import TestAttempt
from .stats import DateRange
from .test_attempts import version


def title_of(test: SelfTest) -> str:
    """A guide's checklist is named after its guide (the test's own title is just «Перевірте себе»)."""
    guide = GUIDES.get(test.guide_slug) if test.guide_slug else None
    return guide.title if guide else test.title


@dataclass
class Share:
    label: str
    count: int
    total: int

    @property
    def share(self) -> float:
        return self.count / self.total if self.total else 0.0


@dataclass
class TestSummary:
    test: SelfTest
    attempts: int = 0
    people: int = 0
    bands: list[Share] = field(default_factory=list)
    scales: list[tuple[str, float, int]] = field(default_factory=list)    # (title, mean, reference)

    @property
    def title(self) -> str:
        return title_of(self.test)


def _in(rng: DateRange):
    since, until = rng.utc_bounds
    return TestAttempt.created_at >= since, TestAttempt.created_at < until


def _people(rows) -> int:
    """Distinct visitors; an attempt without an id (no cookie) is a person of its own."""
    return len({r.visitor_id or r.id for r in rows})


async def _rows(session: AsyncSession, rng: DateRange, slug: str | None = None):
    query = select(TestAttempt).where(*_in(rng)).order_by(TestAttempt.created_at.desc())
    if slug:
        query = query.where(TestAttempt.test_slug == slug)
    return list((await session.scalars(query)).all())


def summarize(rows: list[TestAttempt], test: SelfTest) -> TestSummary:
    summary = TestSummary(test, attempts=len(rows), people=_people(rows))
    if test.bands:
        counts = Counter(r.band_key for r in rows)
        summary.bands = [Share(b.title, counts.get(b.key, 0), len(rows)) for b in reversed(test.bands)]
    for scale in test.scales:
        values = [r.scales[scale.key] for r in rows if r.scales and scale.key in r.scales]
        summary.scales.append((scale.title, sum(values) / len(values) if values else 0.0, scale.reference))
    return summary


async def overview(session: AsyncSession, rng: DateRange) -> list[TestSummary]:
    by_test: dict[str, list[TestAttempt]] = {}
    for row in await _rows(session, rng):
        by_test.setdefault(row.test_slug, []).append(row)
    return sorted((summarize(by_test.get(slug, []), test) for slug, test in SELF_TESTS.items()),
                  key=lambda s: -s.attempts)


@dataclass
class QuestionStats:
    number: int
    text: str
    label: str
    options: list[Share]


async def detail(session: AsyncSession, rng: DateRange, test: SelfTest) -> dict:
    """The test's attempts in the period: totals and how each question was answered (current version only —
    an older version may have had other questions under the same numbers)."""
    rows = await _rows(session, rng, test.slug)
    current = version(test)
    now = [r for r in rows if r.test_version == current]
    questions = []
    for n, question in enumerate(test.questions):
        counts = Counter(r.answers[n] for r in now if n < len(r.answers))
        questions.append(QuestionStats(n + 1, question.text, question.label,
                                       [Share(label, counts.get(i, 0), len(now)) for i, (label, _) in enumerate(question.options)]))
    return {'summary': summarize(rows, test), 'questions': questions, 'older': len(rows) - len(now)}
