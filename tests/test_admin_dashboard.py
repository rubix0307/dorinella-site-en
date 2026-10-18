"""The analytics dashboard's own blocks (sources, top pages, test results, funnels), the sidebar, the no-access page and the profile."""
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import dashboard_stats, stats
from app.core.templating import templates
from app.course import PUBLIC_PAGES
from app.learn import SELF_TESTS
from app.main import app

route_exists = templates.env.globals['route_exists']
TEST = next(test for test in SELF_TESTS.values() if test.bands)


def _stamp(minutes_ago=5):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S.%f')


@pytest.fixture
def clean(sync_db):
    tables = ('page_views', 'test_attempts', 'course_answer_log')

    def wipe():
        with sync_db.begin() as conn:
            for table in tables:
                conn.execute(text(f'DELETE FROM {table}'))
    wipe()
    yield sync_db
    wipe()


@pytest.fixture
def root(accounts, clean):
    accounts.add('root@example.com', 'root')
    return accounts.login('root@example.com')


def _view(conn, path, visitor, referrer=None, utm=None):
    conn.execute(text('INSERT INTO page_views (created_at, path, referrer_host, utm_source, visitor_hash, device) VALUES (:t, :p, :r, :u, :v, :d)'),
                 {'t': _stamp(), 'p': path, 'r': referrer, 'u': utm, 'v': visitor, 'd': 'desktop'})


def _card(html, heading_id):
    start = html.index(f'id="{heading_id}"')
    return html[start:html.index('</section>', start)]


def _bars(card):
    """[(label, numbers text)] of a card's bar list."""
    return [(re.sub(r'<[^>]+>|\s+', ' ', name).strip(), re.sub(r'<[^>]+>|\s+', ' ', numbers).strip())
            for name, numbers in re.findall(r'<span class="bars__label">(.*?)</span>?<b>(.*?)</b></span>', card, re.DOTALL)]


def test_sources_prefer_the_utm_source_then_the_referrer_then_direct(root, clean):
    with clean.begin() as conn:
        for visitor in ('a', 'b'):
            _view(conn, '/', visitor, referrer='l.instagram.com', utm='instagram')
        _view(conn, '/', 'c', referrer='www.google.com')
        _view(conn, '/', 'd')
        _view(conn, '/guides/', 'd')
    html = root.get('/admin/').text
    rows = {name.split(' <')[0].strip(): numbers for name, numbers in _bars(_card(html, 'sources-title'))}
    assert rows['instagram'] == '2 views · 2 visitors'
    assert rows['www.google.com'] == '1 views · 1 visitors'
    assert rows['Direct or unknown'] == '2 views · 1 visitors'
    assert 'l.instagram.com' not in _card(html, 'sources-title')             # the tag wins over the host


def test_sources_are_translated(root, clean):
    with clean.begin() as conn:
        _view(conn, '/', 'a')
    root.cookies.set('admin_lang', 'ru')
    card = _card(root.get('/admin/').text, 'sources-title')
    assert 'Прямой заход или неизвестно' in card and '1 просмотров' in card


def test_top_pages_are_named_and_counted(root, clean):
    guide = next(iter(__import__('app.learn', fromlist=['GUIDES']).GUIDES.values()))
    with clean.begin() as conn:
        for i in range(3):
            _view(conn, f'/guides/{guide.slug}/', f'v{i}')
        _view(conn, '/', 'v0')
        _view(conn, '/some/other/page/', 'v1')
    card = _card(root.get('/admin/').text, 'top-pages-title')
    names = [name for name, _ in _bars(card)]
    assert names[0].startswith(f'Guide: {guide.title}') and names[1].startswith('Home page') and names[2].startswith('/some/other/page/')
    assert 'href="/admin/pages/?from=' in card


def test_empty_blocks_say_so(root, clean):
    html = root.get('/admin/').text
    assert 'Nobody finished a test in this period.' in html
    assert _card(html, 'sources-title').count('No views in this period.') == 1


