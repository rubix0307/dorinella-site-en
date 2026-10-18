import re

import pytest
from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from app import permissions
from app.main import app


def role_of(accounts, uid):
    return accounts.rows('SELECT role, session_version FROM admin_users WHERE id = :i', i=uid)[0]


def member_groups(accounts, uid):
    return {r['group_id'] for r in accounts.rows('SELECT group_id FROM admin_user_groups WHERE user_id = :i', i=uid)}


@pytest.fixture
def world(accounts):
    """A root, a full admin, a limited admin (test answers only), a bare admin and a plain user, each signed in."""
    ids = {
        'root': accounts.add('root@example.com', 'root'),
        'full': accounts.add('full@example.com', 'admin', groups=[accounts.full]),
        'limited': accounts.add('limited@example.com', 'admin', groups=[accounts.group('Test readers', ['tests.view'])]),
        'bare': accounts.add('bare@example.com', 'admin'),
        'plain': accounts.add('plain@example.com', 'user'),
    }
    clients = {name: accounts.login(f'{name}@example.com') for name in ids}
    return ids, clients


# --- the route matrix --------------------------------------------------------------------------------------------

OPEN = {'admin_login', 'admin_login_google', 'admin_login_google_callback', 'admin_logout', 'admin_profile', 'admin_profile_timezone', 'admin_dashboard',
        'admin_language'}


def all_routes():
    for route in app.routes:
        yield from getattr(getattr(route, 'original_router', None), 'routes', [route])


def admin_routes():
    for route in all_routes():
        if isinstance(route, APIRoute) and route.path.startswith('/admin') and route.name not in OPEN:
            yield route


def fill(path):
    return re.sub(r'\{[^}]+\}', '1', path)


def test_every_admin_route_refuses_an_account_without_rights(world):
    _, clients = world
    checked = 0
    for route in admin_routes():
        for who in ('bare', 'plain'):
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                reply = clients[who].request(method, fill(route.path), data={})
                # a body that fails validation (422) is answered before the handler runs, and says nothing about the data
                assert reply.status_code == 403 or (method == 'POST' and reply.status_code == 422), (who, method, route.path, reply.status_code)
                checked += 1
    assert checked > 25


def test_every_admin_handler_checks_a_right():
    """The matrix above cannot reach a handler whose body fails validation, so the source is checked too."""
    import inspect
    missing = []
    for route in admin_routes():
        source = inspect.getsource(route.endpoint)
        if 'authorize(' not in source:
            missing.append(route.path)
    assert not missing, missing


def test_the_websocket_and_json_endpoints_refuse_too(world):
    _, clients = world
    from starlette.websockets import WebSocketDisconnect
    for who in ('bare', 'plain'):
        reply = clients[who].get('/admin/chart-day', params={'date': '2026-01-01'})
        assert reply.status_code == 403 and reply.json() == {'detail': 'forbidden'}
        with pytest.raises(WebSocketDisconnect), clients[who].websocket_connect('/admin/ws', headers={'origin': 'http://testserver'}) as ws:
            ws.receive_text()
    assert any(isinstance(r, WebSocketRoute) and r.path == '/admin/ws' for r in all_routes())


def test_a_limited_admin_sees_only_what_the_group_gives(world):
    _, clients = world
    c = clients['limited']
    assert c.get('/admin/tests/').status_code == 200
    for path in ('/admin/users/', '/admin/groups/', '/admin/links/', '/admin/pages/', '/admin/course/'):
        assert c.get(path).status_code == 403, path
    nav = c.get('/admin/tests/').text
    assert '/admin/tests' in nav and '/admin/users' not in nav and '/admin/groups' not in nav and '/admin/links' not in nav


def test_the_dashboard_sends_a_limited_admin_to_their_section(world):
    _, clients = world
    reply = clients['limited'].get('/admin/')
    assert reply.status_code == 303 and '/admin/tests' in reply.headers['location']
    assert clients['bare'].get('/admin/').status_code == 303 and '/admin/profile' in clients['bare'].get('/admin/').headers['location']
    assert clients['plain'].get('/admin/').status_code == 303
    assert clients['full'].get('/admin/').status_code == 200


def test_the_profile_is_open_to_everyone_signed_in(world):
    _, clients = world
    for who, client in clients.items():
        assert client.get('/admin/profile/').status_code == 200, who


