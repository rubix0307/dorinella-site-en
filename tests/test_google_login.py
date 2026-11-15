"""The only way into the admin: a Google account whose address is already an admin's. Google itself is replaced by a stub."""
import base64
import dataclasses
import json
import time
import types
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import auth, google_login
from app.main import app
from tests.helpers import sign_in

CLIENT_ID = 'test-client.apps.googleusercontent.com'


@pytest.fixture(autouse=True)
def google(monkeypatch):
    monkeypatch.setattr(google_login, 'settings', dataclasses.replace(
        google_login.settings, google_client_id=CLIENT_ID, google_client_secret='test-secret'))


def _jwt(claims: dict) -> str:
    part = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()  # noqa: E731
    return f'{part({"alg": "RS256"})}.{part(claims)}.sig'


def _browser():
    return TestClient(app, follow_redirects=False)


def _start(browser):
    """Press the button: the browser is sent to Google; returns what it carried (state, nonce)."""
    reply = browser.get('/admin/login/google')
    assert reply.status_code == 303
    query = parse_qs(urlparse(reply.headers['location']).query)
    return reply, {k: v[0] for k, v in query.items()}


def _answer(monkeypatch, nonce, /, **claims):
    """Google's token endpoint answers with an ID token."""
    body = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'exp': int(time.time()) + 600, 'nonce': nonce,
            'email_verified': True, **claims}
    monkeypatch.setattr(google_login, '_post_form', lambda url, data: {'id_token': _jwt(body)})


def _finish(browser, query, code='the-code'):
    return browser.get('/admin/login/google/callback', params={'code': code, 'state': query['state']})


def test_the_button_is_shown_only_when_google_is_set_up(monkeypatch):
    assert '/admin/login/google' in _browser().get('/login').text
    monkeypatch.setattr(google_login, 'settings', dataclasses.replace(
        google_login.settings, google_client_id='', google_client_secret=''))
    page = _browser().get('/login')
    assert page.status_code == 200 and '/admin/login/google"' not in page.text
    assert _browser().get('/admin/login/google').status_code == 503


def test_the_login_has_no_password_form():
    assert _browser().get('/admin/login').headers['location'] == '/login'          # the old address goes on
    page = _browser().get('/login')
    assert 'type="password"' not in page.text
    assert _browser().post('/admin/login', data={'email': 'a@b.co', 'password': 'x' * 20}).status_code == 405


def test_the_button_sends_the_browser_to_google_with_a_pkce_challenge():
    reply, query = _start(_browser())
    assert reply.headers['location'].startswith(google_login.AUTH_URL)
    assert query['client_id'] == CLIENT_ID and query['response_type'] == 'code' and 'email' in query['scope']
    assert query['redirect_uri'].endswith('/admin/login/google/callback')
    assert query['code_challenge_method'] == 'S256' and query['state'] and query['nonce']
    cookie = reply.headers['set-cookie']
    assert google_login.FLOW_COOKIE in cookie and 'HttpOnly' in cookie


def test_an_existing_admin_signs_in_whatever_the_case_of_the_address(accounts, monkeypatch):
    uid = accounts.add('Boss@Example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com')
    reply = _finish(browser, query)
    assert reply.status_code == 303 and reply.headers['location'] == '/admin/'
    assert auth.SESSION_COOKIE in reply.headers['set-cookie']
    assert browser.get('/admin/').status_code == 200
    assert accounts.rows('SELECT last_login_at FROM admin_users WHERE id = :i', i=uid)[0]['last_login_at'] is not None


def test_a_new_google_account_gets_a_plain_account_that_opens_nothing_in_the_admin(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='Newcomer@Example.com')
    reply = _finish(browser, query)
    assert reply.status_code == 303 and reply.headers['location'] == '/'          # no consent screen: a plain account goes to the main page
    assert auth.SESSION_COOKIE in reply.headers['set-cookie']
    made = accounts.rows("SELECT role, is_active, password_hash FROM admin_users WHERE email = 'newcomer@example.com'")
    assert made == [{'role': 'user', 'is_active': 1, 'password_hash': auth.NO_PASSWORD}]
    cabinet = browser.get('/account/')
    assert cabinet.status_code == 200 and 'href="/admin/"' not in cabinet.text
    assert browser.get('/admin/users/').status_code == 403                     # the admin does not let it in
    assert browser.get('/admin/tests/').status_code == 403
    assert browser.get('/login').headers['location'] == '/account/'
    assert _browser().get('/account/').headers['location'] == '/login'          # the cabinet wants a sign-in


