import json
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth, i18n, stats, visitors
from app.main import app
from tests.helpers import sign_in

EMAIL = 'admin@lolakrissss.test'


@pytest.fixture(scope='module')
def admin_user(sync_db):
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM admin_users'))
        conn.execute(text("INSERT INTO admin_users (email, password_hash, is_active, role) VALUES (:e, :h, 1, 'root')"),
                     {'e': EMAIL, 'h': auth.NO_PASSWORD})
    return EMAIL


@pytest.fixture
def client(admin_user):
    # a browser on our own pages always sends Origin with a POST
    return TestClient(app, follow_redirects=False, headers={'origin': 'http://testserver'})


def _chart(html, metric='clicks'):
    """One chart's points from the page (it carries all three: views, visitors, clicks)."""
    return json.loads(re.search(r'data-chart-data>(.*?)</script>', html, re.DOTALL).group(1))[metric]


def _tiles(html):
    """{'views': 15, 'visitors': 4, 'clicks': 36} — the numbers on the tiles."""
    return {m: int(v.replace(' ', '')) for m, v in
            re.findall(r'data-chart-pick="(\w+)".*?admin-tile__value">([\d ]+)</span>', html, re.DOTALL)}


def login(client, email=EMAIL):
    return sign_in(client, email)


def test_dashboard_requires_login(client):
    response = client.get('/admin/')
    assert response.status_code == 303
    assert response.headers['location'] == '/admin/login'


def test_login_logout_flow(client):
    login(client, '  Admin@Lolakrissss.TEST ')

    dashboard = client.get('/admin/')
    assert dashboard.status_code == 200
    assert EMAIL in dashboard.text and 'data-chart-data' in dashboard.text

    out = client.post('/admin/logout')
    assert out.status_code == 303
    client.cookies.clear()
    assert client.get('/admin/').status_code == 303


def test_tampered_or_expired_cookie_is_ignored(client):
    login(client)
    value = client.cookies[auth.SESSION_COOKIE]
    user_id, expires, sig = value.split('.')
    client.cookies.set(auth.SESSION_COOKIE, f'{user_id}.{int(expires) + 999}.{sig}')
    assert client.get('/admin/').status_code == 303
    client.cookies.set(auth.SESSION_COOKIE, f'{user_id}.1.{sig}')
    assert client.get('/admin/').status_code == 303


def test_a_changed_session_version_ends_sessions(client, sync_db):
    login(client)
    assert client.get('/admin/').status_code == 200
    with sync_db.begin() as conn:
        conn.execute(text('UPDATE admin_users SET session_version = 7 WHERE email = :e'), {'e': EMAIL})
    try:
        assert client.get('/admin/').status_code == 303
    finally:
        with sync_db.begin() as conn:
            conn.execute(text('UPDATE admin_users SET session_version = 0 WHERE email = :e'), {'e': EMAIL})


def test_public_page_views_are_recorded_but_not_admin_or_bots(client, sync_db):
    def count():
        with sync_db.connect() as conn:
            return conn.execute(text('SELECT count(*) FROM page_views')).scalar()

    before = count()
    client.get('/products/?utm_source=instagram', headers={'referer': 'https://www.instagram.com/'})
    assert count() == before + 1
    with sync_db.connect() as conn:
        row = conn.execute(text('SELECT path, referrer_host, utm_source FROM page_views ORDER BY id DESC')).first()
    assert tuple(row) == ('/products/', 'www.instagram.com', 'instagram')

    client.get('/admin/login')
    client.get('/', headers={'user-agent': 'Googlebot/2.1'})
    client.get('/no-such-page/')
    client.get('/', headers={'sec-purpose': 'prefetch'})
    assert count() == before + 1


def test_dashboard_reflects_the_date_filter(client):
    client.get('/guides/')                                   # at least one view today
    login(client)
    html = client.get('/admin/').text
    data = _chart(html)
    # 4-hour points: 29 whole days and today up to the current 4 hours
    now = datetime.now(stats.tz())
    today_points = now.hour // 4 + 1
    assert len(data) == 29 * 6 + today_points and set(data[-1]) == {'d', 'c', 'u'}
    assert data[0]['d'].endswith('T00:00') and data[-1]['d'].startswith(stats.last_days(1).start.isoformat())
    assert _tiles(html)['views'] >= 1                                    # page views live in the tiles, the chart is about clicks
    assert 'aria-current="true" data-admin-preset>30 days' in html

    week = client.get(f'/admin/?from={stats.last_days(1).start - timedelta(days=6)}&to={stats.last_days(1).start}').text
    data = _chart(week)
    assert len(data) == 6 * 6 + today_points
    assert 'aria-current="true" data-admin-preset>7 days' in week


def test_parse_range_defaults_and_bad_input():
    today = stats.last_days(1).start
    assert stats.parse_range(None, None) == stats.DateRange(today - timedelta(days=29), today)
    assert stats.parse_range('garbage', '2020-13-40').days == 30
    swapped = stats.parse_range(str(today), str(today - timedelta(days=3)))
    assert swapped.start < swapped.end
    assert stats.parse_range('2000-01-01', None).days == stats.MAX_DAYS
    assert stats.parse_range(None, str(today + timedelta(days=10))).end == today


def test_placement_labels():
    assert stats.placement_label('test-result:gut') == 'Test result · gut'
    assert stats.placement_label('header:video') == 'Header · video'
    assert stats.placement_label('mystery') == 'mystery'
    assert i18n.translate(stats.placement_label('header:video'), 'ru') == 'Шапка · video'      # the area is translated, the detail is not
    assert i18n.translate(stats.placement_label('short:bye:deleted'), 'ru') == 'Короткая ссылка · bye (удалена)'