def test_a_forbidden_page_is_a_page_not_a_redirect(world):
    _, clients = world
    reply = clients['plain'].get('/admin/users/')
    assert reply.status_code == 403 and '<html' in reply.text
    assert clients['plain'].get('/admin/users/1').status_code == 403            # 403, not 404: nothing leaks about ids


def test_a_signed_out_browser_goes_to_login(accounts):
    from fastapi.testclient import TestClient
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get('/admin/users/').status_code == 303
    assert anonymous.get('/admin/groups/').status_code == 303


# --- the registry ------------------------------------------------------------------------------------------------

def test_implication_closure():
    assert 'analytics.view' in permissions.closure({'links.manage'})
    assert {'users.view'} <= permissions.closure({'users.promote'})
    assert {'orders.view', 'orders.refund'} <= permissions.closure({'orders.refund'})
    assert permissions.closure(set()) == set()
    assert permissions.closure({'nope.nope'}) == set()


def test_every_implication_points_at_a_known_permission():
    assert set(permissions.IMPLIES) | set(permissions.IMPLIES.values()) <= permissions.ASSIGNABLE


def test_root_only_codes_are_never_assignable():
    assert not (permissions.ROOT_ONLY & permissions.ASSIGNABLE)
    assert {'groups.manage', 'admins.manage'} <= permissions.ROOT_ONLY


def test_the_registry_is_english_source_text():
    texts = [permissions.SYSTEM_GROUP, *permissions.ROLE_LABELS.values(), *(text for pair in permissions.PERMISSIONS.values() for text in pair)]
    assert all(text.isascii() for text in texts), texts


# --- groups ------------------------------------------------------------------------------------------------------

def test_only_root_manages_groups(world, accounts):
    _, clients = world
    for who in ('full', 'limited', 'bare', 'plain'):
        assert clients[who].get('/admin/groups/').status_code == 403
        assert clients[who].post('/admin/groups/new', data={'name': 'X', 'code': ['tests.view']}).status_code == 403
    assert not accounts.rows("SELECT id FROM permission_groups WHERE name = 'X'")
    assert clients['root'].get('/admin/groups/').status_code == 200


def test_root_creates_edits_and_deletes_a_group(world, accounts):
    _ids, clients = world
    root = clients['root']
    reply = root.post('/admin/groups/new', data={'name': 'Authors', 'description': 'd', 'code': ['links.manage']})
    assert reply.status_code == 303 and reply.headers['location'].endswith('?notice=created')
    gid = accounts.rows("SELECT id FROM permission_groups WHERE name = 'Authors'")[0]['id']
    stored = {r['code'] for r in accounts.rows('SELECT code FROM group_permissions WHERE group_id = :g', g=gid)}
    assert stored == {'links.manage', 'analytics.view'}                          # closure is stored
    assert root.post(f'/admin/groups/{gid}', data={'name': 'Authors 2', 'code': ['tests.view']}).headers['location'].endswith('?notice=saved')
    assert {r['code'] for r in accounts.rows('SELECT code FROM group_permissions WHERE group_id = :g', g=gid)} == {'tests.view'}
    accounts.add('member@example.com', 'admin', groups=[gid])
    assert root.post(f'/admin/groups/{gid}/delete').headers['location'].endswith('?notice=deleted')
    assert not accounts.rows('SELECT * FROM permission_groups WHERE id = :g', g=gid)
    assert not accounts.rows('SELECT * FROM admin_user_groups WHERE group_id = :g', g=gid)
    assert not accounts.rows('SELECT * FROM group_permissions WHERE group_id = :g', g=gid)


@pytest.mark.parametrize('codes', [['nope.nope'], ['groups.manage'], ['admins.manage']])
def test_groups_reject_unknown_and_root_only_codes(world, accounts, codes):
    _, clients = world
    reply = clients['root'].post('/admin/groups/new', data={'name': 'Bad', 'code': codes})
    assert reply.status_code == 422
    assert not accounts.rows("SELECT id FROM permission_groups WHERE name = 'Bad'")


def test_group_name_rules(world, accounts):
    _, clients = world
    root = clients['root']
    assert root.post('/admin/groups/new', data={'name': '  ', 'code': []}).status_code == 422
    assert root.post('/admin/groups/new', data={'name': 'Test readers', 'code': []}).status_code == 422         # taken
    assert root.post('/admin/groups/new', data={'name': 'x' * 400, 'code': []}).status_code == 422