def test_the_same_google_account_twice_is_one_account(accounts, monkeypatch):
    for _ in range(2):
        browser = _browser()
        _, query = _start(browser)
        _answer(monkeypatch, query['nonce'], email='twice@example.com')
        assert _finish(browser, query).status_code == 303
    assert len(accounts.rows("SELECT id FROM admin_users WHERE email = 'twice@example.com'")) == 1


def test_an_account_given_admin_rights_opens_the_admin(accounts, monkeypatch):
    accounts.add('chef@example.com', 'admin', groups=[accounts.full])
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='chef@example.com')
    assert _finish(browser, query).headers['location'] == '/admin/'
    assert browser.get('/admin/').status_code == 200


def test_a_switched_off_account_cannot_sign_in(accounts, monkeypatch):
    accounts.add('off@example.com', 'root', active=0)
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='off@example.com')
    reply = _finish(browser, query)
    assert reply.status_code == 403 and auth.SESSION_COOKIE not in reply.headers.get('set-cookie', '')


def test_an_unverified_address_is_refused(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com', email_verified=False)
    assert _finish(browser, query).status_code == 401


@pytest.mark.parametrize('change', [{'aud': 'someone-elses-client'}, {'iss': 'https://evil.example'},
                                    {'exp': 1}, {'nonce': 'not-the-one'}])
def test_a_token_that_was_not_made_for_us_is_refused(accounts, monkeypatch, change):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com', **change)
    reply = _finish(browser, query)
    assert reply.status_code == 401 and auth.SESSION_COOKIE not in reply.headers.get('set-cookie', '')


def test_a_callback_this_browser_did_not_start_is_refused(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    _, query = _start(_browser())
    _answer(monkeypatch, query['nonce'], email='boss@example.com')
    assert _finish(_browser(), query).status_code == 401                     # no flow cookie: someone else's callback
    browser = _browser()
    _start(browser)
    assert browser.get('/admin/login/google/callback', params={'code': 'x', 'state': 'forged'}).status_code == 401


def test_google_saying_no_is_not_a_sign_in(accounts):
    browser = _browser()
    _start(browser)
    reply = browser.get('/admin/login/google/callback', params={'error': 'access_denied'})
    assert reply.status_code == 401 and auth.SESSION_COOKIE not in reply.headers.get('set-cookie', '')


def test_the_flow_cookie_is_used_once(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com')
    assert _finish(browser, query).status_code == 303
    assert google_login.FLOW_COOKIE not in browser.cookies or not browser.cookies.get(google_login.FLOW_COOKIE)


def _tokeninfo(monkeypatch, **claims):
    """Google's tokeninfo answers (it already checked the token's signature); its values are strings."""
    body = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'exp': str(int(time.time()) + 600),
            'email_verified': 'true', **claims}
    monkeypatch.setattr(google_login, '_get_json', lambda url: body)


def _one_tap(browser, credential='a-token', **headers):
    headers = {'origin': 'http://testserver', **headers}
    return browser.post('/auth/google/one-tap', json={'credential': credential}, headers=headers)


def test_one_tap_signs_the_browser_in_and_makes_a_plain_account(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    _tokeninfo(monkeypatch, email='Tapper@Example.com')
    browser = _browser()
    reply = _one_tap(browser)
    assert reply.status_code == 200 and reply.json() == {'ok': True} and auth.SESSION_COOKIE in reply.headers['set-cookie']
    assert accounts.rows("SELECT role FROM admin_users WHERE email = 'tapper@example.com'") == [{'role': 'user'}]
    assert browser.get('/account/').status_code == 200 and browser.get('/admin/users/').status_code == 403


def test_one_tap_for_an_existing_admin_opens_the_admin(accounts, monkeypatch):
    accounts.add('chef@example.com', 'admin', groups=[accounts.full])
    _tokeninfo(monkeypatch, email='chef@example.com')
    browser = _browser()
    assert _one_tap(browser).status_code == 200
    assert browser.get('/admin/').status_code == 200


def test_one_tap_refuses_a_foreign_page_and_a_bad_token(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    _tokeninfo(monkeypatch, email='boss@example.com')
    browser = _browser()
    assert _one_tap(browser, origin='https://evil.example').status_code == 403
    assert browser.post('/auth/google/one-tap', json={'credential': 'x'}).status_code == 403      # no Origin at all
    assert auth.SESSION_COOKIE not in browser.cookies
    for change in ({'aud': 'other'}, {'iss': 'https://evil.example'}, {'exp': '1'}, {'email_verified': 'false'}):
        _tokeninfo(monkeypatch, email='boss@example.com', **change)
        assert _one_tap(_browser()).status_code == 401
    assert _one_tap(_browser(), credential='').status_code == 401
    assert _browser().post('/auth/google/one-tap', content=b'not json', headers={'origin': 'http://testserver'}).status_code == 401


def test_google_script_loads_on_public_pages_for_a_signed_out_browser_but_not_on_the_excluded_ones(accounts):
    for path in ('/login', '/', '/links/', '/guides/gut-basics/', '/tests/bristol/'):
        page = _browser().get(path).text
        assert 'accounts.google.com/gsi/client' in page and CLIENT_ID in page, path
    for path in ('/privacy', '/terms', '/refund'):
        assert 'accounts.google.com/gsi/client' not in _browser().get(path).text, path
    signed_in = _browser()
    signed_in.cookies.set(auth.SESSION_COOKIE, 'x.y.z')
    assert 'accounts.google.com/gsi/client' not in signed_in.get('/login').text and 'accounts.google.com/gsi/client' not in signed_in.get('/').text


def _flow_cookie_cleared(reply):
    header = reply.headers.get('set-cookie', '')
    return f'{google_login.FLOW_COOKIE}=""' in header and 'Max-Age=0' in header


def test_the_flow_cookie_is_cleared_after_a_sign_in(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com')
    assert _flow_cookie_cleared(_finish(browser, query))


def test_a_broken_callback_is_refused_before_google_is_asked(accounts, monkeypatch):
    def never(url, data):
        raise AssertionError('the token endpoint must not be called')
    monkeypatch.setattr(google_login, '_post_form', never)
    browser = _browser()
    _, query = _start(browser)
    callback = '/admin/login/google/callback'
    assert browser.get(callback, params={'code': 'x', 'state': 'forged'}).status_code == 401
    assert browser.get(callback, params={'state': query['state']}).status_code == 401           # no code
    assert browser.get(callback, params={'code': 'x'}).status_code == 401                       # no state
    assert browser.get(callback, params={'error': 'access_denied', 'code': 'x', 'state': query['state']}).status_code == 401


@pytest.mark.parametrize('cookie', ['abc', 'a.b.c', '.', 'e30.' + 'f' * 64])
def test_a_forged_flow_cookie_is_refused(accounts, cookie):
    browser = _browser()
    browser.cookies.set(google_login.FLOW_COOKIE, cookie)
    assert browser.get('/admin/login/google/callback', params={'code': 'x', 'state': 's'}).status_code == 401


def test_an_expired_flow_cookie_is_refused(accounts, monkeypatch):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com', exp=int(time.time()) + 10_000)
    later = time.time() + google_login.FLOW_TTL + 5
    monkeypatch.setattr(google_login, 'time', types.SimpleNamespace(time=lambda: later))
    assert _finish(browser, query).status_code == 401


def test_a_token_endpoint_without_an_id_token_is_refused(accounts, monkeypatch):
    browser = _browser()
    _, query = _start(browser)
    monkeypatch.setattr(google_login, '_post_form', lambda url, data: {'error': 'invalid_grant'})
    assert _finish(browser, query).status_code == 401


def test_one_tap_does_not_sign_in_a_switched_off_account(accounts, monkeypatch):
    accounts.add('off@example.com', 'root', active=0)
    _tokeninfo(monkeypatch, email='off@example.com')
    reply = _one_tap(_browser())
    assert reply.status_code == 403 and auth.SESSION_COOKIE not in reply.headers.get('set-cookie', '')


def test_one_tap_finds_an_existing_account_whatever_the_case(accounts, monkeypatch):
    accounts.add('chef@example.com', 'admin', groups=[accounts.full])
    _tokeninfo(monkeypatch, email='Chef@Example.COM')
    browser = _browser()
    assert _one_tap(browser).status_code == 200
    assert accounts.rows('SELECT count(*) AS n FROM admin_users') == [{'n': 1}]
    assert browser.get('/admin/').status_code == 200


def test_signing_out_ends_a_copied_cookie_too(accounts):
    accounts.add('boss@example.com', 'root')
    browser = _browser()
    sign_in(browser, 'boss@example.com')
    copy = browser.cookies.get(auth.SESSION_COOKIE)
    assert browser.get('/admin/').status_code == 200
    assert browser.post('/admin/logout', headers={'origin': 'http://testserver'}).headers['location'] == '/'
    thief = _browser()
    thief.cookies.set(auth.SESSION_COOKIE, copy)
    assert thief.get('/admin/').status_code == 303


def test_a_secret_key_is_required_on_an_https_site():
    import os
    import subprocess
    import sys
    env = {**os.environ, 'SITE_URL': 'https://example.com'}
    env.pop('SECRET_KEY', None)
    run = subprocess.run([sys.executable, '-c', 'import app.core.config'], env=env, capture_output=True, check=False)
    assert run.returncode != 0
    assert b'SECRET_KEY' in run.stderr
    for weak in ('x', 'change-me', 'k' * 31):
        assert subprocess.run([sys.executable, '-c', 'import app.core.config'], env={**env, 'SECRET_KEY': weak},
                              capture_output=True, check=False).returncode != 0
    assert subprocess.run([sys.executable, '-c', 'import app.core.config'], env={**env, 'SECRET_KEY': 'k' * 32},
                          capture_output=True, check=False).returncode == 0
    # a real database is production too, whatever SITE_URL says
    prod = {**os.environ, 'SITE_URL': '', 'DATABASE_URL': 'postgresql+asyncpg://x:x@127.0.0.1:1/t', 'SECRET_KEY': 'change-me'}
    assert subprocess.run([sys.executable, '-c', 'import app.core.config'], env=prod, capture_output=True, check=False).returncode != 0


def test_the_google_name_is_asked_for_and_kept(accounts, monkeypatch):
    accounts.add('old@example.com', 'user')
    browser = _browser()
    _url, query = _start(browser)
    assert 'profile' in query['scope'].split()
    _answer(monkeypatch, query['nonce'], email='new@example.com', name='  Maria   Smith ')
    assert _finish(browser, query).status_code == 303
    assert accounts.rows("SELECT name FROM admin_users WHERE email = 'new@example.com'") == [{'name': 'Maria Smith'}]
    other = _browser()                       # an account made before names existed gets one at its next sign-in
    _, query = _start(other)
    _answer(monkeypatch, query['nonce'], email='old@example.com', name='Irene')
    _finish(other, query)
    assert accounts.rows("SELECT name FROM admin_users WHERE email = 'old@example.com'") == [{'name': 'Irene'}]


def test_the_sign_in_page_shows_google_errors_in_english(accounts, monkeypatch):
    """The sign-in page is public, so its messages are English only (no admin translation)."""
    browser = _browser()
    _, query = _start(browser)
    _answer(monkeypatch, query['nonce'], email='boss@example.com', email_verified=False)
    reply = _finish(browser, query)
    assert reply.status_code == 401 and 'The e-mail of this Google account is not verified.' in reply.text
