"""The audit trigger and the migrations on a real PostgreSQL. The trigger exists in PostgreSQL only, so these tests start a throwaway container of the
image the site runs on (AUDIT_PG_IMAGE, default `postgres:18`), apply the real migrations to it and make changes with plain SQL, the way the app's
transactions do (`set_config('app.actor', …, true)` first, as app/audit.py does). They are skipped when docker is not available."""
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app import audit

ROOT = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get('AUDIT_PG_IMAGE', 'postgres:18')
SECRET_COLUMNS = {'email', 'name', 'password_hash', 'last_login_at', 'session_version'}

pytestmark = pytest.mark.skipif(shutil.which('docker') is None, reason='needs docker (the audit trigger is PostgreSQL-only and is tested on the production image)')


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _alembic(env, *args):
    done = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', check=False)
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
    return done


@contextlib.contextmanager
def pg_container(migrate: bool = True):
    """A throwaway container (unique name, random free port, always removed): yields (name, env for alembic), the migrations applied up to head if `migrate`."""
    port = _free_port()
    name = f'audit-test-en-{uuid.uuid4().hex[:10]}'
    started = subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '-p', f'127.0.0.1:{port}:5432', '-e', 'POSTGRES_PASSWORD=x', '-e', 'POSTGRES_DB=t', IMAGE],
                             capture_output=True, text=True, check=False)
    if started.returncode != 0:
        reason = f'cannot start {IMAGE}: {started.stderr.strip()[:200]}'
        if os.environ.get('CI'):                  # in CI a missing image must not turn the PostgreSQL tests into a green skip
            pytest.fail(reason)
        pytest.skip(reason)
    try:
        steady = 0
        for _ in range(90):
            alive = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres', '-d', 't'], capture_output=True, check=False).returncode == 0
            steady = steady + 1 if alive else 0
            if steady == 3:                        # the image restarts once while it initialises: wait until it stays up
                break
            time.sleep(1)
        else:
            pytest.fail(f'{IMAGE} did not become ready')
        env = {**os.environ, 'DATABASE_URL': f'postgresql+asyncpg://postgres:x@127.0.0.1:{port}/t', 'SECRET_KEY': 'test-' + 'k' * 32, 'PYTHONIOENCODING': 'utf-8', 'PYTHONPATH': str(ROOT)}
        if migrate:
            _alembic(env, 'upgrade', 'head')
        yield name, env
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)


def run(name, statement):
    """Run `statement` through psql (one implicit transaction if it holds several statements): the completed process."""
    return subprocess.run(['docker', 'exec', name, 'psql', '-U', 'postgres', '-d', 't', '-v', 'ON_ERROR_STOP=1', '-tA', '-c', statement],
                          capture_output=True, text=True, encoding='utf-8', check=False)


def sql(name, statement):
    done = run(name, statement)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ''


def column(name, query):
    """The first column of every row of `query`."""
    done = run(name, query)
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def log(name, where='true'):
    """The audit_log rows that match `where`, oldest first."""
    query = ("SELECT coalesce(jsonb_agg(to_jsonb(q) ORDER BY id), '[]') FROM (SELECT id, table_name, row_id, op, old_data, new_data, actor, actor_id, db_user, txid "
             f"FROM audit_log WHERE {where}) q")
    return json.loads(sql(name, query))


def as_actor(actor, *statements):
    """The statements in one transaction that acts as `actor`, like a request of the app."""
    return f"SELECT set_config('app.actor', '{actor}', true); " + '; '.join(statements)


def keys(row):
    return set(row['old_data'] or {}) | set(row['new_data'] or {})


@pytest.fixture(scope='module')
def pg():
    with pg_container() as (name, _):
        yield name


@pytest.fixture(scope='module')
def users(pg):
    """An admin and a plain user, made by a plain script (no actor): their ids."""
    run(pg, "INSERT INTO admin_users (email, name, password_hash, role) VALUES ('a@example.test', 'Ann', 'hash-a', 'admin'), ('b@example.test', 'Bob', 'hash-b', 'user')")
    return [int(i) for i in column(pg, 'SELECT id FROM admin_users ORDER BY id')]