def test_group_errors_are_english_and_russian(world):
    _, clients = world
    root = clients['root']
    english = root.post('/admin/groups/new', data={'name': ' ', 'code': []})
    assert 'Enter a group name' in english.text
    root.cookies.set('admin_lang', 'ru')
    assert 'Укажите название группы' in root.post('/admin/groups/new', data={'name': ' ', 'code': []}).text


def test_the_system_group_is_immutable(world, accounts):
    _, clients = world
    root, gid = clients['root'], accounts.full
    assert root.post(f'/admin/groups/{gid}', data={'name': 'Renamed', 'code': ['tests.view']}).headers['location'].endswith('error=system')
    assert root.post(f'/admin/groups/{gid}/delete').headers['location'].endswith('error=system')
    assert accounts.rows('SELECT name FROM permission_groups WHERE id = :g', g=gid)[0]['name'] == 'Full access'


def test_group_names_are_escaped(world):
    _, clients = world
    clients['root'].post('/admin/groups/new', data={'name': '<script>x</script>', 'description': '<b>b</b>', 'code': []})
    page = clients['root'].get('/admin/groups/').text
    assert '<script>x' not in page and '<b>b</b>' not in page


def test_unknown_group_is_a_redirect_with_a_whitelisted_error(world):
    _, clients = world
    assert clients['root'].get('/admin/groups/999').headers['location'].endswith('error=missing')
    assert clients['root'].post('/admin/groups/999/delete').headers['location'].endswith('error=missing')


def test_the_groups_page_lists_members_and_rights(world):
    _, clients = world
    page = clients['root'].get('/admin/groups/').text
    assert 'Full access' in page and 'Admins: 1' in page and 'All rights' in page


# --- who may touch whom ------------------------------------------------------------------------------------------

def test_a_full_admin_cannot_touch_admins_or_roots(world, accounts):
    ids, clients = world
    c = clients['full']
    for target in ('root', 'limited', 'bare'):
        uid = ids[target]
        assert c.post(f'/admin/users/{uid}', data={'email': 'x@example.com', 'active': '1'}).status_code in (303, 403, 422)
        assert c.post(f'/admin/users/{uid}/toggle').status_code in (303, 403)
        assert c.post(f'/admin/users/{uid}/delete').status_code in (303, 403)
        assert c.post(f'/admin/users/{uid}/access', data={'role': 'user'}).status_code in (303, 403, 422)
    emails = {r['email'] for r in accounts.rows('SELECT email FROM admin_users')}
    assert {'root@example.com', 'limited@example.com', 'bare@example.com'} <= emails
    assert all(r['is_active'] for r in accounts.rows('SELECT is_active FROM admin_users'))
    assert role_of(accounts, ids['limited'])['role'] == 'admin' and role_of(accounts, ids['root'])['role'] == 'root'
    assert member_groups(accounts, ids['limited'])


def test_roots_are_untouchable_even_by_root(world, accounts):
    _ids, clients = world
    other = accounts.add('root2@example.com', 'root')
    for path in (f'/admin/users/{other}/toggle', f'/admin/users/{other}/delete'):
        assert clients['root'].post(path).headers['location'].endswith('error=root')
    assert accounts.rows('SELECT is_active FROM admin_users WHERE id = :i', i=other)[0]['is_active']
    reply = clients['root'].post(f'/admin/users/{other}/access', data={'role': 'user'})
    assert role_of(accounts, other)['role'] == 'root' and reply.status_code in (303, 422)


def test_root_edits_and_demotes_an_admin(world, accounts):
    ids, clients = world
    uid = ids['limited']
    reply = clients['root'].post(f'/admin/users/{uid}/access', data={'role': 'user'})
    assert reply.status_code == 303
    assert role_of(accounts, uid)['role'] == 'user' and not member_groups(accounts, uid)        # demotion clears groups
    assert clients['limited'].get('/admin/tests/').status_code in (303, 403)                    # old cookie is dead / no rights


