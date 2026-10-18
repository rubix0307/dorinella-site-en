"""Audit of changes: one table (`audit_log`) for every tracked object, filled by a trigger in the database itself.

Why a trigger: the row of the log is written by the same statement and inside the same transaction as the change. A rolled-back change leaves no
trace, a committed one always has its record, and nothing that writes to a tracked table (a request, a worker, a script, plain SQL) can forget it.

What a row says: the table and the id of the row, INSERT / UPDATE / DELETE, the whole row before (`old_data`) and after (`new_data`) as JSON, the
transaction, the time, who did it (`actor`) and the database user that ran the statement (`db_user`). An UPDATE that changes nothing writes nothing;
an INSERT has no `old_data`, a DELETE no `new_data`. Nothing shows the log anywhere yet.

Who: the application tells the database the actor of the transaction (`app.actor` = 'user:<id>' or 'system:<job>') — `use_actor` for a request, `use_system`
for a background job — and the trigger copies it. A change made with no actor (a script, a manual query) is logged as `db:<database user>`.

The trigger exists in PostgreSQL only (the migration that created `audit_log`; the tests of it run against the same image as production, see
tests/test_audit.py). SQLite (the other tests, a local database) has the table but no trigger, so there the hooks below do nothing.
To track one more table: give it the trigger (`CREATE TRIGGER audit_<table> AFTER INSERT OR UPDATE OR DELETE ON <table> FOR EACH ROW EXECUTE FUNCTION audit_row('ignored_column', …)`)
in an Alembic migration, and list the table in TRACKED below (a test fails until every table of the models is either tracked or in NOT_TRACKED with its reason).
The arguments of the trigger are columns left out of the logged row: secrets, blobs (a photo would bloat every row) and values that change on every request
(`last_login_at`, `session_version`). An UPDATE that changes only such columns writes nothing. The id of the row (`row_id`) is its primary key, parts joined by ':'
(unambiguous for integer keys); a table without a primary key cannot be tracked (the trigger raises).

What the log keeps of a person: ids, never an e-mail or a name (`admin_users.email` / `name` are ignored columns, so even the first INSERT of an account has neither).
`audit_log` itself refuses UPDATE, DELETE and TRUNCATE unless the transaction says `app.audit_purge = 'on'`; this protects against mistakes, not against someone who can run SQL as the
application's database role. `short_links.target_url` is logged as it is, so a link must not carry a secret; a deploy rollback to older code keeps the triggers.
"""
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

_actor: ContextVar[str | None] = ContextVar('audit_actor', default=None)

# table -> columns the trigger leaves out of the logged row (the arguments of `audit_row(...)`, see the migrations)
TRACKED: dict[str, tuple[str, ...]] = {
    'admin_users': ('email', 'name', 'password_hash', 'last_login_at', 'session_version'),
    'permission_groups': (),
    'group_permissions': (),
    'admin_user_groups': (),
    'short_links': (),
    'bio_buttons': ('photo',),
    'bio_sublinks': ('photo',),
    'bio_page': ('photo',),
}

# table -> why it is not tracked (every table of the models is in one of the two lists)
NOT_TRACKED: dict[str, str] = {
    'page_views': 'event log', 'outbound_clicks': 'event log', 'login_failures': 'event log',
    'visitors': 'derived: one row per browser', 'visitor_accounts': 'derived: browser -> account link',
    'test_attempts': 'event log', 'test_versions': 'derived',
    'backup_runs': 'usage record', 'favorite_links': 'a star on a report row, no risk',
    'course_answers': 'health data: has its own log, never copied here', 'course_answer_log': 'is the history of course answers',
    'audit_log': 'the log itself',
}


def _write_actor(connection, actor: str) -> None:
    """Hand the actor to PostgreSQL for the transaction that is open on `connection` (transaction-local: gone with the commit, never leaks to the
    next user of the pooled connection)."""
    if connection.dialect.name == 'postgresql':
        connection.execute(text("SELECT set_config('app.actor', :a, true)"), {'a': actor})


@event.listens_for(Session, 'after_begin')
def _on_begin(_session, _transaction, connection) -> None:
    """Every new transaction of this request / job starts with its actor."""
    actor = _actor.get()
    if actor is not None:
        _write_actor(connection, actor)


async def use_actor(session: AsyncSession, kind: str, ref) -> None:
    """The rest of this request (its current and later transactions) acts as `kind:ref` (a signed-in person: 'user', their id)."""
    actor = f'{kind}:{ref}'
    _actor.set(actor)
    await session.run_sync(lambda sync: _write_actor(sync.connection(), actor))      # the transaction already open on this session gets it now


def use_system(job: str) -> None:
    """A background job: everything it writes from here on (in this task) is logged as `system:<job>`."""
    _actor.set(f'system:{job}')


async def set_actor_now(session: AsyncSession, kind: str, ref) -> None:
    """The transaction that is open on this session acts as `kind:ref` until it ends; the actor of the request is untouched, so the next transaction
    is the person's again. Flush what is pending first if it must stay the person's doing."""
    actor = f'{kind}:{ref}'
    await session.run_sync(lambda sync: _write_actor(sync.connection(), actor))


async def system_now(session: AsyncSession, job: str) -> None:
    """`use_system` for a request that has a session: also the transaction that may already have begun (the hook only sees new ones)."""
    use_system(job)
    await set_actor_now(session, 'system', job)
