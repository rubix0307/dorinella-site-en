"""Async database access (SQLAlchemy 2 + asyncpg)."""
from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy import MetaData, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .core.config import settings

# Stable constraint names, so Alembic migrations stay deterministic.
NAMING = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


engine: AsyncEngine | None = (
    create_async_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False) if engine else None


def sqlite_foreign_keys(target) -> None:
    """SQLite ignores foreign keys unless asked per connection; the tests run on it, so ON DELETE must work there too."""
    @event.listens_for(target, 'connect')
    def _enable(connection, _record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


if engine is not None and engine.dialect.name == 'sqlite':
    sqlite_foreign_keys(engine.sync_engine)


def session_factory() -> async_sessionmaker[AsyncSession]:
    """The session maker for code outside a request (workers, CLI); a clear error when no database is configured."""
    if SessionLocal is None:
        raise RuntimeError('DATABASE_URL is not configured')
    return SessionLocal


def new_session() -> AsyncSession:
    """One session for code outside a request, used as `async with db.new_session() as session`."""
    return session_factory()()


def get_engine() -> AsyncEngine:
    """The engine for code that cannot work without a database."""
    if engine is None:
        raise RuntimeError('DATABASE_URL is not configured')
    return engine


async def get_optional_session() -> AsyncIterator[AsyncSession | None]:
    """FastAPI dependency for the public pages that still render without a database: yields None then."""
    if SessionLocal is None:
        yield None
        return
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency; answers 503 when no database is configured."""
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail='Database is not configured', headers={'Cache-Control': 'no-store'})
    async with SessionLocal() as session:
        yield session


async def ping() -> bool:
    if engine is None:
        return False
    async with engine.connect() as conn:
        await conn.execute(text('SELECT 1'))
    return True