def test_test_results_show_completions_and_the_distribution(root, clean):
    bands = [band.key for band in TEST.bands]
    with clean.begin() as conn:
        for n, band in enumerate([bands[0], bands[0], bands[-1]]):
            conn.execute(text('INSERT INTO test_attempts (id, created_at, test_slug, test_version, answers, score, band_key, source, device) '
                              "VALUES (:i, :t, :s, 'v1', '[]', 0, :b, 'js', 'desktop')"),
                         {'i': uuid.uuid4().hex, 't': _stamp(n + 1), 's': TEST.slug, 'b': band})
    card = _card(root.get('/admin/').text, 'tests-title')
    assert '3 completions · 3 people' in card
    shares = {name.split(' <')[0]: numbers for name, numbers in _bars(card)}
    assert sorted(numbers.split(' · ')[0] for numbers in shares.values())[-2:] == ['1', '2']
    assert f'href="/admin/tests/{TEST.slug}/' in card


def test_funnels_count_people_per_step(root, clean, accounts):
    guide_product = next(slug for slug, product in __import__('app.catalog', fromlist=['PRODUCTS']).PRODUCTS.items() if product.type == 'guide')
    last_page = PUBLIC_PAGES[-1].slug
    user_a, user_b = accounts.add('a@example.com'), accounts.add('b@example.com')
    with clean.begin() as conn:
        for visitor in ('a', 'b', 'c', 'd'):
            _view(conn, '/', visitor)
        _view(conn, '/guides/x/', 'a')
        _view(conn, '/guides/x/', 'a')
        _view(conn, '/guides/y/', 'b')
        _view(conn, f'/products/{guide_product}/', 'a')
        for user, key in ((user_a, 'goal'), (user_a, f'done.{last_page}'), (user_b, 'goal')):
            conn.execute(text("INSERT INTO course_answer_log (user_id, course, key, value, at) VALUES (:u, 'abdominal', :k, 'null', :t)"),
                         {'u': user, 'k': key, 't': _stamp()})
    funnels = {funnel.title: [(step.label, step.people) for step in funnel.steps] for funnel in _funnels(root)}
    assert funnels['Guides'] == [('Visitors', 4), ('Opened a guide', 2), ('Looked at a guide product page', 1)]
    assert funnels['Tests'][:2] == [('Visitors', 4), ('Opened a test', 0)]
    assert funnels['Course'][1:] == [('Started the course', 2), ('Finished the course', 1)]
    html = root.get('/admin/').text
    assert _bars(_card(html, 'funnels-title'))[:3] == [('Visitors', '4'), ('Opened a guide', '2 · 50%'), ('Looked at a guide product page', '1 · 25%')]


def _funnels(_client):
    import asyncio

    from app import db

    async def run():
        async with db.new_session() as session:
            return await dashboard_stats.funnels(session, stats.last_days(1))
    return asyncio.run(run())


# ---------------------------------------------------------------- the sidebar and the no-access page

def _nav(html):
    return re.findall(r'<a href="(/admin/[^"]*)"[^>]*>\s*<svg[^>]*>.*?</svg><span>([^<]+)</span>', html.split('id="admin-menu-all"')[1].split('</nav>')[0], re.DOTALL)


def test_the_sidebar_lists_only_the_sections_that_exist_and_are_allowed(accounts):
    accounts.add('root@example.com', 'root')
    root = accounts.login('root@example.com')
    labels = [label for _, label in _nav(root.get('/admin/').text)]
    assert labels[:3] == ['Analytics', 'Pages', 'Tests']
    assert ('Orders' in labels) == route_exists('admin_orders')        # a section whose page is not built is left out, not a 500
    assert {'Course', 'Links page', 'Users', 'Groups'} <= set(labels)

    group = accounts.group('Readers', ['analytics.view'])
    accounts.add('reader@example.com', 'admin', groups=[group])
    reader = accounts.login('reader@example.com')
    assert [label for _, label in _nav(reader.get('/admin/').text)] == ['Analytics', 'Pages']


def test_the_sidebar_marks_the_current_page(accounts):
    accounts.add('root@example.com', 'root')
    root = accounts.login('root@example.com')
    def current(path):       # the sidebar and the phone menu each mark it once
        return re.findall(r'<a href="(/admin/[^"]*)" aria-current="page"', root.get(path).text)
    assert current('/admin/pages/') == ['/admin/pages/'] * 2
    assert current('/admin/') == ['/admin/'] * 2


