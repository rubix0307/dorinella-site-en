import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.main import app
from tests.helpers import sign_in

BOSS = 'boss@example.com'


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def add(engine, email, active=1, last_login=None, created='2026-01-01 10:00:00', role='user', groups=()):
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO admin_users (email, password_hash, is_active, role, last_login_at, created_at) '
                          'VALUES (:e, :h, :a, :r, :l, :c)'),
                     {'e': email, 'h': auth.NO_PASSWORD, 'a': active, 'r': role, 'l': last_login, 'c': created})
        uid = conn.execute(text('SELECT id FROM admin_users WHERE email = :e'), {'e': email}).scalar()
        for gid in groups:
            conn.execute(text('INSERT INTO admin_user_groups (user_id, group_id) VALUES (:u, :g)'), {'u': uid, 'g': gid})
    return uid


def browser():
    return TestClient(app, follow_redirects=False, headers={'origin': 'http://testserver'})


@pytest.fixture
def admin(accounts, sync_db):
    boss = add(sync_db, BOSS, role='root', last_login='2026-02-01 10:00:00')
    client = browser()
    sign_in(client, BOSS)
    client.boss_id = boss
    return client


def tbody(page):
    return page.split('<tbody>')[-1].split('</tbody>')[0]


def form(email='new@example.com', active='1'):
    data = {'email': email}
    if active:
        data['active'] = active
    return data


def test_everything_needs_a_login(sync_db):
    anonymous = browser()
    for path in ('/admin/users/', '/admin/users/new', '/admin/users/1'):
        assert anonymous.get(path).status_code == 303
    for path in ('/admin/users/new', '/admin/users/1', '/admin/users/1/toggle', '/admin/users/1/delete'):
        assert anonymous.post(path, data=form()).status_code == 303
    assert anonymous.post('/admin/users/new', data=form()).status_code == 303 and not rows(sync_db, "SELECT id FROM admin_users WHERE email = 'new@example.com'")


def test_the_list_shows_the_accounts_and_the_menu_links_to_it(admin, sync_db):
    add(sync_db, 'off@example.com', active=0)
    page = admin.get('/admin/users/')
    assert page.status_code == 200
    assert BOSS in page.text and 'off@example.com' in page.text and 'Switched off' in page.text and 'You</span>' in page.text
    assert '/admin/users' in admin.get('/admin/').text


def test_the_own_row_has_no_switch_off_or_delete(admin):
    page = admin.get('/admin/users/').text
    assert f'/admin/users/{admin.boss_id}/delete' not in page and f'/admin/users/{admin.boss_id}/toggle' not in page


def test_search_filter_and_sort(admin, sync_db):
    add(sync_db, 'anna@example.com', last_login='2026-03-01 10:00:00', created='2026-01-05 10:00:00')
    add(sync_db, 'bob@example.com', created='2026-01-03 10:00:00')
    add(sync_db, 'carl@example.com', active=0, created='2026-01-04 10:00:00')
    page = admin.get('/admin/users/', params={'users_q': 'anna'}).text
    assert 'anna@example.com' in page and 'bob@example.com' not in page
    page = admin.get('/admin/users/', params={'users_status': 'off'}).text
    assert 'carl@example.com' in page and 'anna@example.com' not in page
    page = admin.get('/admin/users/', params={'users_status': 'never'}).text
    assert 'bob@example.com' in page and 'carl@example.com' not in page
    page = tbody(admin.get('/admin/users/', params={'users_sort': 'email'}).text)
    assert page.index('anna@') < page.index('bob@') < page.index('boss@') < page.index('carl@')
    page = tbody(admin.get('/admin/users/', params={'users_sort': '-email'}).text)
    assert page.index('carl@') < page.index('boss@') < page.index('bob@') < page.index('anna@')
    page = admin.get('/admin/users/', params={'users_q': 'zzz'}).text
    assert 'Nothing found' in page


def test_wildcards_in_the_search_are_plain_characters(admin, sync_db):
    add(sync_db, 'a_b@example.com')
    add(sync_db, 'axb@example.com')
    page = admin.get('/admin/users/', params={'users_q': 'a_b'}).text
    assert 'a_b@example.com' in page and 'axb@example.com' not in page
    assert 'Nothing found' in admin.get('/admin/users/', params={'users_q': '%'}).text      # not "match everything"


def test_the_list_is_paged(admin, sync_db):
    for number in range(35):
        add(sync_db, f'user{number:02d}@example.com', created=f'2026-01-01 10:{number:02d}:00')
    first = admin.get('/admin/users/', params={'users_sort': 'email'})
    assert 'Show more' in first.text and 'user34@' not in first.text
    import re
    href = re.search(r'href="(\?[^"]*)"[^>]*>Show more', first.text).group(1).replace('&amp;', '&')
    second = admin.get('/admin/users/' + href)
    assert 'user34@' in second.text and 'Show more' not in second.text


def test_the_email_is_escaped(admin, sync_db):
    add(sync_db, '"><script>x</script>@example.com')
    assert '<script>x' not in admin.get('/admin/users/').text


