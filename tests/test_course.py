"""The «Gut Massage» course: closed to everybody but admins (or buyers in the paid mode), invisible to everybody else, complete for admins."""
import re

import pytest
from fastapi.testclient import TestClient

from app import course
from app.main import app

BASE = course.BASE
PAGES = [p.slug for p in course.PUBLIC_PAGES]


@pytest.fixture
def anon():
    return TestClient(app, follow_redirects=False, headers={'origin': 'http://testserver'})


@pytest.fixture
def admin(accounts):
    accounts.add('course-root@example.test', role='root')
    return accounts.login('course-root@example.test')


@pytest.fixture
def customer(accounts):
    accounts.add('course-customer@example.test', role='user')
    return accounts.login('course-customer@example.test')


def walk(client, upto=None):
    """Pass the pages in order (the safety page with its confirmation) until `upto` is reached."""
    for slug in PAGES:
        if slug == upto:
            return
        r = client.post(f'{BASE}{slug}/complete', data={'ack': '1'} if slug == 'safety' else {})
        assert r.status_code == 303, slug


def _missing(client):
    return client.get('/this-page-does-not-exist-at-all/')


@pytest.mark.parametrize('path', ['/course', '/course/', BASE, f'{BASE}techniques/', f'{BASE}data/massage.json',
                                  f'{BASE}nope/', '/course/anything/else'])
def test_anonymous_gets_the_plain_404(anon, path):
    r = anon.get(path)
    assert r.status_code == 404
    def same(text: str, was: str) -> str:
        return re.sub(r'nonce="[^"]*"', 'nonce="-"', text.replace(was, '/x'))   # the CSP nonce differs on every response

    assert same(r.text, path) == same(_missing(anon).text, '/this-page-does-not-exist-at-all/')


@pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete', 'options'])
def test_anonymous_other_methods_are_404_too(anon, method):
    assert getattr(anon, method)(f'{BASE}techniques/').status_code == 404
    assert getattr(anon, method)('/course/x').status_code == 404


def test_signed_in_customer_is_not_staff(customer):
    for path in (BASE, f'{BASE}techniques/', f'{BASE}data/massage.json'):
        assert customer.get(path).status_code == 404


def test_not_listed_anywhere_public(anon):
    assert 'course' not in anon.get('/robots.txt').text.lower()
    assert course.BASE not in anon.get('/').text            # the sales page may talk about the course, but nothing links into it


def test_admin_reads_every_page_privately(admin):
    walk(admin, 'author')
    for slug in PAGES:
        r = admin.get(f'{BASE}{slug}/')
        assert r.status_code == 200, slug
        assert r.headers['cache-control'] == 'private, no-store'
        assert 'noindex' in r.headers['x-robots-tag']
        assert 'name="robots" content="noindex' in r.text
        assert 'rel="canonical"' not in r.text and 'og:url' not in r.text


def test_admin_home_and_data(admin):
    assert admin.get(BASE).status_code == 200
    assert admin.get(course.DATA_URL).status_code == 404            # the player data opens with the techniques page
    walk(admin, 'techniques')
    r = admin.get(course.DATA_URL)
    assert r.status_code == 200 and r.headers['cache-control'] == 'private, no-store'
    assert set(r.json()['techniques']) == {f'm{i}' for i in range(1, 9)}


def test_no_trailing_slash_redirect_oracle(anon, admin):
    assert anon.get(f'{BASE}techniques').status_code == 404


def test_hub_lists_all_techniques_without_js(admin):
    walk(admin, 'techniques')
    html = admin.get(f'{BASE}techniques/').text
    for i in range(1, 9):
        assert f'id="m{i}"' in html
    assert 'data-version="' in html




def test_player_data_matches_written_steps():
    import json
    data = json.loads(course.DATA_FILE.read_text(encoding='utf-8'))
    for t in course.TECHNIQUES:
        tech = data['techniques'][t['key']]
        labelled = [c for c in tech['captions'] if c[1]]
        assert len(labelled) == len(t['steps']), t['key']
        for (label, _text, start), cap in zip(t['steps'], labelled, strict=False):
            assert label == cap[1] and abs(start - cap[0]) < 0.01, t['key']


def test_modes(monkeypatch, anon):
    monkeypatch.setattr(course, 'access_mode', lambda: 'public')
    assert anon.get(f'{BASE}techniques/').status_code == 200
    r = anon.get(f'{BASE}techniques/')
    assert 'no-store' not in r.headers.get('cache-control', '')
    monkeypatch.setattr(course, 'access_mode', lambda: 'paid')
    assert anon.get(f'{BASE}techniques/').status_code == 404


