"""Test settings: must run before `app` is imported (settings are read at import time)."""
import dataclasses
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine

DB_FILE = Path(tempfile.mkdtemp()) / 'test.sqlite3'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{DB_FILE.as_posix()}'
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['VISITOR_KEYS'] = 'test-visitor-key-1,test-visitor-key-old'


@pytest.fixture(scope='session')
def sync_db():
    """Sync engine on the same SQLite file: creates the schema and lets tests read rows back."""
    from app import models  # noqa: F401 — registers tables
    from app.db import Base, sqlite_foreign_keys

    engine = create_engine(f'sqlite:///{DB_FILE.as_posix()}')
    sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


class Accounts:
    """Admin accounts, groups and signed-in browsers for the permission tests."""

    def __init__(self, engine):
        self.engine = engine

    def reset(self):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            for table in ('short_links', 'admin_user_groups', 'group_permissions', 'permission_groups', 'admin_users', 'login_failures'):
                conn.execute(text(f'DELETE FROM {table}'))
        self.full = self.group('Full access', system=True)
        return self

    def exec(self, sql, **params):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text(sql), params)

    def rows(self, sql, **params):
        from sqlalchemy import text
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), params)]

    def group(self, name, codes=(), system=False):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO permission_groups (name, description, is_system) VALUES (:n, '', :s)"),
                         {'n': name, 's': 1 if system else 0})
            gid = conn.execute(text('SELECT id FROM permission_groups WHERE name = :n'), {'n': name}).scalar()
            for code in codes:
                conn.execute(text('INSERT INTO group_permissions (group_id, code) VALUES (:g, :c)'), {'g': gid, 'c': code})
        return gid

    def add(self, email, role='user', active=1, groups=(), last_login=None, created='2026-01-01 10:00:00'):
        from sqlalchemy import text

        from app import auth
        with self.engine.begin() as conn:
            conn.execute(text('INSERT INTO admin_users (email, password_hash, is_active, role, last_login_at, created_at) '
                              'VALUES (:e, :h, :a, :r, :l, :c)'),
                         {'e': email, 'h': auth.NO_PASSWORD, 'a': active, 'r': role, 'l': last_login, 'c': created})
            uid = conn.execute(text('SELECT id FROM admin_users WHERE email = :e'), {'e': email}).scalar()
            for gid in groups:
                conn.execute(text('INSERT INTO admin_user_groups (user_id, group_id) VALUES (:u, :g)'), {'u': uid, 'g': gid})
        return uid

    def login(self, email):
        from fastapi.testclient import TestClient

        from app.main import app
        client = TestClient(app, follow_redirects=False, headers={'origin': 'http://testserver'})
        from tests.helpers import sign_in
        return sign_in(client, email)


@pytest.fixture
def accounts(sync_db):
    return Accounts(sync_db).reset()


@pytest.fixture
def hidden_catalog():
    """Marks a test that wants the real catalog, with the products that are not ready yet hidden."""


@pytest.fixture(autouse=True)
def _show_hidden_products(request, monkeypatch):
    """The unfinished products are hidden on the real site; the older tests of their pages see them as public."""
    if 'hidden_catalog' in request.fixturenames:
        return
    from app import catalog
    for slug, product in list(catalog.PRODUCTS.items()):
        if not product.public:
            monkeypatch.setitem(catalog.PRODUCTS, slug, dataclasses.replace(product, public=True))