def test_create_an_account(admin, sync_db):
    assert admin.get('/admin/users/new').status_code == 200
    reply = admin.post('/admin/users/new', data=form(email='  New@Example.COM '))
    assert reply.status_code == 303 and reply.headers['location'].endswith('?notice=created')
    row = rows(sync_db, "SELECT * FROM admin_users WHERE email = 'new@example.com'")[0]
    assert row['is_active'] and row['password_hash'] == auth.NO_PASSWORD      # no password: the way in is Google
    other = sign_in(browser(), 'new@example.com')          # the new person can come in
    assert other.get('/admin/profile/').status_code == 200 and other.get('/admin/users/').status_code == 403      # a plain user has no rights


def test_create_without_active_makes_it_inactive(admin, sync_db):
    admin.post('/admin/users/new', data=form(active=''))
    assert not rows(sync_db, "SELECT is_active FROM admin_users WHERE email = 'new@example.com'")[0]['is_active']


@pytest.mark.parametrize('data, field', [
    (form(email='nope'), 'email'),
    (form(email=''), 'email'),
    (form(email='a' * 250 + '@example.com'), 'email'),
])
def test_create_refuses_bad_input(admin, sync_db, data, field):
    reply = admin.post('/admin/users/new', data=data)
    assert reply.status_code == 422 and f'name="{field}"' in reply.text and 'aria-invalid="true"' in reply.text
    assert rows(sync_db, 'SELECT count(*) AS n FROM admin_users')[0]['n'] == 1


def test_create_refuses_a_duplicate_in_any_case(admin):
    reply = admin.post('/admin/users/new', data=form(email=BOSS.upper()))
    assert reply.status_code == 422 and 'already exists' in reply.text