def test_promotion_is_limited_to_the_actors_own_rights(world, accounts):
    ids, _clients = world
    poor = accounts.group('Little', ['users.promote', 'tests.view'])
    rich = accounts.group('Much', ['course.view'])
    accounts.add('promoter@example.com', 'admin', groups=[poor])
    promoter_client = accounts.login('promoter@example.com')
    target = ids['plain']
    # a group wider than the actor's own rights
    reply = promoter_client.post(f'/admin/users/{target}/access', data={'role': 'admin', 'group': [rich]})
    assert reply.status_code in (303, 422) and role_of(accounts, target)['role'] == 'user'
    assert not member_groups(accounts, target)
    # unknown and system groups
    for bad in (99999, accounts.full):
        promoter_client.post(f'/admin/users/{target}/access', data={'role': 'admin', 'group': [bad]})
        assert role_of(accounts, target)['role'] == 'user' and not member_groups(accounts, target)
    # a subset is fine
    reply = promoter_client.post(f'/admin/users/{target}/access', data={'role': 'admin', 'group': [poor]})
    assert reply.status_code == 303 and role_of(accounts, target)['role'] == 'admin' and member_groups(accounts, target) == {poor}
    # and creating an admin with the same limits
    promoter_client.post('/admin/users/new', data={'email': 'n@example.com', 'active': '1', 'role': 'admin', 'group': [rich]})
    assert not accounts.rows("SELECT id FROM admin_users WHERE email = 'n@example.com' AND role = 'admin'")


def test_without_the_promote_right_no_admin_can_be_made(world, accounts):
    ids, _clients = world
    accounts.add('mgr@example.com', 'admin', groups=[accounts.group('Manager', ['users.manage'])])
    client = accounts.login('mgr@example.com')
    assert client.post(f'/admin/users/{ids["plain"]}/access', data={'role': 'admin', 'group': []}).status_code == 403
    client.post('/admin/users/new', data={'email': 'q@example.com', 'role': 'admin'})
    assert not accounts.rows("SELECT id FROM admin_users WHERE role = 'admin' AND email = 'q@example.com'")


def test_root_can_promote_with_any_group(world, accounts):
    ids, clients = world
    reply = clients['root'].post(f'/admin/users/{ids["plain"]}/access', data={'role': 'admin', 'group': [accounts.full]})
    assert reply.status_code == 303 and role_of(accounts, ids['plain'])['role'] == 'admin'


def test_only_root_hands_out_the_system_group(world, accounts):
    ids, _clients = world
    every = accounts.group('Everything', sorted(permissions.ASSIGNABLE))
    accounts.add('big@example.com', 'admin', groups=[every])
    big = accounts.login('big@example.com')
    reply = big.post(f'/admin/users/{ids["plain"]}/access', data={'role': 'admin', 'group': [accounts.full]})
    assert reply.status_code in (303, 422) and role_of(accounts, ids['plain'])['role'] == 'user'
    assert 'disabled' in big.get(f'/admin/users/{ids["plain"]}').text


# --- sessions ----------------------------------------------------------------------------------------------------

def test_old_cookies_stay_dead_after_a_role_change(world, accounts):
    ids, clients = world
    assert clients['full'].get('/admin/tests/').status_code == 200
    clients['root'].post(f'/admin/users/{ids["full"]}/access', data={'role': 'admin', 'group': [accounts.group('Minimum', ['course.view'])]})
    assert clients['full'].get('/admin/tests/').status_code == 303


def test_disable_then_enable_does_not_revive_a_cookie(world, accounts):
    ids, clients = world
    before = role_of(accounts, ids['full'])['session_version']
    clients['root'].post(f'/admin/users/{ids["full"]}/toggle')
    assert clients['full'].get('/admin/tests/').status_code == 303
    clients['root'].post(f'/admin/users/{ids["full"]}/toggle')
    assert role_of(accounts, ids['full'])['session_version'] > before
    assert clients['full'].get('/admin/tests/').status_code == 303


# --- the command line ----------------------------------------------------------------------------------------------

def test_cli_make_root_and_activate(accounts):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app import cli, db
    from app.core.config import settings

    uid = accounts.add('cli@example.com', 'admin', active=0, groups=[accounts.full])

    async def run(email, root):
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        db.SessionLocal, saved = async_sessionmaker(engine, expire_on_commit=False), db.SessionLocal
        try:
            return await cli._change(email, root=root)
        finally:
            db.SessionLocal = saved
            await engine.dispose()

    assert asyncio.run(run('nobody@example.com', True)) == 1
    assert asyncio.run(run('cli@example.com', False)) == 0
    row = accounts.rows('SELECT role, is_active FROM admin_users WHERE id = :i', i=uid)[0]
    assert row['role'] == 'admin' and row['is_active']
    assert asyncio.run(run('CLI@example.com', True)) == 0
    assert accounts.rows('SELECT role FROM admin_users WHERE id = :i', i=uid)[0]['role'] == 'root'
    assert not member_groups(accounts, uid)