def test_listing_helper():
    class U:
        role = 'user'
    class A:
        role = 'admin'
    assert not course.listed_for(U()) and not course.listed_for(None)
    assert course.listed_for(A())


def test_404_sets_cookie_like_any_other_page():
    ua = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36'}
    def cookie(path):
        c = TestClient(app, follow_redirects=False)
        return 'set-cookie' in c.get(path, headers=ua).headers
    assert cookie('/course/abdominal-massage/techniques/') == cookie('/nope/')


def test_head_as_admin_is_private(admin):
    walk(admin, 'techniques')
    r = admin.head(f'{BASE}techniques/')
    assert r.status_code == 200 and r.headers['cache-control'] == 'private, no-store'


def test_course_views_are_not_counted():
    from types import SimpleNamespace

    from app import analytics
    req = SimpleNamespace(method='GET', url=SimpleNamespace(path=f'{BASE}techniques/'), headers={'user-agent': 'Mozilla/5.0 Chrome/120'})
    assert not analytics.is_trackable_view(req, 200, 'text/html')


# ---------------------------------------------------------------- steps in order, answers in the database

def test_pages_open_one_after_another(admin):
    r = admin.get(f'{BASE}preparation/')
    assert r.status_code == 303 and r.headers['location'] == f'{BASE}about/'
    assert admin.get(f'{BASE}about/').status_code == 200
    assert admin.get(f'{BASE}safety/').status_code == 303          # «about» is not passed yet
    assert admin.post(f'{BASE}about/complete').status_code == 303
    assert admin.get(f'{BASE}safety/').status_code == 200
    assert admin.get(f'{BASE}body/').status_code == 303


def test_a_locked_page_cannot_be_completed(admin):
    r = admin.post(f'{BASE}body/complete', data={'ack': '1'})
    assert r.headers['location'] == f'{BASE}about/'
    assert admin.get(f'{BASE}about/').status_code == 200 and admin.get(f'{BASE}body/').status_code == 303


def test_safety_confirmation_is_required(admin):
    admin.post(f'{BASE}about/complete')
    r = admin.post(f'{BASE}safety/complete')
    assert r.status_code == 303 and 'need=ack' in r.headers['location']
    assert admin.get(f'{BASE}body/').status_code == 303
    assert 'abm-need' in admin.get(f'{BASE}safety/?need=ack').text
    admin.post(f'{BASE}safety/complete', data={'ack': '1'})
    assert admin.get(f'{BASE}body/').status_code == 200


def test_the_confirmation_cannot_be_taken_back(admin):
    walk(admin, 'body')
    r = admin.post(f'{BASE}state', json={'key': 'safety', 'value': {'ack': False}})
    assert r.json()['saved'] is False
    assert admin.get(f'{BASE}body/').status_code == 200


def test_answers_are_stored_with_time_and_history(admin, accounts):
    walk(admin, 'techniques')
    assert admin.post(f'{BASE}state', json={'key': 'goal', 'value': 'bloating'}).json()['saved'] is True
    assert admin.post(f'{BASE}state', json={'key': 'goal', 'value': 'bloating'}).json()['saved'] is False     # unchanged
    assert admin.post(f'{BASE}state', json={'key': 'goal', 'value': course.GOALS[1]['id']}).json()['saved'] is True
    assert admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}}).json()['saved'] is True
    assert admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1', 'r2']}).json()['saved'] is True
    now = accounts.rows("SELECT key, value, updated_at FROM course_answers WHERE key IN ('goal', 'checks') ORDER BY key")
    assert len(now) == 2 and all(r['updated_at'] for r in now)
    log = accounts.rows("SELECT value, at FROM course_answer_log WHERE key = 'goal' ORDER BY id")
    assert len(log) == 2 and all(r['at'] for r in log)


def test_ticks_and_watch_time_are_stored_only_after_the_consent(admin, accounts):
    walk(admin, 'techniques')
    assert admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1']}).json()['saved'] is False
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 12, 'plays': 1, 'completed': True})
    assert accounts.rows("SELECT key FROM course_answers WHERE key = 'checks' OR key LIKE 'tech.%'") == []
    # reading and passing pages never depends on it
    assert admin.get(f'{BASE}techniques/').status_code == 200
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}})
    assert admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1']}).json()['saved'] is True
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 12, 'plays': 1, 'completed': True})
    assert len(accounts.rows("SELECT key FROM course_answers WHERE key = 'tech.m1'")) == 1
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': False}})
    assert admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1', 'r1']}).json()['saved'] is False