def test_a_link_to_a_missing_page_is_skipped_not_a_500():
    assert route_exists('admin_dashboard') and not route_exists('admin_no_such_page')


def test_a_user_without_the_right_sees_the_no_access_page_in_their_language(accounts):
    group = accounts.group('Tests only', ['tests.view'])
    accounts.add('tester@example.com', 'admin', groups=[group])
    tester = accounts.login('tester@example.com')
    denied = tester.get('/admin/pages/')
    assert denied.status_code == 403 and 'No access' in denied.text and 'Back to the dashboard' in denied.text
    tester.cookies.set('admin_lang', 'ru')
    assert 'Нет доступа' in tester.get('/admin/pages/').text
    assert tester.get('/admin/').headers['location'] == '/admin/tests/'      # the way in for an account without analytics


def test_a_plain_account_is_sent_to_login(accounts):
    accounts.add('plain@example.com', 'user')
    assert accounts.login('plain@example.com').get('/admin/pages/').status_code in (303, 403)


# ---------------------------------------------------------------- notices, language

def test_a_link_notice_is_shown_in_the_admins_language(root):
    root.cookies.set('admin_lang', 'ru')
    html = root.get('/admin/', params={'notice': 'created', 'slug': 'abc'}).text
    assert 'Ссылка /l/abc создана.' in html and 'data-show-on-load' in html
    root.cookies.set('admin_lang', 'en')
    assert 'The link /l/abc was created.' in root.get('/admin/', params={'notice': 'created', 'slug': 'abc'}).text


def test_the_dashboard_is_russian_in_every_block(root):
    root.cookies.set('admin_lang', 'ru')
    html = root.get('/admin/').text
    for text_ in ('Аналитика', 'Воронки', 'Откуда приходят посетители', 'Популярные страницы', 'Результаты тестов', 'Устройства', 'Фильтр переходов',
                  'Свои даты', 'Просмотры страниц'):
        assert text_ in html, text_
    assert 'Where visitors come from' not in html


# ---------------------------------------------------------------- the profile

def test_the_profile_shows_the_account_the_zone_and_the_language(root):
    html = root.get('/admin/profile/').text
    assert 'root@example.com' in html and 'Time zone' in html and 'name="timezone"' in html
    assert re.search(r'<button type="submit" name="lang" value="en" lang="en" aria-current="true">English</button>', html)
    assert 'value="ru" lang="ru">Русский</button>' in html
    root.cookies.set('admin_lang', 'ru')
    assert 'Часовой пояс' in root.get('/admin/profile/').text


def test_the_time_zone_is_saved_or_refused(root, accounts):
    saved = root.post('/admin/profile/timezone', data={'timezone': 'America/New_York'})
    assert saved.status_code == 303 and saved.headers['location'] == '/admin/profile/?saved=1'
    page = root.get('/admin/profile/?saved=1').text
    assert 'Time zone saved.' in page and 'value="America/New_York" selected' in page
    assert accounts.rows("SELECT timezone FROM admin_users WHERE email = 'root@example.com'") == [{'timezone': 'America/New_York'}]
    refused = root.post('/admin/profile/timezone', data={'timezone': 'Mars/Olympus'})
    assert refused.status_code == 422 and 'This time zone is not in the list.' in refused.text
    root.cookies.set('admin_lang', 'ru')
    assert 'Такого часового пояса нет в списке.' in root.post('/admin/profile/timezone', data={'timezone': 'Mars/Olympus'}).text


def test_the_profile_lists_the_permissions_of_an_admin_in_their_language(accounts):
    group = accounts.group('Readers', ['analytics.view'])
    accounts.add('reader@example.com', 'admin', groups=[group])
    reader = accounts.login('reader@example.com')
    html = reader.get('/admin/profile/').text
    assert 'View analytics and pages' in html and 'badge--off' in html
    reader.cookies.set('admin_lang', 'ru')
    assert 'Просмотр аналитики и страниц' in reader.get('/admin/profile/').text or 'View analytics and pages' in reader.get('/admin/profile/').text


def test_the_profile_needs_a_login():
    assert TestClient(app, follow_redirects=False).get('/admin/profile/').status_code == 303