# ---------------------------------------------------------------- sortable / filterable tables

from datetime import UTC, datetime  # noqa: E402
from html import unescape  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

from app.admin_tables import Column, TableSpec, TableView  # noqa: E402

CLICKS = [   # (url, placement, how many clicks, hours ago)
    ('https://www.youtube.com/watch?v=abc', 'header:video', 5, 30),
    ('https://shop.example.com/o/basic', 'test-result:basic', 2, 1),
    ('https://www.instagram.com/lola_krissss', 'footer:instagram', 9, 50),
    ('https://example.com/100%_off', 'faq:help', 1, 5),
]


KEYS = ('instagram', 'youtube', 'shop', '100%')


@pytest.fixture
def clicks(sync_db):
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))
        for url, placement, n, hours in CLICKS:
            for i in range(n):
                conn.execute(text(
                    'INSERT INTO outbound_clicks (created_at, target_url, target_host, placement, visitor_hash, device) '
                    'VALUES (:t, :u, :h, :p, :v, :d)'),
                    {'t': (datetime.now(UTC) - timedelta(hours=hours, minutes=i)).strftime('%Y-%m-%d %H:%M:%S.%f'),
                     'u': url, 'h': urlsplit(url).hostname, 'p': placement, 'v': f'v{i % 3}', 'd': 'mobile'})
    yield
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))


def _link_rows(html):
    if 'admin-table--links' not in html:      # empty state instead of a table
        return []
    body = html.split('admin-table--links', 1)[1].split('<tbody>', 1)[1].split('</tbody>', 1)[0]
    return re.findall(r'<td class="admin-link[^"]*">\s*<a href="([^"]+)"', body)


def test_every_admin_table_header_is_a_sort_link(client, clicks, no_short_links):
    """Guard for future tables too: no admin <th> without server-side sorting."""
    login(client)
    create_link(client, slug='guard')                    # the links table only renders with rows
    html = client.get('/admin/').text
    headers = re.findall(r'<th\b[^>]*>.*?</th>', html, re.DOTALL)
    assert len(headers) == 6                             # one links table (site + short links)
    for th in headers:
        assert 'aria-sort=' in th and 'data-admin-sort' in th, th


@pytest.mark.parametrize('sort, expected', [
    ('-clicks', ['instagram', 'youtube', 'shop', '100%']),
    ('clicks', ['100%', 'shop', 'youtube', 'instagram']),
    ('link', ['100%', 'shop', 'instagram', 'youtube']),   # https://e…, https://s…, https://www.i…, https://www.y…
    ('placement', ['100%', 'instagram', 'youtube', 'shop']),   # faq, footer, header, test-result
    ('-last', ['shop', '100%', 'youtube', 'instagram']),
    ('last', ['instagram', 'youtube', '100%', 'shop']),
    ('-visitors', ['instagram', 'youtube', 'shop', '100%']),   # 3 = 3 → tiebreak: more clicks first
])
def test_links_table_sorts_on_the_server(client, clicks, sort, expected):
    login(client)
    rows = _link_rows(client.get(f'/admin/?links_sort={sort}').text)
    assert [next(k for k in expected if k in url) for url in rows] == expected


def test_sort_header_toggles_and_keeps_other_state(client, clicks):
    login(client)
    html = client.get('/admin/?from=2026-09-01&links_q=youtube&links_sort=placement').text
    th = re.search(r'<th[^>]*data-sort-key="placement".*?</th>', html, re.DOTALL).group(0)
    assert 'aria-sort="ascending"' in th
    query = parse_qs(urlsplit(unescape(re.search(r'href="([^"]+)"', th).group(1))).query)
    assert query['links_sort'] == ['-placement'] and query['links_q'] == ['youtube'] and query['from'] == ['2026-09-01']


def test_bad_sort_param_falls_back_to_default(client, clicks):
    login(client)
    response = client.get('/admin/?links_sort=password_hash&days_sort=;drop')
    assert response.status_code == 200
    assert re.search(r'aria-sort="ascending" data-sort-key="source"', response.text)   # default: short links first


@pytest.mark.parametrize('query, expected', [
    ('links_q=instagram', ['instagram']),
    ('links_q=SHOP.EXAMPLE', ['shop']),                 # case-insensitive
    ('links_q=100%25', ['100%']),                       # literal % is escaped, not a wildcard
    ('links_q=zzz', []),
    ('links_placement=test-result:basic&links_placement=header:video', ['youtube', 'shop']),
    ('links_placement=test-result:basic&links_q=instagram', []),
])
def test_links_search_and_placement_filter(client, clicks, query, expected):
    login(client)
    html = client.get(f'/admin/?{query}').text
    rows = _link_rows(html)
    assert [next(k for k in KEYS if k in url) for url in rows] == expected
    if not expected:
        assert 'Nothing found' in html


def test_placement_popover_lists_options_with_counts(client, clicks):
    login(client)
    html = client.get('/admin/?links_placement=test-result:basic').text
    popover = html.split('id="links-placement-filter"', 1)[1].split('</div>', 1)[0] + html.split('id="links-placement-filter"', 1)[1]
    assert 'popover' in html and 'popovertarget="links-placement-filter"' in html
    assert re.search(r'value="test-result:basic"\s+form="links-filter"\s+checked', html)
    assert 'Test result · basic' in html                     # one value chosen: the button names it
    assert 'Footer · instagram' in popover