# ---------------------------------------------------------------- the trigger

def test_every_tracked_table_has_its_trigger_with_the_ignored_columns(pg):
    found = {}
    query = ("SELECT c.relname || '|' || pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
             "WHERE NOT t.tgisinternal AND t.tgname = 'audit_' || c.relname")
    for line in run(pg, query).stdout.splitlines():
        table, definition = line.split('|', 1)
        found[table] = definition
    assert set(found) == set(audit.TRACKED)
    for table, ignored in audit.TRACKED.items():
        assert 'AFTER INSERT OR DELETE OR UPDATE' in found[table] and 'FOR EACH ROW' in found[table], table      # PostgreSQL's spelling of the events
        arguments = re.search(r'audit_row\((.*)\)', found[table])
        assert arguments and re.findall(r"'([a-z_]+)'", arguments.group(1)) == list(ignored), table


def test_every_table_of_the_database_is_tracked_or_listed_as_not_tracked(pg):
    tables = set(column(pg, "SELECT relname FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace")) - {'alembic_version'}
    assert tables == set(audit.TRACKED) | set(audit.NOT_TRACKED)
    with_trigger = set(column(pg, "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal AND t.tgname = 'audit_' || c.relname"))
    assert not with_trigger & set(audit.NOT_TRACKED)


