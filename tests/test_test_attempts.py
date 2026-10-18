"""Every completed Bristol test is saved anonymously with all its answers, under the visitor's id and the test's version."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import test_attempts, visitors
from app.learn import BRISTOL
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_limits():
    test_attempts._recent.clear()
    test_attempts._hourly.clear()


def _last(sync_db):
    with sync_db.connect() as conn:
        return conn.execute(text('SELECT visitor_id, test_version, answers, score, band_key, source, device '
                                 'FROM test_attempts WHERE test_slug = :s ORDER BY created_at DESC, id DESC LIMIT 1'),
                            {'s': BRISTOL.slug}).mappings().first()


def _count(sync_db):
    with sync_db.connect() as conn:
        return conn.execute(text('SELECT count(*) FROM test_attempts WHERE test_slug = :s'), {'s': BRISTOL.slug}).scalar()


def _visitor(browser):
    browser.get('/tests/bristol/')                 # the page gives the id, the submit brings it back
    return visitors.decode(browser.cookies.get(visitors.COOKIE)).id


def _post(browser, answers, source, consent=True):
    if source == 'api':
        return browser.post('/api/v1/tests/bristol/evaluate', json={'answers': answers, 'consent': consent})
    headers = {'sec-fetch-mode': 'navigate' if source == 'form' else 'cors'}
    data = {f'q{i}': a for i, a in enumerate(answers)} | ({'consent': '1'} if consent else {})
    return browser.post('/tests/bristol/', data=data, headers=headers)


@pytest.mark.parametrize('source', ['form', 'js', 'api'])
def test_attempt_is_saved_with_answers_result_version_and_visitor(sync_db, source):
    browser = TestClient(app)
    vid = _visitor(browser)
    answers = [3, 1, 0]
    assert _post(browser, answers, source).status_code == 200
    row = _last(sync_db)
    stored = row['answers'] if isinstance(row['answers'], list) else json.loads(row['answers'])
    assert stored == answers and row['source'] == source and row['device'] == 'desktop'
    assert str(row['visitor_id']).replace('-', '') == vid.hex
    assert row['test_version'] == test_attempts.version(BRISTOL)
    assert row['score'] == 4 and row['band_key'] == 'type-4'
    with sync_db.connect() as conn:
        snap = conn.execute(text('SELECT snapshot FROM test_versions WHERE slug = :s AND version = :v'),
                            {'s': BRISTOL.slug, 'v': row['test_version']}).scalar()
        assert conn.execute(text('SELECT 1 FROM visitors WHERE id = :id'), {'id': vid.hex}).first()
    snap = snap if isinstance(snap, dict) else json.loads(snap)
    assert [q['text'] for q in snap['questions']] == [q.text for q in BRISTOL.questions]


@pytest.mark.parametrize('source', ['form', 'js', 'api'])
def test_nothing_is_saved_without_the_consent_but_the_result_is_shown(sync_db, source):
    browser = TestClient(app)
    _visitor(browser)
    before = _count(sync_db)
    response = _post(browser, [2, 2, 0], source, consent=False)
    assert response.status_code == 200
    assert _count(sync_db) == before
    if source != 'api':
        assert 'data-result' in response.text and 'Nothing was saved' in response.text


def test_the_form_has_an_unticked_consent_box_and_the_result_says_what_was_done():
    page = TestClient(app).get('/tests/bristol/').text
    assert 'name="consent"' in page and 'name="consent" value="1" checked' not in page and 'required' not in page.split('name="consent"')[1].split('>')[0]
    saved = TestClient(app).post('/tests/bristol/', data={'q0': 3, 'q1': 1, 'q2': 0, 'consent': '1'}).text
    assert 'stored anonymously, as you agreed' in saved


def test_a_double_submit_is_one_attempt_but_a_retake_is_another(sync_db):
    browser = TestClient(app)
    _visitor(browser)
    before = _count(sync_db)
    _post(browser, [2, 1, 0], 'js')
    _post(browser, [2, 1, 0], 'js')
    assert _count(sync_db) == before + 1
    _post(browser, [5, 1, 0], 'js')
    assert _count(sync_db) == before + 2


def test_invalid_answers_bots_and_flooding_are_not_saved(sync_db, monkeypatch):
    browser = TestClient(app)
    _visitor(browser)
    before = _count(sync_db)
    assert _post(browser, [0], 'api').status_code == 422
    assert _post(browser, [9, 0, 0], 'api').status_code == 422
    TestClient(app, headers={'user-agent': 'Googlebot/2.1'}).post('/tests/bristol/', data={'q0': 3, 'q1': 1, 'q2': 0, 'consent': '1'})
    assert _count(sync_db) == before
    monkeypatch.setattr(test_attempts, 'HOURLY_LIMIT', 2)
    for n in range(4):
        _post(browser, [n % 7, 1, 0], 'js')
        test_attempts._recent.clear()
    assert _count(sync_db) == before + 2


def test_a_client_without_cookies_is_saved_without_an_id(sync_db):
    TestClient(app).post('/tests/bristol/', data={'q0': 3, 'q1': 1, 'q2': 0, 'consent': '1'})
    assert _last(sync_db)['visitor_id'] is None


def test_saving_failure_still_shows_the_result(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('db down')
    monkeypatch.setattr(test_attempts, 'version', broken)
    response = TestClient(app).post('/tests/bristol/', data={'q0': 3, 'q1': 1, 'q2': 0, 'consent': '1'})
    assert response.status_code == 200 and 'data-result' in response.text


# ---------------------------------------------------------------- admin Tests

def test_admin_tests_pages_show_counts_and_answers(accounts):
    browser = TestClient(app)
    _visitor(browser)
    _post(browser, [1, 2, 0], 'js')

    accounts.add('tests@example.com', 'admin', groups=[accounts.group('Analysts', ['tests.view'])])
    admin = accounts.login('tests@example.com')
    overview = admin.get('/admin/tests/')
    assert overview.status_code == 200 and '/admin/tests/bristol/' in overview.text
    page = admin.get('/admin/tests/bristol/')
    assert page.status_code == 200 and BRISTOL.questions[0].text in page.text
    assert admin.get('/admin/tests/nope/').status_code == 404


def test_admin_tests_speak_russian_when_asked(accounts):
    accounts.add('tests@example.com', 'admin', groups=[accounts.group('Analysts', ['tests.view'])])
    admin = accounts.login('tests@example.com')
    admin.cookies.set('admin_lang', 'ru')
    page = admin.get('/admin/tests/bristol/')
    assert 'Какое описание подходит' in page.text or 'Результаты' in page.text or 'Тесты' in page.text


def test_admin_tests_refuse_without_the_right(accounts):
    accounts.add('nobody@example.com', 'admin', groups=[accounts.group('Others', ['analytics.view'])])
    admin = accounts.login('nobody@example.com')
    for path in ('/admin/tests/', '/admin/tests/bristol/'):
        assert admin.get(path).status_code == 403
