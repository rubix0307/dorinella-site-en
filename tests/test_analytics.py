import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.analytics import device_of, outbound_url
from app.catalog import PRODUCTS
from app.learn import GUIDES, SELF_TESTS
from app.main import app

UA_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'
YOUTUBE = 'https://www.youtube.com/watch?v=abc123'


@pytest.fixture(scope='module')
def client(sync_db):
    return TestClient(app, follow_redirects=False)


def _rows(sync_db):
    with sync_db.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text('SELECT * FROM outbound_clicks ORDER BY id'))]


def test_tracked_link_redirects_and_is_recorded(client, sync_db):
    before = len(_rows(sync_db))
    link = outbound_url(YOUTUBE, 'test-result:gut')
    response = client.get(link, headers={
        'referer': 'http://testserver/products/?utm_source=instagram&utm_campaign=autumn',
        'user-agent': UA_IPHONE,
    })
    assert response.status_code == 302
    assert response.headers['location'] == YOUTUBE
    assert response.headers['cache-control'] == 'no-store'

    rows = _rows(sync_db)
    assert len(rows) == before + 1
    row = rows[-1]
    assert row['target_host'] == 'www.youtube.com'
    assert row['placement'] == 'test-result:gut'
    assert row['page_path'] == '/products/'
    assert (row['utm_source'], row['utm_campaign']) == ('instagram', 'autumn')
    assert row['device'] == 'mobile'
    assert len(row['visitor_hash']) == 32


def test_external_referer_is_not_stored_as_page(client, sync_db):
    client.get(outbound_url(YOUTUBE, 'footer:video'), headers={'referer': 'https://evil.example/somewhere'})
    assert _rows(sync_db)[-1]['page_path'] is None


@pytest.mark.parametrize('query', [
    'to=https://evil.example/&from=footer:video&sig=deadbeef',                       # forged signature
    'to=javascript:alert(1)&from=x&sig=0',                                             # not http(s)
    'to=https://a.com&from=x&sig=%C3%A9',                                              # non-ASCII signature: 400, not 500
])
def test_unsigned_or_unsafe_links_are_refused(client, query):
    response = client.get('/go/?' + query)
    assert response.status_code == 400
    assert 'location' not in response.headers


def test_tampered_target_is_refused(client):
    link = outbound_url(YOUTUBE, 'header:video')
    assert client.get(link.replace('abc123', 'other')).status_code == 400


def test_missing_params_are_rejected(client):
    assert client.get('/go/').status_code in (400, 422)


def test_go_without_trailing_slash_keeps_the_query(client):
    link = outbound_url(YOUTUBE, 'header:video')
    response = client.get(link.replace('/go/?', '/go?'))
    assert response.status_code == 301
    assert response.headers['location'] == link


def test_health_reports_database(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'database': 'ok'}


PAGES = ['/', '/products/', '/guides/', '/tests/', '/links/',
         *[f'/products/{s}/' for s in PRODUCTS], *[f'/guides/{s}/' for s in GUIDES], *[f'/tests/{s}/' for s in SELF_TESTS]]


@pytest.mark.parametrize('path', PAGES)
def test_every_external_link_is_tracked_and_valid(client, path):
    html = client.get(path).text
    hrefs = [unescape(h) for h in re.findall(r'<a [^>]*href="([^"]+)"', html)]
    external = [h for h in hrefs if h.startswith(('http://', 'https://'))]
    assert external == [], f'untracked external links on {path}: {external}'
    for href in (h for h in hrefs if h.startswith('/go/')):
        query = parse_qs(urlsplit(href).query)
        assert client.get(href).status_code == 302, href
        assert query['from'][0]


@pytest.mark.parametrize('ua, device', [
    (UA_IPHONE, 'mobile'),
    ('Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)', 'tablet'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0', 'desktop'),
    ('TelegramBot (like TwitterBot)', 'bot'),
])
def test_device_detection(ua, device):
    assert device_of(ua) == device


def test_text_columns_sort_naturally_not_letter_by_letter():
    from app.admin_tables import _sort_value
    names = ['gut_health_10', 'Gut_health_2', 'gut_health_1', 'gut_health_14', 'abc']
    assert sorted(names, key=_sort_value) == ['abc', 'gut_health_1', 'Gut_health_2', 'gut_health_10', 'gut_health_14']