def test_the_safety_page_asks_for_the_consent_and_lists_the_skip_conditions(admin):
    walk(admin, 'safety')
    html = admin.get(f'{BASE}safety/').text
    assert 'data-consent' in html and 'data-consent checked' not in html
    assert 'flare-up of Crohn' in html and 'that has not been explained' in html


def test_answers_come_back_with_the_page(admin):
    walk(admin, 'safety')
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}})
    admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1']})
    admin.post(f'{BASE}safety/complete', data={'ack': '1'})
    html = admin.get(f'{BASE}safety/').text
    assert 'id="abm-state"' in html and '"checks"' in html and '"safety"' in html


@pytest.mark.parametrize('body', [{'key': 'nope', 'value': 1}, {'key': 'goal', 'value': 'zzz'}, {'key': 'list.x y', 'value': []},
                                  {'key': 'checks', 'value': ['<script>']}, {'value': 1}])
def test_unknown_answers_are_refused(admin, body):
    assert admin.post(f'{BASE}state', json=body).status_code == 400


def test_answer_endpoints_are_404_for_outsiders(anon, customer):
    for c in (anon, customer):
        assert c.post(f'{BASE}state', json={'key': 'goal', 'value': 'bloating'}).status_code == 404
        assert c.post(f'{BASE}about/complete').status_code == 404
        assert c.get(f'{BASE}state').status_code == 404


def test_cross_origin_post_is_refused(admin):
    r = admin.post(f'{BASE}about/complete', headers={'origin': 'https://evil.example'})
    assert r.status_code == 404


def test_the_reset_button_is_gone(admin):
    assert 'data-abm-reset' not in admin.get(f'{BASE}about/').text
    assert 'data-abm-reset' not in admin.get(BASE).text


def test_author_page_is_open_from_the_start(admin):
    assert admin.get(f'{BASE}author/').status_code == 200
    assert admin.post(f'{BASE}author/complete').headers['location'] == f'{BASE}about/'      # but the course is not finished by it


def test_technique_link_appears_with_the_techniques(admin):
    walk(admin, 'body')
    assert f'{BASE}techniques/#m4' not in admin.get(f'{BASE}body/').text            # the techniques are still locked
    walk(admin, 'combining')
    assert f'{BASE}techniques/#m4' in admin.get(f'{BASE}body/').text


def test_no_saved_banner_or_status_chip(admin):
    walk(admin, 'techniques')
    html = admin.get(f'{BASE}techniques/').text
    assert 'data-safe-state' not in html and 'Confirmed' not in html


# ---------------------------------------------------------------- watching techniques, statistics page

def test_watching_is_summed_and_logged(admin, accounts):
    walk(admin, 'techniques')
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}})
    assert admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 3, 'plays': 0}).json()['ok']
    assert accounts.rows("SELECT value FROM course_answers WHERE key = 'tech.m1'")[0]['value'] and True
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 4.5, 'plays': 1, 'completed': True})
    import json
    value = accounts.rows("SELECT value FROM course_answers WHERE key = 'tech.m1'")[0]['value']
    value = json.loads(value) if isinstance(value, str) else value
    assert value == {'seconds': 7.5, 'plays': 1, 'completed': True, 'viewed': True}
    assert len(accounts.rows("SELECT id FROM course_answer_log WHERE key = 'tech.m1'")) == 2


def test_nothing_is_recorded_for_a_locked_player_or_a_bad_key(admin, accounts):
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}})
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 9, 'plays': 1})          # techniques not open yet
    walk(admin, 'techniques')
    admin.post(f'{BASE}track', json={'key': 'm9', 'seconds': 9, 'plays': 1})
    admin.post(f'{BASE}track', json={'key': 'm2', 'seconds': 0})
    assert accounts.rows("SELECT id FROM course_answers WHERE key LIKE 'tech.%'") == []


def test_track_is_closed_to_outsiders(anon, customer):
    for c in (anon, customer):
        assert c.post(f'{BASE}track', json={'key': 'm1', 'seconds': 9}).status_code == 404


def test_player_page_carries_the_track_url(admin):
    walk(admin, 'techniques')
    assert f'data-track="{BASE}track"' in admin.get(f'{BASE}techniques/').text


def test_statistics_page(admin):
    walk(admin, 'techniques')
    admin.post(f'{BASE}state', json={'key': 'consent', 'value': {'ok': True}})
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 12, 'plays': 1, 'completed': True})
    admin.post(f'{BASE}state', json={'key': 'checks', 'value': ['a1']})
    r = admin.get('/admin/course/')
    assert r.status_code == 200
    assert 'Course steps' in r.text and 'course-root@example.test' in r.text and 'Techniques in the player' in r.text


