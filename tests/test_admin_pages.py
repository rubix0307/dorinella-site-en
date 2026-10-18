import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth, stats
from app.catalog import PRODUCTS
from app.learn import GUIDES
from app.main import app
from tests.helpers import sign_in

EMAIL = 'stats@example.com'
GUIDE = next(iter(GUIDES.values()))
PRODUCT = next(iter(PRODUCTS.values()))
# path, views, distinct visitors
VIEWS = [('/', 6, 3), ('/guides/', 4, 2), (f'/guides/{GUIDE.slug}/', 5, 2), (f'/products/{PRODUCT.slug}/', 2, 1), ('/tests/', 1, 1), ('/weird/', 1, 1)]


@pytest.fixture
def admin(sync_db):
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM admin_users'))
        conn.execute(text('DELETE FROM login_failures'))
        conn.execute(text('DELETE FROM page_views'))
        conn.execute(text("INSERT INTO admin_users (email, password_hash, is_active, role) VALUES (:e, :h, 1, 'root')"),
                     {'e': EMAIL, 'h': auth.NO_PASSWORD})
        stamp = (datetime.now(UTC) - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S.%f')
        for path, n, visitors in VIEWS:
            for i in range(n):
                conn.execute(text('INSERT INTO page_views (created_at, path, visitor_hash, device) VALUES (:t, :p, :v, :d)'),
                             {'t': stamp, 'p': path, 'v': f'v{i % visitors}', 'd': 'mobile'})
    client = TestClient(app, follow_redirects=False, headers={'origin': 'http://testserver'})
    sign_in(client, EMAIL)
    yield client
    with sync_db.begin() as conn:
        conn.execute(text('DELETE FROM page_views'))


def _rows(html: str) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for row in re.findall(r'<tr(?: class="is-empty")?>(.*?)</tr>', html.split('<tbody>')[1], re.DOTALL):
        label = re.sub(r'\s+', ' ', re.sub(r'<small>.*?</small>|<[^>]+>', ' ', cast(re.Match[str], re.search(r'<th scope="row">(.*?)</th>', row, re.DOTALL))[1])).strip()
        nums = [int(n.replace(',', '')) for n in re.findall(r'<td class="num"[^>]*>([\d,]+)</td>', row)]
        out.append((label, *nums))
    return out


def test_needs_a_login():
    assert TestClient(app, follow_redirects=False).get('/admin/pages/').status_code == 303


def test_sections_add_up_and_sort_by_views(admin):
    today = stats.last_days(1).start.isoformat()
    html = admin.get(f'/admin/pages/?from={today}&to={today}').text
    rows = {label: (views, visitors) for label, views, visitors in _rows(html)}
    assert rows['Home'] == (6, 3)
    assert rows['Guides'] == (9, 2)           # the library + a guide; the same 2 visitors counted once
    assert rows['Products'] == (2, 1) and rows['Tests'] == (1, 1) and rows['Other'] == (1, 1)
    assert 'Course' not in rows               # /course/ views are never stored, so the section would always be 0
    assert [r[1] for r in _rows(html)] == sorted((r[1] for r in _rows(html)), reverse=True)
    assert 'section=guides' in html and '19 views' in html


def test_drill_down_into_a_section(admin):
    today = stats.last_days(1).start.isoformat()
    html = admin.get(f'/admin/pages/?from={today}&to={today}&section=guides').text
    rows = _rows(html)
    assert rows[0] == (f'Guide: {GUIDE.title}', 5, 2)         # a guide by its name, not its address
    assert rows[1] == ('Guide library', 4, 2)
    assert 'All sections' in html and f'href="/guides/{GUIDE.slug}/"' in html
    assert 'name="section" value="guides"' in html            # the period filter keeps the section


def test_the_drill_down_is_translated(admin):
    today = stats.last_days(1).start.isoformat()
    admin.cookies.set('admin_lang', 'ru')
    html = admin.get(f'/admin/pages/?from={today}&to={today}&section=guides').text
    assert 'Все разделы' in html and f'Гайд: {GUIDE.title}' in html and 'Библиотека гайдов' in html


def test_unknown_section_falls_back_to_all(admin):
    html = admin.get('/admin/pages/?section=<script>').text
    assert 'Sections of the site' in html and '<script>' not in html.split('<main')[1].split('</main>')[0]