def test_date_filter_and_presets_keep_table_state(client, clicks):
    login(client)
    html = client.get('/admin/?links_q=youtube&links_sort=placement').text
    date_form = html.split('data-admin-filter', 1)[1].split('</form>', 1)[0]
    assert 'name="links_q" value="youtube"' in date_form and 'name="links_sort" value="placement"' in date_form
    preset = unescape(re.search(r'class="chip" href="([^"]+)"', html).group(1))
    assert 'links_q=youtube' in preset and 'links_sort=placement' in preset


def test_table_view_in_memory_sort_is_stable_and_nulls_last():
    spec = TableSpec('t', default='-n', tiebreak=('name',), columns=(
        Column('name', 'Name', attr='name'), Column('n', 'N', attr='n', numeric=True)))
    rows = [type('R', (), {'name': name, 'n': n}) for name, n in (('b', 1), ('a', 1), ('c', None), ('d', 3))]
    view = TableView(spec, 'n', True, ())
    assert [r.name for r in view.sort(rows)] == ['d', 'a', 'b', 'c']


@pytest.mark.parametrize('query, expected', [
    ('https://www.instagram.com/', ['instagram']),   # scheme / www. / trailing slash don't make every link match
    ('www.youtube', ['youtube']),
    ('watch abc', ['youtube']),                      # every word must match…
    ('shop instagram', []),                          # …so words from different links find nothing
])
def test_search_ignores_url_noise_and_needs_every_word(client, clicks, query, expected):
    login(client)
    rows = _link_rows(client.get('/admin/', params={'links_q': query}).text)
    assert [next(k for k in KEYS if k in url) for url in rows] == expected


# ---------------------------------------------------------------- short links

@pytest.fixture
def no_short_links(sync_db):
    def wipe():
        with sync_db.begin() as conn:
            conn.execute(text('DELETE FROM outbound_clicks WHERE short_link_id IS NOT NULL'))
            conn.execute(text('DELETE FROM short_links'))
    wipe()
    yield
    wipe()


def create_link(client, **data):
    # these tests make several links to one address on purpose; the duplicate check has its own test
    return client.post('/admin/links/', data={'target_url': 'https://example.com/promo', 'allow_duplicate': '1', **data})


def test_links_page_requires_login(client, no_short_links):
    assert client.get('/admin/').status_code in (302, 303)
    assert client.get('/admin/links/').status_code in (302, 303)        # the links page (its buttons) wants a login too
    assert create_link(client).status_code in (302, 303, 401, 403)
    assert client.get('/l/promo').status_code == 404


