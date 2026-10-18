"""The actor hooks of the audit log (app/audit.py). The trigger itself exists in PostgreSQL only; on SQLite (these tests) the hooks must do nothing and never fail.
The trigger tests need the initial migration, which is written at the end of the port (see docs/modules/Users-and-access.md)."""
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import audit
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean_actor():
    token = audit._actor.set(None)
    yield
    audit._actor.reset(token)


def run(job):
    """`job(session)` on a fresh session of the test database."""
    async def go():
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await job(session)
        finally:
            await engine.dispose()
    return asyncio.run(go())


def test_use_actor_remembers_the_actor_for_the_request(sync_db):
    async def job(session):
        await audit.use_actor(session, 'user', 7)
        await session.execute(text('SELECT 1'))                       # SQLite: nothing is written, nothing fails
        return audit._actor.get()

    assert run(job) == 'user:7'


def test_use_system_and_system_now_name_the_job(sync_db):
    async def job(session):
        audit.use_system('backup')
        first = audit._actor.get()
        await audit.system_now(session, 'sign-in')
        return first, audit._actor.get()

    assert run(job) == ('system:backup', 'system:sign-in')


def test_set_actor_now_leaves_the_actor_of_the_request_alone(sync_db):
    async def job(session):
        audit.use_system('first')
        await audit.set_actor_now(session, 'system', 'second')
        return audit._actor.get()

    assert run(job) == 'system:first'


def test_the_actor_is_written_only_on_postgresql():
    calls = []

    class Connection:
        def __init__(self, name):
            self.dialect = type('D', (), {'name': name})()

        def execute(self, statement, params):
            calls.append((str(statement), params))

    audit._write_actor(Connection('sqlite'), 'user:1')
    assert calls == []
    audit._write_actor(Connection('postgresql'), 'user:1')
    assert len(calls) == 1 and 'app.actor' in calls[0][0] and calls[0][1] == {'a': 'user:1'}