def test_statistics_need_the_permission(accounts):
    accounts.add('plain-admin@example.test', role='admin')
    c = accounts.login('plain-admin@example.test')
    assert c.get('/admin/course/').status_code == 403
    assert TestClient(app, follow_redirects=False).get('/admin/course/').status_code == 303


def test_nothing_about_collecting_data_is_shown(admin):
    walk(admin, 'techniques')
    for slug in ('safety', 'techniques'):
        html = admin.get(f'{BASE}{slug}/').text
        assert 'Text version' not in html and 'Saved' not in html


def test_statistics_filters_sorting_and_live(admin, accounts):
    walk(admin, 'techniques')
    admin.post(f'{BASE}track', json={'key': 'm1', 'seconds': 12, 'plays': 1, 'completed': True})
    html = admin.get('/admin/course/').text
    assert 'data-live="course"' in html and 'data-admin-live' in html
    assert 'data-admin-filter' in html and 'name="from"' in html          # the period filter
    for sort in ('techs_sort=-viewers', 'people_sort=email', 'goals_sort=title', 'checks_sort=-n'):
        assert admin.get(f'/admin/course/?{sort}').status_code == 200
    assert admin.get('/admin/course/?people_sort=password_hash;drop').status_code == 200        # unknown sort → default
    assert 'course-root@example.test' in admin.get('/admin/course/?people_q=ROOT').text
    assert 'No one found' in admin.get('/admin/course/?people_q=nobody-here').text
    assert 'No one found' in admin.get('/admin/course/?people_status=finished').text
    assert 'course-root@example.test' in admin.get('/admin/course/?people_status=progress').text
    assert 'course-root@example.test' in admin.get('/admin/course/?people_next=combining').text
    old = admin.get('/admin/course/?from=2020-01-01&to=2020-01-31').text
    assert 'Nothing happened in the course in this period' in old


def test_every_statistics_header_is_a_sort_link(admin):
    import re
    walk(admin, 'techniques')
    html = admin.get('/admin/course/').text
    headers = re.findall(r'<th\b[^>]*>.*?</th>', html, re.DOTALL)
    assert headers
    for th in headers:
        assert 'aria-sort=' in th and 'data-admin-sort' in th, th


def test_saving_an_answer_tells_the_admin_pages(admin, monkeypatch):
    from app import live
    seen: list[str] = []
    monkeypatch.setattr(live, 'published', seen.append)
    walk(admin, 'about')
    admin.post(f'{BASE}about/complete')
    assert 'course' in seen


def test_start_page_has_a_sticky_continue_button(admin):
    html = admin.get(BASE).text
    assert 'data-cta-top' in html and 'data-cta-sticky' in html


def test_people_list_is_cursor_paginated(admin, accounts):
    import re

    from sqlalchemy import text
    for i in range(45):
        accounts.add(f'person{i:02d}@example.test', role='user')
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with accounts.engine.begin() as conn:
        for uid, in conn.execute(text("SELECT id FROM admin_users WHERE email LIKE 'person%'")).all():
            conn.execute(text("INSERT INTO course_answer_log (user_id, course, key, value, at) VALUES (:u, 'abdominal', 'done.about', 'true', :t)"), {'u': uid, 't': now})
            conn.execute(text("INSERT INTO course_answers (user_id, course, key, value, created_at, updated_at) VALUES (:u, 'abdominal', 'done.about', 'true', :t, :t)"), {'u': uid, 't': now})
    first = admin.get('/admin/course/?people_sort=email').text
    emails = re.findall(r'person\d\d@example\.test', first)
    assert emails == sorted(emails) and len(emails) == 10                      # the first page is small
    href = re.search(r'href="([^"]*people_after=[^"]+)"[^>]*data-admin-more', first).group(1).replace('&amp;', '&')
    second = admin.get('/admin/course/' + href).text
    emails2 = re.findall(r'person\d\d@example\.test', second)
    assert len(emails2) == 30 and emails2[0] > emails[-1]                      # then 30 after the cursor
    assert admin.get('/admin/course/?people_sort=email&people_after=garbage').status_code == 200


def test_player_progress_is_a_segmented_bar(admin):
    walk(admin, 'techniques')
    html = admin.get(f'{BASE}techniques/').text
    assert 'data-mp="seg"' in html and 'data-mp="scrub"' in html