def test_create_short_link_and_redirect_counts_click(client, sync_db, no_short_links):
    login(client)
    response = create_link(client, slug='Insta', title='Stories')
    assert response.status_code == 303 and response.headers['location'] == '/admin/?notice=created&slug=insta'
    page = client.get('/admin/').text
    assert '/l/insta' in page and 'data-copy="http://testserver/l/insta"' in page

    hit = client.get('/l/insta', headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
    assert hit.status_code == 302 and hit.headers['location'] == 'https://example.com/promo'
    with sync_db.connect() as conn:
        row = conn.execute(text('SELECT placement FROM outbound_clicks WHERE short_link_id IS NOT NULL')).one()
    assert row.placement == 'short:insta'
    assert '<strong>1</strong>' in client.get('/admin/').text
    assert _rows(client.get('/admin/?links_source=short').text) == ['https://example.com/promo short:insta']
    assert 'example.com/promo' not in ' '.join(_rows(client.get('/admin/?links_source=site').text))


def test_auto_slug_is_short(client, no_short_links):
    login(client)
    location = create_link(client).headers['location']
    assert len(re.search(r'slug=([a-z0-9]+)', location).group(1)) == 5


@pytest.mark.parametrize('data', [{'slug': 'bad slug!'}, {'target_url': 'not-a-url'}])
def test_create_rejects_bad_input(client, no_short_links, data):
    login(client)
    response = create_link(client, **data)
    assert response.status_code == 400 and 'admin-alert' in response.text


def test_taken_slug_is_rejected(client, no_short_links):
    login(client)
    create_link(client, slug='promo')
    response = create_link(client, slug='promo')
    assert response.status_code == 400 and 'admin-alert' in response.text


def test_deactivated_link_404s(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='off')
    with sync_db.connect() as conn:
        link_id = conn.execute(text("SELECT id FROM short_links WHERE slug='off'")).scalar()
    response = client.post(f'/admin/links/{link_id}', data={'target_url': 'https://example.com/x', 'title': ''})
    assert response.status_code == 303
    assert client.get('/l/off').status_code == 404


def test_short_link_sets_visitor_cookie_and_counts_uniques(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='vid')
    first = TestClient(app, follow_redirects=False)
    hit = first.get('/l/vid')
    assert visitors.COOKIE in hit.headers['set-cookie'] and 'HttpOnly' in hit.headers['set-cookie']
    again = first.get('/l/vid')                       # same browser: the cookie came back, written once more as seen
    assert visitors.COOKIE in again.headers['set-cookie']
    assert 'set-cookie' not in first.get('/l/vid').headers   # from then on it is just sent
    TestClient(app, follow_redirects=False).get('/l/vid')   # another browser, same IP + UA
    with sync_db.connect() as conn:
        total, unique = conn.execute(text('SELECT count(*), count(DISTINCT visitor_id) FROM outbound_clicks '
                                          'WHERE short_link_id IS NOT NULL')).one()
    assert (total, unique) == (2, 2)                  # the same browser again within 30 s is one click


# ---------------------------------------------------------------- live updates (WebSocket)

def test_live_socket_requires_admin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect('/admin/ws') as ws:
        ws.receive_json()
    assert closed.value.code == 1008


def test_live_socket_refuses_other_origins(client):
    from starlette.websockets import WebSocketDisconnect
    login(client)
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect('/admin/ws', headers={'origin': 'https://evil.example'}) as ws:
        ws.receive_json()
    assert closed.value.code == 1008


def test_live_socket_pushes_new_clicks_and_views(client, no_short_links):
    login(client)
    create_link(client, slug='live')
    with client.websocket_connect('/admin/ws', headers={'origin': 'http://testserver'}) as ws:
        TestClient(app, follow_redirects=False).get('/l/live')
        assert ws.receive_json() == {'type': 'click'}
        client.get('/guides/')
        assert ws.receive_json() == {'type': 'view'}


# ---------------------------------------------------------------- hardening (code review)

def test_concurrent_duplicate_slug_is_a_form_error_not_500(client, no_short_links, monkeypatch):
    login(client)
    create_link(client, slug='race')

    async def never_taken(*_args, **_kwargs):        # the other request inserted after our check
        return False
    monkeypatch.setattr('app.shortlinks.slug_taken', never_taken)
    response = create_link(client, slug='race')
    assert response.status_code == 400 and 'is already taken' in response.text


def test_update_never_redirects_to_another_site(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='back')
    with sync_db.connect() as conn:
        link_id = conn.execute(text("SELECT id FROM short_links WHERE slug='back'")).scalar()
    response = client.post(f'/admin/links/{link_id}', data={'target_url': 'https://example.com/x', 'active': 'on'},
                           headers={'referer': 'https://evil.example/phish'})
    assert response.headers['location'] == '/admin/?notice=updated&slug=back'
    response = client.post(f'/admin/links/{link_id}?short_sort=slug&notice=old&slug=old',   # page state rides on the URL
                           data={'target_url': 'https://example.com/x', 'active': 'on'})
    assert response.headers['location'] == '/admin/?short_sort=slug&notice=updated&slug=back'


def _short_clicks(sync_db):
    with sync_db.connect() as conn:
        return conn.execute(text('SELECT count(*) FROM outbound_clicks WHERE short_link_id IS NOT NULL')).scalar()


def test_browser_prefetch_is_not_a_click(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='pre')
    browser = TestClient(app, follow_redirects=False)
    for purpose in ('prefetch;prerender', 'prefetch'):
        hit = browser.get('/l/pre', headers={'sec-purpose': purpose})
        assert hit.status_code == 503 and hit.headers['cache-control'] == 'no-store' and 'set-cookie' not in hit.headers
    assert _short_clicks(sync_db) == 0
    assert visitors.COOKIE in browser.get('/l/pre').headers['set-cookie']   # the real navigation counts
    assert _short_clicks(sync_db) == 1


def test_repeated_open_counts_once_but_later_visit_counts_again(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='rep')
    browser = TestClient(app, follow_redirects=False)
    for _ in range(5):                                   # reloads / double taps
        assert browser.get('/l/rep').status_code == 302
    assert _short_clicks(sync_db) == 1
    with sync_db.begin() as conn:                        # a minute later it's a new click
        conn.execute(text('UPDATE outbound_clicks SET created_at = :t WHERE short_link_id IS NOT NULL'),
                     {'t': (datetime.now(UTC) - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S.%f')})
    browser.get('/l/rep')
    assert _short_clicks(sync_db) == 2


def _link_id(sync_db, slug):
    with sync_db.connect() as conn:
        return conn.execute(text('SELECT id FROM short_links WHERE slug = :s'), {'s': slug}).scalar()


def test_delete_link_keeps_its_clicks_as_short_links(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='gone')
    TestClient(app, follow_redirects=False).get('/l/gone')
    response = client.post(f"/admin/links/{_link_id(sync_db, 'gone')}/delete")
    assert response.headers['location'] == '/admin/?notice=deleted&slug=gone'
    assert _link_id(sync_db, 'gone') is None and client.get('/l/gone').status_code == 404
    with sync_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM outbound_clicks WHERE placement = 'short:gone:deleted'")).scalar() == 1
    assert 'example.com/promo' in client.get('/admin/?links_source=short&links_status=deleted').text   # still a short link
    assert 'was deleted' in client.get(response.headers['location']).text


def test_toggle_switches_a_link_off_and_on(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='flip')
    link_id = _link_id(sync_db, 'flip')
    assert client.post(f'/admin/links/{link_id}/toggle').headers['location'].endswith('notice=disabled&slug=flip')
    assert client.get('/l/flip').status_code == 404
    assert client.post(f'/admin/links/{link_id}/toggle').headers['location'].endswith('notice=enabled&slug=flip')
    assert client.get('/l/flip').status_code == 302


def test_edit_can_change_the_code_and_rejects_a_taken_one(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='old')
    create_link(client, slug='busy')
    link_id = _link_id(sync_db, 'old')
    data = {'target_url': 'https://example.com/new', 'title': 'T', 'active': 'on'}
    taken = client.post(f'/admin/links/{link_id}', data={**data, 'slug': 'busy'})
    assert taken.status_code == 400 and 'is already taken' in taken.text and 'data-open-on-load' in taken.text
    moved = client.post(f'/admin/links/{link_id}', data={**data, 'slug': 'fresh'})
    assert moved.status_code == 303 and _link_id(sync_db, 'fresh') == link_id
    assert client.get('/l/old').status_code == 404
    assert client.get('/l/fresh').headers['location'] == 'https://example.com/new'


def test_actions_require_login_and_same_origin(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='safe')
    link_id = _link_id(sync_db, 'safe')
    for path in (f'/admin/links/{link_id}/delete', f'/admin/links/{link_id}/toggle'):
        assert client.post(path, headers={'origin': 'https://evil.example'}).headers['location'] == '/admin/login'
    assert _link_id(sync_db, 'safe') == link_id


def test_rename_moves_the_link_history_and_a_reused_code_starts_from_zero(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='spring')
    TestClient(app, follow_redirects=False).get('/l/spring')
    link_id = _link_id(sync_db, 'spring')
    client.post(f'/admin/links/{link_id}', data={'target_url': 'https://example.com/promo', 'slug': 'summer', 'active': 'on'})
    with sync_db.connect() as conn:
        assert conn.execute(text('SELECT placement FROM outbound_clicks WHERE short_link_id = :i'), {'i': link_id}).scalar() == 'short:summer'
    client.post(f'/admin/links/{link_id}/delete')
    create_link(client, slug='summer')                   # the same code again: a new link, no old clicks
    page = client.get('/admin/?links_source=short').text
    assert 'summer (deleted)' in page


def test_failed_edit_keeps_the_page_and_its_state(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='keep')
    link_id = _link_id(sync_db, 'keep')
    page = client.post(f'/admin/links/{link_id}?from=2026-09-01&short_q=ke',
                       data={'target_url': 'not a url', 'slug': 'keep', 'title': 'Mine', 'active': 'on'})
    assert page.status_code == 400
    assert f'action="/admin/links/{link_id}"' in page.text                     # the dialog posts to the same link
    assert 'value="Mine"' in page.text and 'value="not a url"' in page.text    # what was typed comes back
    assert 'data-page-url="/admin/?from=2026-09-01&amp;short_q=ke"' in page.text   # address bar fixed by admin.js
    assert 'value="ke"' in page.text                                            # search kept


@pytest.mark.parametrize('slug', ['<b>x</b>', 'A B', ''])
def test_notice_with_a_bad_slug_is_not_shown(client, slug):
    login(client)
    html = client.get('/admin/', params={'notice': 'created', 'slug': slug}).text
    assert 'data-show-on-load' not in html and '<b>x</b>' not in html


def test_analytics_rows_can_be_copied(client, sync_db, clicks, no_short_links):
    login(client)
    create_link(client, slug='copyme')
    TestClient(app, follow_redirects=False).get('/l/copyme')
    html = client.get('/admin/').text
    assert 'data-copy="http://testserver/l/copyme"' in html               # short link: the address to share
    # site link: the signed tracking redirect, which leads to the destination and counts in the same placement
    tracked = unescape(re.search(r'data-copy="(http://testserver/go/\?[^"]+from=header%3Avideo[^"]*)"', html).group(1))
    with sync_db.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM outbound_clicks WHERE placement = 'header:video'")).scalar()
    hit = TestClient(app, follow_redirects=False).get(tracked.removeprefix('http://testserver'))
    assert hit.status_code == 302 and hit.headers['location'] == 'https://www.youtube.com/watch?v=abc'
    with sync_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM outbound_clicks WHERE placement = 'header:video'")).scalar() == before + 1


def test_a_real_repeat_click_counts_and_head_does_not(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='again')
    browser = TestClient(app, follow_redirects=False)
    assert browser.head('/l/again').status_code == 302               # a messenger's link preview
    assert _short_clicks(sync_db) == 0
    browser.get('/l/again')
    with sync_db.begin() as conn:                                     # the same person comes back 10 s later
        conn.execute(text('UPDATE outbound_clicks SET created_at = :t WHERE short_link_id IS NOT NULL'),
                     {'t': (datetime.now(UTC) - timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S.%f')})
    browser.get('/l/again')
    assert _short_clicks(sync_db) == 2


def test_link_filters_drive_the_chart_line_and_the_tile(client, clicks):
    login(client)

    def chart_clicks(query=''):
        html = client.get(f'/admin/{query}').text
        data = _chart(html)
        return sum(d['c'] for d in data), html

    everything, html = chart_clicks()
    only_instagram, filtered = chart_clicks('?links_q=instagram')
    assert everything > only_instagram > 0
    assert _tiles(filtered)['clicks'] == only_instagram and 'filtered' in filtered
    assert '>filtered<' not in html


# ---------------------------------------------------------------- cursor pagination

def _section(html, table='links'):
    """The card of one of the two feeds on the page: 'links' (clicks) or 'short' (short links)."""
    start = html.index('admin-table--links' if table == 'links' else 'id="short-links"')
    return html[start:html.index('</section>', start)]


def _rows(html, table='links'):
    if table == 'links' and 'admin-table--links' not in html:
        return []
    return re.findall(r'<tr[^>]*data-key="([^"]+)"', _section(html, table))


def _feed(client, url, table='links'):
    """Walk a paginated feed through its "Show more" links; returns the row keys page by page."""
    pages = []
    while url:
        html = client.get(url).text
        pages.append(_rows(html, table))
        more = re.search(r'href="([^"]+)" data-admin-more', _section(html, table))
        url = '/admin/' + unescape(more.group(1)) if more else None
    return pages


@pytest.fixture
def many_clicks(sync_db):
    """70 links, click counts with lots of ties, so every sort needs the tiebreak to be exact."""
    now = datetime.now(UTC)
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))
        for n in range(70):
            for i in range(n % 4 + 1):
                conn.execute(text(
                    'INSERT INTO outbound_clicks (created_at, target_url, target_host, placement, visitor_hash, device) '
                    'VALUES (:t, :u, :h, :p, :v, :d)'),
                    {'t': (now - timedelta(minutes=n * 3 + i)).strftime('%Y-%m-%d %H:%M:%S.%f'),
                     'u': f'https://example.com/{n:02d}', 'h': 'example.com', 'p': ('footer:x', 'header:y')[n % 2],
                     'v': f'v{i}', 'd': 'mobile'})
    yield
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))


@pytest.mark.parametrize('sort', ['-clicks', 'clicks', '-last', 'last', 'link', '-link', 'placement', '-visitors', 'source'])
def test_analytics_feed_pages_cover_every_row_once_in_order(client, many_clicks, sort):
    login(client)
    pages = _feed(client, f'/admin/?links_sort={sort}')
    assert len(pages) == 3 and [len(p) for p in pages] == [30, 30, 10]
    walked = [key for page in pages for key in page]
    everything = _rows(client.get(f'/admin/?links_sort={sort}&links_take=300').text)
    assert walked == everything and len(set(walked)) == 70


def test_feed_keeps_search_and_filters_across_pages(client, many_clicks):
    login(client)
    pages = _feed(client, '/admin/?links_placement=footer:x&links_sort=-clicks')
    walked = [key for page in pages for key in page]
    assert len(walked) == 35 and all(key.endswith(' footer:x') for key in walked)


@pytest.mark.parametrize('sort', ['-clicks', 'clicks', '-last', 'last', 'link', 'placement', 'source', '-visitors'])
def test_one_table_pages_site_and_short_links_together(client, sync_db, many_clicks, no_short_links, sort):
    """70 site links + 45 short links (most never clicked: NULL last click) in ONE cursor-paginated table."""
    login(client)
    with sync_db.begin() as conn:
        for n in range(45):
            conn.execute(text("INSERT INTO short_links (slug, target_url, title, is_active, created_at) "
                              "VALUES (:s, 'https://example.com/', :t, 1, :c)"),
                         {'s': f'p{n:02d}', 't': f'T{n % 3}',
                          'c': (datetime.now(UTC) - timedelta(hours=n // 2)).strftime('%Y-%m-%d %H:%M:%S')})
    browser = TestClient(app, follow_redirects=False)
    for n in range(0, 45, 4):                                         # a few short links clicked, the rest have none
        browser.get(f'/l/p{n:02d}')
    pages = _feed(client, f'/admin/?links_sort={sort}')
    walked = [key for page in pages for key in page]
    everything = _rows(client.get(f'/admin/?links_sort={sort}&links_take=300').text)
    assert [len(p) for p in pages] == [30, 30, 30, 25] and walked == everything and len(set(walked)) == 115
    assert sum(1 for key in walked if ' short:p' in key) == 45        # every short link listed, clicked or not


def test_bad_cursor_and_take_fall_back_to_the_first_page(client, many_clicks):
    login(client)
    first = _rows(client.get('/admin/').text)
    for query in ('links_after=%%%', 'links_after=W10', 'links_take=abc', 'links_take=-5'):
        rows = _rows(client.get(f'/admin/?{query}').text)
        assert rows[:1] == first[:1], query
    assert len(_rows(client.get('/admin/?links_take=100000').text)) == 70   # capped, not unlimited


def test_feed_counter_shows_shown_of_total_and_disappears_on_the_last_page(client, many_clicks):
    login(client)
    first = _section(client.get('/admin/').text, 'links')
    assert '30 of 70' in first and 'Show more' in first
    last = client.get('/admin/?links_take=70').text
    assert len(_rows(last)) == 70 and 'data-load-more' not in _section(last, 'links')   # nothing left: no button, no counter


def test_sort_and_filter_links_start_from_the_first_page(client, many_clicks):
    login(client)
    html = client.get('/admin/?links_after=' + _feed(client, '/admin/')[0][0].replace(' ', '%20')).text
    assert 'links_after' not in ''.join(re.findall(r'href="([^"]*)" data-admin-sort', html))


def test_chart_shows_clicks_and_unique_clickers_under_the_filter(client, clicks):
    login(client)

    def chart(query=''):
        html = client.get(f'/admin/{query}').text
        data = _chart(html)
        return sum(d['c'] for d in data), max(d['u'] for d in data)

    clicks_all, unique_all = chart()
    clicks_ig, unique_ig = chart('?links_q=instagram')
    assert clicks_all > clicks_ig > 0 and 0 < unique_ig <= unique_all <= 3     # 3 distinct visitors in the fixture


@pytest.mark.parametrize('query', ['findme', '/l/findme', 'http://testserver/l/findme', 'testserver/l/findme',
                                   'breakfast', 'Story'])
def test_analytics_search_finds_short_links_by_code_url_and_title(client, sync_db, no_short_links, query):
    login(client)
    create_link(client, slug='findme', title='Story about breakfast')
    TestClient(app, follow_redirects=False).get('/l/findme')
    html = client.get('/admin/', params={'links_q': query}).text
    assert _rows(html) == ['https://example.com/promo short:findme'], query
    data = _chart(html)
    assert sum(d['c'] for d in data) == 1                      # the chart follows the same search


def test_search_only_column_is_not_a_sort_key(client, clicks):
    login(client)
    response = client.get('/admin/?links_sort=short_title')
    assert response.status_code == 200 and 'aria-sort="ascending" data-sort-key="source"' in response.text


def test_a_pasted_tracking_link_finds_its_own_row(client, clicks):
    login(client)
    html = client.get('/admin/').text
    tracked = unescape(re.search(r'data-copy="(http://testserver/go/\?[^"]+from=header%3Avideo[^"]*)"', html).group(1))
    for query in (tracked, tracked.removeprefix('http://'), tracked.removeprefix('http://testserver')):
        found = _rows(client.get('/admin/', params={'links_q': query}).text)
        assert found == ['https://www.youtube.com/watch?v=abc header:video'], query


# ---------------------------------------------------------------- several searches (one per link)

def test_several_searches_show_all_those_links_together(client, clicks):
    login(client)
    one = set(_rows(client.get('/admin/?links_q=instagram').text))
    two = set(_rows(client.get('/admin/?links_q=youtube').text))
    both_html = client.get('/admin/?links_q=instagram&links_q=youtube').text
    assert one and two and set(_rows(both_html)) == one | two
    assert both_html.count('type="search" name="links_q"') == 2            # one input per link, as typed
    assert 'links_q=instagram&amp;links_q=youtube' in both_html                  # kept in sort / period links
    data = _chart(both_html)
    def single(q):
        return sum(d['c'] for d in _chart(client.get(f'/admin/?links_q={q}').text))
    assert sum(d['c'] for d in data) == single('instagram') + single('youtube')   # the chart sums them too
    assert set(_rows(client.get('/admin/?links_q=instagram&links_q=&links_q=instagram').text)) == one   # blanks / repeats ignored


def test_deleted_short_link_rows_are_struck_through(client, sync_db, no_short_links):
    login(client)
    create_link(client, slug='bye')
    TestClient(app, follow_redirects=False).get('/l/bye')
    client.post(f"/admin/links/{_link_id(sync_db, 'bye')}/delete")
    row = re.search(r'<tr class="[^"]*is-deleted[^"]*"[^>]*data-key="https://example.com/promo short:bye:deleted".*?</tr>',
                    client.get('/admin/?links_status=deleted').text, re.DOTALL).group(0)
    assert '<s class="link-id__slug">/l/bye</s>' in row and 'Deleted' in row
    assert 'data-link-edit' not in row and 'data-copy' not in row   # nothing to edit or copy any more


def test_status_filter_active_off_deleted(client, sync_db, no_short_links):
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))
    login(client)
    for slug in ('on', 'paused', 'gone'):
        create_link(client, slug=slug)
    TestClient(app, follow_redirects=False).get('/l/gone')
    client.post(f"/admin/links/{_link_id(sync_db, 'paused')}/toggle")
    client.post(f"/admin/links/{_link_id(sync_db, 'gone')}/delete")

    def keys(query):
        return sorted(k.split(' ', 1)[1] for k in _rows(client.get(f'/admin/?{query}').text))
    assert keys('links_status=active') == ['short:on']
    assert keys('links_status=off') == ['short:paused']
    assert keys('links_status=deleted') == ['short:gone:deleted']
    assert keys('links_status=off&links_status=deleted') == ['short:gone:deleted', 'short:paused']
    html = client.get('/admin/').text
    assert re.search(r'value="off"[^>]*>\s*<span>Off</span>\s*<span class="table-popover__count">1<', html)
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM outbound_clicks'))


def test_default_view_short_links_first_then_site_only_active(client, sync_db, clicks, no_short_links):
    login(client)
    create_link(client, slug='mine')
    create_link(client, slug='old')
    client.post(f"/admin/links/{_link_id(sync_db, 'old')}/toggle")      # switched off → hidden by default
    keys = _rows(client.get('/admin/').text)
    assert keys[0] == 'https://example.com/promo short:mine'             # short links on top
    assert not any(' short:old' in k for k in keys)                       # only active ones by default
    assert all(not k.split(' ', 1)[1].startswith('short:') for k in keys[1:])
    html = client.get('/admin/').text
    assert '>filtered<' not in html and '>Active<' in html             # the default isn't "a filter"; the button names it


def test_a_single_day_is_charted_by_the_hour(client, clicks):
    login(client)
    today = stats.last_days(1).start.isoformat()
    html = client.get(f'/admin/?from={today}&to={today}').text
    data = _chart(html)
    # 15-minute steps; today only up to the current one (the chart keeps the whole day on its axis)
    slots = [f'T{m // 60:02d}:{m % 60:02d}' for m in range(0, 24 * 60, 15)]
    now = datetime.now(stats.tz())
    shown = (now.hour * 60 + now.minute) // 15 + 1 if now.date().isoformat() == today else 96
    assert [p['d'] for p in data] == [today + t for t in slots[:shown]]
    yesterday = (stats.last_days(1).start - timedelta(days=1)).isoformat()
    past = _chart(client.get(f'/admin/?from={yesterday}&to={yesterday}').text)
    assert [p['d'] for p in past] == [yesterday + t for t in slots]                    # a past day: all 96
    assert sum(p['c'] for p in data) == _tiles(html)['clicks']
    assert [p['d'] for p in _chart(html, 'views')] == [p['d'] for p in data]       # views by the hour too
    assert 'Clicks per 15 minutes' in html and 'aria-current="true" data-admin-preset>Today' in html
    week = client.get('/admin/?links_take=1').text                              # the default 30 days stay daily
    assert 'Clicks per 4 hours' in week


def test_dates_follow_the_browser_time_zone(client):
    login(client)

    def page(zone):
        client.cookies.set('admin_tz', zone, path='/admin')
        html = client.get('/admin/').text
        return html, re.search(r'name="to" value="([0-9-]+)"', html).group(1)

    east, east_today = page('Pacific/Kiritimati')      # UTC+14
    west, west_today = page('Pacific/Pago_Pago')       # UTC-11: 25 hours behind, always another day
    # 25 hours apart: one day, or two in the hour after 10:00 UTC (Kiritimati already past midnight, Pago Pago not yet)
    assert date.fromisoformat(east_today) - date.fromisoformat(west_today) in (timedelta(days=1), timedelta(days=2))
    assert 'data-tz="Pacific/Kiritimati"' in east and 'UTC+14:00' in east
    assert 'data-tz="Pacific/Pago_Pago"' in west and 'UTC-11:00' in west
    renamed, _ = page('Europe/Kiev')                   # browsers still say Kiev; databases know Kyiv
    assert 'data-tz="Europe/Kyiv" data-tz-asked="Europe/Kiev"' in renamed
    for bad in ('Mars/Olympus', '../../etc/passwd', 'x' * 200):
        html, _ = page(bad)
        assert 'data-tz="UTC"' in html, bad           # unknown → UTC
    client.cookies.delete('admin_tz', path='/admin')


def test_tiles_pick_the_chart_views_by_default(client, clicks):
    login(client)
    html = client.get('/admin/').text
    assert 'data-metric="views"' in html
    assert re.search(r'data-chart-pick="views"[^>]*aria-pressed="true"', html)
    assert set(json.loads(re.search(r'data-chart-data>(.*?)</script>', html, re.DOTALL).group(1))) >= {'views', 'visitors', 'clicks', 'step', 'slots'}
    client.cookies.set('admin_chart', 'clicks', path='/admin')
    picked = client.get('/admin/').text
    assert 'data-metric="clicks"' in picked and re.search(r'data-chart-pick="clicks"[^>]*aria-pressed="true"', picked)
    client.cookies.set('admin_chart', '<script>', path='/admin')
    assert 'data-metric="views"' in client.get('/admin/').text           # anything else → views
    client.cookies.delete('admin_chart', path='/admin')


def test_the_one_day_chart_loads_earlier_days(client, clicks):
    login(client)
    yesterday = stats.last_days(1).start - timedelta(days=1)
    day = client.get(f'/admin/chart-day?day={yesterday.isoformat()}').json()
    assert day['day'] == yesterday.isoformat() and day['step'] == 15
    assert [p['d'] for p in day['views']][:2] == [f'{yesterday.isoformat()}T00:00', f'{yesterday.isoformat()}T00:15']
    assert len(day['views']) == len(day['clicks']) == 96                      # a past day: all of it
    assert client.get('/admin/chart-day?day=nonsense').status_code == 400
    future = stats.last_days(1).start + timedelta(days=1)
    assert client.get(f'/admin/chart-day?day={future.isoformat()}').status_code == 400
    html = client.get(f'/admin/?from={stats.last_days(1).start}&to={stats.last_days(1).start}').text
    assert 'data-day-url="/admin/chart-day?' in html
    assert '"history_start"' in html


def test_chart_day_needs_a_login():
    assert TestClient(app).get('/admin/chart-day?day=2026-01-01').status_code == 401


def test_devices_of_visitors_and_of_the_filtered_link_clicks(sync_db, accounts):
    """Devices: visitors per device, and clicks per device under the link filter (like the chart)."""
    import re as _re
    with sync_db.begin() as conn:
        for n, device in enumerate(['mobile', 'mobile', 'mobile', 'desktop']):
            conn.execute(text("INSERT INTO page_views (created_at, path, visitor_hash, device) VALUES ('2020-04-02 10:00:00', '/', :h, :d)"),
                         {'h': f'dev-visitor-{n}', 'd': device})
        conn.execute(text("INSERT INTO page_views (created_at, path, visitor_hash, device) VALUES ('2020-04-02 10:00:00', '/', 'dev-bot', 'bot')"))
        for url, device in [('https://devices-a.example/', 'mobile'), ('https://devices-a.example/', 'tablet'),
                            ('https://devices-b.example/', 'desktop')]:
            conn.execute(text("INSERT INTO outbound_clicks (created_at, target_url, target_host, placement, visitor_hash, device, user_agent) "
                              "VALUES ('2020-04-03 10:00:00', :u, 'x', 'footer', :h, :d, '')"), {'u': url, 'h': f'dev-{url}-{device}', 'd': device})
    accounts.add('root@example.com', 'root')
    admin = accounts.login('root@example.com')

    def devices(query=''):
        html = admin.get(f'/admin/?from=2020-04-01&to=2020-04-07{query}').text
        card = html[html.index('id="devices-title"'):html.index('id="filter-title"')]
        halves = card.split('class="devices"')[1:]
        return [dict(_re.findall(r'</svg>([^<]+)</span>.*?<strong>(\d+)%</strong>', half, _re.DOTALL)) for half in halves]

    visits, clicks = devices()
    assert visits == {'Phone': '75', 'Computer': '25', 'Tablet': '0'}           # the bot isn't a device
    assert clicks == {'Phone': '33', 'Computer': '33', 'Tablet': '33'}
    _, filtered = devices('&links_q=devices-a')
    assert filtered == {'Phone': '50', 'Computer': '0', 'Tablet': '50'}

    folded = _re.compile(r'<details class="admin-card devices-card"[^>]*>')            # folded unless left open
    assert ' open' not in folded.search(admin.get('/admin/').text).group(0)
    admin.cookies.set('admin_devices', '1', path='/admin')
    assert folded.search(admin.get('/admin/').text).group(0).endswith(' open>')


def test_a_short_link_with_an_impossible_port_is_a_form_error_not_a_crash():
    from app import shortlinks
    assert shortlinks.validate('', 'https://a.com:99999/x')
    assert shortlinks.validate('', 'https://a.com:443/x') is None