def test_the_log_has_its_guards_against_update_delete_and_truncate(pg):
    names = set(column(pg, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"))
    assert names == {'audit_log_guard', 'audit_log_guard_truncate'}


def test_an_insert_update_delete_with_a_user_actor(pg, users):
    admin = users[0]
    run(pg, as_actor(f'user:{admin}', "INSERT INTO short_links (slug, target_url, title) VALUES ('ig', 'https://example.test/', 'Instagram')"))
    run(pg, as_actor(f'user:{admin}', "UPDATE short_links SET title = 'IG' WHERE slug = 'ig'"))
    link = sql(pg, "SELECT id FROM short_links WHERE slug = 'ig'")
    run(pg, as_actor(f'user:{admin}', "DELETE FROM short_links WHERE slug = 'ig'"))
    insert, update, delete = log(pg, f"table_name = 'short_links' AND row_id = '{link}'")
    assert (insert['op'], update['op'], delete['op']) == ('INSERT', 'UPDATE', 'DELETE')
    assert insert['old_data'] is None and insert['new_data']['title'] == 'Instagram' and insert['new_data']['slug'] == 'ig'
    assert set(update['old_data']) == set(update['new_data']) and update['old_data']['title'] == 'Instagram' and update['new_data']['title'] == 'IG'
    assert delete['new_data'] is None and delete['old_data']['title'] == 'IG'
    for row in (insert, update, delete):
        assert row['actor'] == f'user:{admin}' and row['actor_id'] == admin and row['db_user'] == 'postgres' and row['txid']


def test_a_system_job_and_a_plain_script_are_told_apart(pg, users):
    run(pg, "INSERT INTO short_links (slug, target_url) VALUES ('job', 'https://example.test/job'), ('script', 'https://example.test/script')")
    run(pg, as_actor('system:backup', "UPDATE short_links SET is_active = false WHERE slug = 'job'"))
    run(pg, "UPDATE short_links SET is_active = false WHERE slug = 'script'")
    job = log(pg, "table_name = 'short_links' AND op = 'UPDATE' AND new_data->>'slug' = 'job'")[0]
    script = log(pg, "table_name = 'short_links' AND op = 'UPDATE' AND new_data->>'slug' = 'script'")[0]
    assert job['actor'] == 'system:backup' and job['actor_id'] is None
    assert script['actor'] == 'db:postgres' and script['actor_id'] is None and script['db_user'] == 'postgres'


def test_the_ignored_columns_of_an_account_are_never_in_the_log(pg, users):
    rows = log(pg, "table_name = 'admin_users'")
    assert len(rows) >= 2
    assert all(not SECRET_COLUMNS & keys(row) for row in rows)
    leaked = sql(pg, "SELECT count(*) FROM audit_log WHERE table_name = 'admin_users' AND (new_data::text LIKE '%example.test%' OR new_data::text LIKE '%hash-%' OR new_data::text LIKE '%Ann%')")
    assert leaked == '0'


def test_a_change_of_ignored_columns_alone_writes_nothing_but_a_real_one_does(pg, users):
    admin, other = users
    before = len(log(pg, "table_name = 'admin_users'"))
    run(pg, as_actor(f'user:{admin}', f"UPDATE admin_users SET email = 'new@example.test', name = 'Anna', password_hash = 'x', last_login_at = now(), session_version = session_version + 1 WHERE id = {other}"))
    assert len(log(pg, "table_name = 'admin_users'")) == before
    run(pg, as_actor(f'user:{admin}', f"UPDATE admin_users SET role = 'admin', session_version = session_version + 1 WHERE id = {other}"))
    change = log(pg, f"table_name = 'admin_users' AND row_id = '{other}' AND op = 'UPDATE'")[-1]
    assert change['old_data']['role'] == 'user' and change['new_data']['role'] == 'admin' and change['actor'] == f'user:{admin}'
    assert not SECRET_COLUMNS & keys(change)


def test_a_no_op_update_writes_nothing(pg, users):
    run(pg, "INSERT INTO short_links (slug, target_url) VALUES ('noop', 'https://example.test/noop')")
    before = len(log(pg))
    run(pg, "UPDATE short_links SET title = title, slug = slug WHERE slug = 'noop'")
    assert len(log(pg)) == before


def test_a_rolled_back_change_leaves_no_trace(pg, users):
    before = len(log(pg))
    done = run(pg, "BEGIN; INSERT INTO short_links (slug, target_url) VALUES ('ghost', 'https://example.test/g'); UPDATE short_links SET title = 'x' WHERE slug = 'noop'; ROLLBACK")
    assert done.returncode == 0, done.stderr
    assert len(log(pg)) == before
    assert sql(pg, "SELECT count(*) FROM short_links WHERE slug = 'ghost'") == '0'
    assert not log(pg, "new_data->>'slug' = 'ghost'")


def test_a_photo_is_not_logged_but_its_hash_is(pg, users):
    run(pg, "INSERT INTO bio_buttons (position, title, image, photo, photo_hash) VALUES (1, 'Shop', 'photo', '\\x0102'::bytea, 'h1')")
    run(pg, "UPDATE bio_buttons SET photo = '\\x0304'::bytea, photo_hash = 'h2'")
    run(pg, "UPDATE bio_buttons SET photo = '\\x0506'::bytea")                         # the photo alone is not a change
    rows = log(pg, "table_name = 'bio_buttons'")
    assert [r['op'] for r in rows] == ['INSERT', 'UPDATE']
    assert all('photo' not in keys(r) for r in rows)
    assert rows[1]['old_data']['photo_hash'] == 'h1' and rows[1]['new_data']['photo_hash'] == 'h2'


def test_tables_without_an_id_column_log_their_primary_key_as_the_row_id(pg, users):
    admin = users[0]
    run(pg, "INSERT INTO permission_groups (name) VALUES ('Editors')")
    group = sql(pg, "SELECT id FROM permission_groups WHERE name = 'Editors'")
    done = run(pg, as_actor(f'user:{admin}', f"INSERT INTO group_permissions (group_id, code) VALUES ({group}, 'links.manage')", f'INSERT INTO admin_user_groups (user_id, group_id) VALUES ({admin}, {group})'))
    assert done.returncode == 0, done.stderr
    assert [r['row_id'] for r in log(pg, "table_name = 'group_permissions'")] == [f'{group}:links.manage']
    assert [r['row_id'] for r in log(pg, "table_name = 'admin_user_groups'")] == [f'{admin}:{group}']
    assert len({r['txid'] for r in log(pg, "table_name IN ('group_permissions', 'admin_user_groups')")}) == 1


def test_orders_and_entitlements_are_logged(pg, users):
    admin, buyer = users
    run(pg, as_actor('system:stripe', f"INSERT INTO orders (number, user_id, product_slug, amount, test_mode, terms_version) VALUES ('N1', {buyer}, 'course', 5000, true, '1')"))
    order = sql(pg, "SELECT id FROM orders WHERE number = 'N1'")
    run(pg, as_actor('system:stripe', f"INSERT INTO entitlements (user_id, product_slug, order_id) VALUES ({buyer}, 'course', {order})"))
    run(pg, as_actor(f'user:{admin}', f"UPDATE orders SET status = 'paid', paid_at = now() WHERE id = {order}"))
    assert [r['op'] for r in log(pg, f"table_name = 'orders' AND row_id = '{order}'")] == ['INSERT', 'UPDATE']
    assert log(pg, "table_name = 'entitlements'")[0]['actor'] == 'system:stripe'


def test_an_untracked_table_writes_nothing(pg, users):
    before = len(log(pg))
    done = run(pg, "INSERT INTO favorite_links (target_url, placement) VALUES ('https://example.test/', 'bio')")
    assert done.returncode == 0, done.stderr
    assert len(log(pg)) == before


def test_the_log_refuses_to_be_edited(pg, users):
    assert log(pg)
    for statement in ('UPDATE audit_log SET actor = $$x$$', 'DELETE FROM audit_log', 'TRUNCATE audit_log'):
        done = run(pg, statement)
        assert done.returncode != 0 and 'append-only' in done.stderr, statement
    assert sql(pg, 'SELECT count(*) FROM audit_log') != '0'


def test_the_purge_flag_lifts_the_guard_for_one_transaction_only(pg, users):
    run(pg, "INSERT INTO short_links (slug, target_url) VALUES ('purge', 'https://example.test/p')")
    row = log(pg, "new_data->>'slug' = 'purge'")[0]['id']
    done = run(pg, f"BEGIN; SET LOCAL app.audit_purge = 'on'; DELETE FROM audit_log WHERE id = {row}; COMMIT")
    assert done.returncode == 0, done.stderr
    assert not log(pg, f'id = {row}')
    assert run(pg, 'DELETE FROM audit_log WHERE id = (SELECT min(id) FROM audit_log)').returncode != 0         # the next transaction is guarded again


# ---------------------------------------------------------------- the migrations

def test_there_is_one_migration_head():
    env = {**os.environ, 'DATABASE_URL': 'postgresql+asyncpg://x:x@127.0.0.1:1/t', 'SECRET_KEY': 'test-' + 'k' * 32}
    done = _alembic(env, 'heads')                                                    # reads the scripts only, never connects
    assert len(re.findall(r'\(head\)', done.stdout)) == 1, done.stdout


def test_upgrade_downgrade_upgrade_and_no_drift_between_the_models_and_the_migrations():
    with pg_container(migrate=False) as (name, env):
        _alembic(env, 'upgrade', 'head')
        assert sql(name, "SELECT count(*) FROM pg_proc WHERE proname = 'audit_row'") == '1'
        _alembic(env, 'check')                                                       # exits non-zero when the models need a migration
        done = run(name, as_actor('user:1', "INSERT INTO short_links (slug, target_url) VALUES ('x', 'https://example.test/')"))
        assert done.returncode == 0, done.stderr
        assert sql(name, 'SELECT count(*) FROM audit_log') == '1'
        _alembic(env, 'downgrade', 'base')                                           # also with a row in the append-only log
        assert sql(name, "SELECT count(*) FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace AND relname <> 'alembic_version'") == '0'
        assert sql(name, "SELECT count(*) FROM pg_proc WHERE proname IN ('audit_row', 'audit_log_guard')") == '0'
        _alembic(env, 'upgrade', 'head')
        _alembic(env, 'check')
        triggers = sql(name, "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'audit\\_%'")
        assert triggers == str(len(audit.TRACKED) + 2)                               # + the guard and its TRUNCATE twin