def test_background_carries_the_codes_of_the_person(admin, accounts):
    from app import course_mark
    html = admin.get(BASE).text
    me = accounts.rows("SELECT id FROM admin_users WHERE email = 'course-root@example.test'")[0]['id']
    assert course_mark.code_for(me) in __import__('urllib.parse').parse.unquote(html)        # the code is in the tile of the background
    assert '--wm:' in html and 'data:image/svg+xml' in html
    assert 'course-root@example.test' not in html.split('--wm:')[1].split('>')[0]            # nothing personal in the mark itself


def test_mark_codes_are_stable_distinct_and_resolvable():
    from app import course_mark
    codes = {uid: course_mark.code_for(uid) for uid in range(1, 400)}
    assert len(set(codes.values())) == len(codes) and all(len(c) == 8 and c[3] == '-' for c in codes.values())
    assert course_mark.code_for(7) == codes[7]
    assert course_mark.resolve(codes[123].lower().replace('-', ''), range(1, 400)) == 123
    assert course_mark.resolve('AAA-AAAA', range(1, 400)) is None


def test_course_mark_bars_carry_the_same_number():
    from app import course_mark
    bits = course_mark.bits_for(5)
    assert bits.startswith(course_mark.SYNC) and len(bits) == 4 + course_mark.BARS
    assert bits != course_mark.bits_for(6)
    css = __import__('urllib.parse').parse.unquote(course_mark.css_background(5))
    assert css.count('<rect') == bits.count('1')                       # one bar per 1


def test_finished_course_does_not_offer_to_continue(admin):
    assert 'Course complete' not in admin.get(BASE).text
    walk(admin)
    html = admin.get(BASE).text
    assert 'Course complete' in html and 'data-cta-top' not in html and 'data-cta-sticky' not in html
    assert 'Continue' not in html and 'Next up:' not in html


# ---------------------------------------------------------------- the English edition

def test_the_product_page_gets_what_it_needs():
    assert course.BASE == '/course/abdominal-massage/' and len(course.TECHNIQUES) == 8
    assert list(course.GROUPS) and {p.group for p in course.PUBLIC_PAGES} <= set(range(len(course.GROUPS)))
    assert all(p.title and p.nav and p.promise for p in course.PUBLIC_PAGES)


def test_all_course_text_is_english():
    import re
    import urllib.parse
    cyrillic = re.compile('[\u0400-\u04ff]')
    assert not cyrillic.search(course.DATA_FILE.read_text(encoding='utf-8'))
    assert not cyrillic.search(repr((course.PAGES, course.ANATOMY, course.GOALS, course.FOOD_LISTS, course.WATER, course.DRINKS, course.FAQ,
                                     course.STOP_RULES, course.AUTHOR)))
    assert not cyrillic.search(urllib.parse.unquote(repr(course.GROUPS)))


def test_every_page_renders_in_english(admin):
    walk(admin, 'author')
    for slug in PAGES:
        html = admin.get(f'{BASE}{slug}/').text
        assert '<html lang="en"' in html
        assert not __import__('re').search('[\u0400-\u04ff]', html), slug
    assert 'dorinellla' not in admin.get(f'{BASE}techniques/').text


def test_the_safety_page_lists_the_contraindications(admin):
    walk(admin, 'safety')
    html = admin.get(f'{BASE}safety/').text
    for item_id in ('a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'r1', 'r2', 'r3', 'r4', 'r5'):
        assert f'data-abs="{item_id}"' in html or f'data-rel="{item_id}"' in html
    assert 'not medical advice' in html and course.SAFETY_VERSION in html


def test_paid_mode_lets_a_buyer_in_and_nobody_else(monkeypatch, customer, admin):
    from app import payments
    monkeypatch.setattr(course, 'access_mode', lambda: 'paid')
    assert customer.get(f'{BASE}about/').status_code == 404          # the placeholder payments module: nobody bought it

    async def bought(session, user, slug=''):
        return slug == course.PRODUCT
    monkeypatch.setattr(payments, 'has_entitlement', bought)
    assert customer.get(f'{BASE}about/').status_code == 200
    assert customer.get(f'{BASE}safety/').status_code == 303        # a buyer walks the pages in order like everybody else
    assert customer.post(f'{BASE}about/complete').status_code == 303


def test_the_product_slug_is_in_the_catalog():
    from app import catalog
    assert course.PRODUCT in catalog.PRODUCTS


def test_no_leftovers_of_removed_features(admin):
    walk(admin, 'techniques')
    for html in (admin.get(BASE).text, admin.get(f'{BASE}techniques/').text):
        for word in ('Portmone', 'Telegram', 't.me/', 'review'):
            assert word not in html, word