def test_edit_the_email(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    reply = admin.post(f'/admin/users/{other}', data={'email': 'Edited@example.com', 'active': '1'})
    assert reply.status_code == 303 and reply.headers['location'].endswith('?notice=saved')
    row = rows(sync_db, 'SELECT * FROM admin_users WHERE id = :i', i=other)[0]
    assert row['email'] == 'edited@example.com'


def test_edit_refuses_an_address_that_is_taken(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    reply = admin.post(f'/admin/users/{other}', data={'email': BOSS, 'active': '1'})
    assert reply.status_code == 422 and 'already exists' in reply.text
    assert rows(sync_db, 'SELECT email FROM admin_users WHERE id = :i', i=other)[0]['email'] == 'edit@example.com'


def test_switch_off_and_on(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    theirs = sign_in(browser(), 'edit@example.com')
    reply = admin.post(f'/admin/users/{other}/toggle')
    assert reply.headers['location'].endswith('?notice=disabled')
    assert not rows(sync_db, 'SELECT is_active FROM admin_users WHERE id = :i', i=other)[0]['is_active']
    assert theirs.get('/admin/profile/').status_code == 303
    assert admin.post(f'/admin/users/{other}/toggle').headers['location'].endswith('?notice=enabled')
    assert rows(sync_db, 'SELECT is_active FROM admin_users WHERE id = :i', i=other)[0]['is_active']


def test_you_cannot_switch_off_or_delete_yourself(admin, sync_db):
    add(sync_db, 'second@example.com')          # not the last one: it is the "self" rule that refuses
    assert admin.post(f'/admin/users/{admin.boss_id}/toggle').headers['location'].endswith('?error=self')
    assert admin.post(f'/admin/users/{admin.boss_id}/delete').headers['location'].endswith('?error=self')
    reply = admin.post(f'/admin/users/{admin.boss_id}', data=form(email=BOSS, active=''))
    assert reply.status_code == 303
    assert rows(sync_db, 'SELECT is_active FROM admin_users WHERE id = :i', i=admin.boss_id)[0]['is_active']



def test_delete_keeps_the_links_without_an_author(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    with sync_db.begin() as conn:
        conn.execute(text("INSERT INTO short_links (slug, target_url, title, created_by) VALUES ('abc', 'https://example.com', '', :i)"), {'i': other})
    page = admin.get(f'/admin/users/{other}').text
    assert 'short links: 1' in page
    reply = admin.post(f'/admin/users/{other}/delete')
    assert reply.headers['location'].endswith('?notice=deleted')
    assert not rows(sync_db, 'SELECT id FROM admin_users WHERE id = :i', i=other)
    assert rows(sync_db, "SELECT created_by FROM short_links WHERE slug = 'abc'")[0]['created_by'] is None
    assert admin.get(f'/admin/users/{other}').status_code == 404


def test_unknown_account_is_a_404_with_the_list_chrome(admin):
    for call in (admin.get('/admin/users/999'),):
        assert call.status_code == 404 and 'User not found' in call.text
    for path in ('/admin/users/999', '/admin/users/999/toggle', '/admin/users/999/delete'):
        assert admin.post(path, data=form()).status_code == 404


def test_foreign_or_missing_origin_is_refused(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    foreign = TestClient(app, follow_redirects=False, headers={'origin': 'https://evil.example'})
    foreign.cookies.update(admin.cookies)
    for path in ('/admin/users/new', f'/admin/users/{other}', f'/admin/users/{other}/toggle', f'/admin/users/{other}/delete'):
        assert foreign.post(path, data=form()).status_code in (303, 403), path
    assert rows(sync_db, 'SELECT email, is_active FROM admin_users WHERE id = :i', i=other) == [{'email': 'edit@example.com', 'is_active': 1}]
    assert not rows(sync_db, "SELECT id FROM admin_users WHERE email = 'new@example.com'")
    bare = TestClient(app, follow_redirects=False)
    bare.cookies.update(admin.cookies)
    assert bare.post(f'/admin/users/{other}/delete').status_code in (303, 403)
    assert rows(sync_db, 'SELECT id FROM admin_users WHERE id = :i', i=other)


def test_mutating_urls_do_not_answer_to_get(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    assert admin.get(f'/admin/users/{other}/toggle').status_code == 405
    assert admin.get(f'/admin/users/{other}/delete').status_code == 405


def test_notices_are_a_whitelist(admin):
    page = admin.get('/admin/users/', params={'notice': '<b>hi</b>', 'error': '<i>x</i>'}).text
    assert '<b>hi</b>' not in page and '<i>x</i>' not in page and 'hi' not in page.split('<main')[1].split('</header>')[1][:300]
    assert 'User created.' in admin.get('/admin/users/', params={'notice': 'created'}).text


def test_nobody_edits_themselves_here(admin, sync_db):
    assert admin.get(f'/admin/users/{admin.boss_id}').status_code == 303
    assert admin.get(f'/admin/users/{admin.boss_id}').headers['location'].endswith('/admin/profile/')
    before = rows(sync_db, 'SELECT * FROM admin_users WHERE id = :i', i=admin.boss_id)
    for path in (f'/admin/users/{admin.boss_id}', f'/admin/users/{admin.boss_id}/access'):
        reply = admin.post(path, data=form(email='else@example.com') | {'role': 'user'})
        assert reply.status_code in (303, 422), path
    assert rows(sync_db, 'SELECT * FROM admin_users WHERE id = :i', i=admin.boss_id) == before


def test_the_admin_speaks_english_by_default_and_russian_on_request(admin, sync_db):
    add(sync_db, 'edit@example.com')
    english = admin.get('/admin/users/').text
    assert 'New user' in english and 'Never signed in' in english and 'Новый пользователь' not in english
    admin.cookies.set('admin_lang', 'ru')
    russian = admin.get('/admin/users/').text
    assert 'Новый пользователь' in russian and 'Ещё не входил' in russian and 'New user' not in russian
    assert 'Свою учётную запись' in russian
    # the filter popovers are imported macros: they must see the language too
    assert 'Выбрать все' in russian and 'Select all' not in russian and 'Close the menu' not in russian


def test_a_refusal_is_shown_in_the_language_of_the_admin(admin):
    reply = admin.post('/admin/users/new', data=form(email=BOSS))
    assert reply.status_code == 422 and 'A user with this e-mail already exists.' in reply.text
    admin.cookies.set('admin_lang', 'ru')
    assert 'Пользователь с такой почтой уже есть.' in admin.post('/admin/users/new', data=form(email=BOSS)).text


def test_a_refusal_with_a_name_in_it_fills_the_placeholder(admin, sync_db, accounts):
    from app import permissions
    # a group the actor lacks rights for: here a limited promoter holding less than the group
    little = accounts.group('Little', ['users.promote'])
    big = accounts.group('Big', sorted(permissions.ASSIGNABLE))
    accounts.add('promoter@example.com', 'admin', groups=[little])
    promoter = accounts.login('promoter@example.com')
    reply = promoter.post('/admin/users/new', data={'email': 'z@example.com', 'role': 'admin', 'group': [big]})
    assert reply.status_code in (403, 422)
    target = add(sync_db, 'target@example.com')
    reply = promoter.post(f'/admin/users/{target}/access', data={'role': 'admin', 'group': [big]})
    assert reply.status_code == 422 and 'The group &#34;Big&#34; gives rights you do not have.' in reply.text
    promoter.cookies.set('admin_lang', 'ru')
    reply = promoter.post(f'/admin/users/{target}/access', data={'role': 'admin', 'group': [big]})
    assert 'Группа «Big» даёт права, которых у вас нет.' in reply.text


def test_a_plain_user_can_be_switched_off_by_a_manager(admin, sync_db, accounts):
    accounts.add('mgr@example.com', 'admin', groups=[accounts.group('Manager', ['users.manage'])])
    manager = accounts.login('mgr@example.com')
    victim = add(sync_db, 'victim@example.com')
    assert manager.post(f'/admin/users/{victim}/toggle').headers['location'].endswith('?notice=disabled')
    assert manager.post(f'/admin/users/{victim}/toggle').headers['location'].endswith('?notice=enabled')


def test_the_form_has_no_leftovers_of_removed_features(admin, sync_db):
    other = add(sync_db, 'edit@example.com')
    page = admin.get(f'/admin/users/{other}').text
    assert 'Telegram' not in page and 'meeting' not in page.lower()
    assert 'short links: 0' in page
