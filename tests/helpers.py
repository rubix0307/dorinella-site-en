"""Helpers shared by the test modules (conftest.py itself must not be imported: it sets up the test database)."""
import os
from types import SimpleNamespace

from sqlalchemy import create_engine, text


def sign_in(client, email):
    """A browser that is signed in as this account. The way in is a Google sign-in; the tests start after it, with the
    session cookie the callback would have set (the sign-in itself is in test_google_login.py)."""
    from app import auth
    path = os.environ['DATABASE_URL'].split(':///', 1)[1]
    engine = create_engine(f'sqlite:///{path}')
    with engine.connect() as conn:
        row = conn.execute(text('SELECT id, password_hash, session_version FROM admin_users WHERE lower(email) = :e'),
                           {'e': email.strip().lower()}).first()
    engine.dispose()
    assert row is not None, f'no account {email}'
    value, _ttl = auth.session_cookie(SimpleNamespace(**row._mapping))
    client.cookies.set(auth.SESSION_COOKIE, value)
    return client
