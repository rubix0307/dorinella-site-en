"""The public shell: the landing, the products, the cabinet, sign-in and the error pages are English, complete and honest about what is not for sale yet."""
import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from app import catalog, google_login, payments
from app.main import app

CYRILLIC = re.compile('[А-Яа-яІіЇїЄєҐґЁё]')
PUBLIC = ['/', '/products/', '/products/gut-massage-guide/', '/products/massage-course/', '/links/', '/login']


@pytest.fixture(scope='module')
def client(sync_db):
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize('path', PUBLIC)
def test_a_public_page_is_english_and_has_one_h1(client, path):
    response = client.get(path)
    assert response.status_code == 200, path
    assert not CYRILLIC.search(response.text), path
    assert '<html lang="en"' in response.text and len(re.findall(r'<h1[ >]', response.text)) == 1


def test_the_landing_sells_the_guide_and_has_its_sections(client):
    html = client.get('/').text
    for part in ('id="inside"', 'id="products"', 'id="faq"', 'id="about"', '/products/gut-massage-guide/', '/tests/bristol/', '/guides/'):
        assert part in html, part
    assert 'Lola Krissss' in html and 'Not on sale' not in html          # the buy state lives on the product page
    for gone in ('Telegram', 'telegram', 't.me', 'Portmone', 'dorinellla', 'diploma'):
        assert gone not in html, gone


def test_the_navigation_has_no_leftovers_of_the_ukrainian_site(client, monkeypatch):
    monkeypatch.setattr(google_login, 'settings', dataclasses.replace(google_login.settings, google_client_id='id', google_client_secret='secret'))
    html = client.get('/').text
    nav = re.search(r'<footer.*?</footer>', html, re.DOTALL).group(0)
    assert 'Privacy' in nav and 'Terms' in nav and 'Sign in' in nav
    for gone in ('/cooking/', '/learn/', 'recipes', 'Articles', 'newsletter'):
        assert gone not in html, gone


def test_the_sign_in_links_are_hidden_while_google_is_not_set_up(client, monkeypatch):
    unset = dataclasses.replace(google_login.settings, google_client_id='', google_client_secret='')
    monkeypatch.setattr(google_login, 'settings', unset)
    assert '>Sign in<' not in client.get('/').text
    page = client.get('/login').text
    assert 'Sign-in is not available yet' in page and 'not set up' not in page
    monkeypatch.setattr(google_login, 'settings', dataclasses.replace(unset, google_client_id='id', google_client_secret='secret'))
    assert '>Sign in<' in client.get('/').text


def test_the_guide_buttons_are_honest_while_it_is_not_on_sale(client, monkeypatch):
    html = client.get('/').text
    assert 'Get the guide' not in html and html.count('About the guide') >= 3
    monkeypatch.setitem(catalog.PRODUCTS, 'gut-massage-guide', dataclasses.replace(catalog.PRODUCTS['gut-massage-guide'], on_sale=True))
    html = client.get('/').text
    assert html.count('Get the guide') >= 3 and 'About the guide' not in html


def test_favicon_ico_and_the_api_docs(client):
    icon = client.get('/favicon.ico')
    assert icon.status_code == 200 and icon.headers['content-type'].startswith('image/svg+xml') and 'max-age=2592000' in icon.headers['cache-control']
    assert client.get('/api/docs').status_code == 404 and client.get('/api/openapi.json').status_code == 404


def test_a_product_that_is_not_on_sale_shows_coming_soon_and_no_price(client):
    for slug in ('gut-massage-guide', 'massage-course'):
        html = client.get(f'/products/{slug}/').text
        assert 'Coming soon' in html and 'aria-disabled="true"' in html, slug
        assert '$' not in html and 'name="agree"' not in html, slug


def test_an_open_sale_asks_a_visitor_to_sign_in_first(client, monkeypatch):
    monkeypatch.setattr(payments, 'sales_open', lambda slug='': True)
    html = client.get('/products/gut-massage-guide/').text
    assert '$19.00' in html and 'Sign in to buy' in html and 'href="/login"' in html


def test_a_signed_in_buyer_gets_the_checkout_form_with_the_terms_box(accounts, monkeypatch):
    accounts.add('buyer@example.com')
    buyer = accounts.login('buyer@example.com')

    async def offer(session, user, slug=''):
        return {'slug': slug, 'price': '$19.00', 'bought': False, 'ready': True, 'test': False}
    monkeypatch.setattr(payments, 'offer_for', offer)
    monkeypatch.setattr(payments, 'sales_open', lambda slug='': True)
    page = buyer.get('/products/gut-massage-guide/')
    assert 'action="/checkout/gut-massage-guide"' in page.text and 'name="agree"' in page.text and 'Buy for $19.00' in page.text
    assert page.headers['cache-control'] == 'private, no-store'           # the card differs per account


def test_an_owner_is_sent_to_the_product_not_to_the_checkout(accounts, monkeypatch):
    accounts.add('owner@example.com')
    owner = accounts.login('owner@example.com')

    async def offer(session, user, slug=''):
        return {'slug': slug, 'price': '$19.00', 'bought': True, 'ready': True, 'test': False}
    monkeypatch.setattr(payments, 'offer_for', offer)
    page = owner.get('/products/gut-massage-guide/')
    assert 'You already own this' in page.text and 'href="/guides/"' in page.text and 'name="agree"' not in page.text


def test_the_course_page_lists_the_program_from_the_course_data(client):
    from app import course
    html = client.get('/products/massage-course/').text
    assert all(group in html for group in course.GROUPS)
    assert all(page.title.split('&')[0][:20] in html for page in course.PUBLIC_PAGES[:3])


def test_an_unknown_or_hidden_product_is_a_404(client):
    assert client.get('/products/nope/').status_code == 404
    page = client.get('/no-such-page/')
    assert page.status_code == 404 and 'Error 404' in page.text and not CYRILLIC.search(page.text)


def test_a_failure_shows_the_error_page_in_english(monkeypatch):
    from app.routers import pages

    def boom(*args, **kwargs):
        raise RuntimeError('boom')
    monkeypatch.setattr(pages, 'render', boom)
    page = TestClient(app, raise_server_exceptions=False).get('/')
    assert page.status_code == 500 and 'Something went wrong' in page.text and not CYRILLIC.search(page.text)
    assert page.headers['x-robots-tag'].startswith('noindex')
    assert page.headers['x-frame-options'] == 'DENY' and 'frame-ancestors' in page.headers['content-security-policy']    # the 500 page is built outside the middleware


def test_the_csp_lets_the_editors_preview_a_chosen_photo(client):
    csp = client.get('/').headers['content-security-policy']
    assert 'img-src' in csp and 'blob:' in csp.split('img-src')[1].split(';')[0]
    assert 'wss://testserver' in csp                                                          # the admin live socket, named for old Safari


def test_login_is_not_indexed_and_says_what_it_needs(client):
    page = client.get('/login')
    assert page.headers['x-robots-tag'].startswith('noindex') and page.headers['cache-control'] == 'no-store'
    assert 'Sign in' in page.text


def test_the_cabinet_needs_a_sign_in(client):
    reply = client.get('/account/')
    assert reply.status_code == 303 and reply.headers['location'] == '/login'


def test_the_cabinet_lists_the_products_and_the_way_out(accounts):
    accounts.add('reader@example.com')
    page = accounts.login('reader@example.com').get('/account/')
    assert page.status_code == 200 and 'reader@example.com' in page.text and 'Sign out' in page.text
    assert 'Gut Massage Guide' in page.text and 'Open the admin' not in page.text
    assert 'noindex' in page.text and not CYRILLIC.search(page.text)


def test_an_admin_sees_the_admin_link_and_may_open_every_product(accounts):
    accounts.add('boss@example.com', 'root')
    page = accounts.login('boss@example.com').get('/account/')
    assert 'Open the admin' in page.text and 'Admin preview' in page.text and 'href="/guides/"' in page.text


def test_a_buyer_opens_what_they_bought_from_the_cabinet(accounts, monkeypatch):
    accounts.add('fan@example.com')
    fan = accounts.login('fan@example.com')

    async def bought(session, user, slug=''):
        return slug == 'gut-massage-guide'
    monkeypatch.setattr(payments, 'has_entitlement', bought)
    page = fan.get('/account/').text
    assert 'In your account' in page and page.count('>Open<') == 1 and 'Coming soon' in page


def test_every_page_carries_a_content_security_policy_with_a_fresh_nonce():
    client = TestClient(app)
    import re
    first, second = client.get('/'), client.get('/')
    policy = first.headers['content-security-policy']
    nonce = re.search(r"'nonce-([\w-]+)'", policy).group(1)
    assert "script-src 'self'" in policy and "object-src 'none'" in policy and "'unsafe-inline'" not in policy.split('style-src')[0]
    assert f'nonce="{nonce}"' in first.text                                         # the import map is the page's only inline script
    assert nonce not in second.headers['content-security-policy']
    assert not re.search(r'<script(?![^>]*(src=|type="application/(json|ld\+json)"|nonce=))', first.text)
    assert ' onload=' not in first.text


def test_a_huge_answer_list_is_refused_before_it_is_checked():
    reply = TestClient(app).post('/api/v1/tests/any/evaluate', json={'answers': [0] * 5000})
    assert reply.status_code == 422


def test_no_price_is_published_by_the_api_before_the_sales_open(client):
    assert client.get('/api/v1/products/gut-massage-guide/plans').json() == []
    assert client.get('/api/v1/products/gut-massage-guide').json()['plans'] == []


@pytest.mark.parametrize('path', ['/', '/guides/', '/health', '/robots.txt'])
def test_a_head_probe_gets_the_status_and_headers_of_the_page(client, path):
    """Uptime monitors and link previews send HEAD: it must not read as a 405."""
    got, head = client.get(path), client.head(path)
    assert head.status_code == got.status_code == 200
    assert head.headers['content-type'] == got.headers['content-type']


def test_the_sentence_of_stop_signs_copes_with_any_count():
    from app.safety import sentence
    assert (sentence(()), sentence(('a',)), sentence(('a', 'b', 'c'))) == ('', 'a', 'a, b or c')


def test_the_missing_page_is_not_indexed_and_has_no_canonical():
    page = TestClient(app).get('/no-such-page-at-all').text
    assert 'name="robots" content="noindex"' in page and 'rel="canonical"' not in page


def test_the_course_has_its_own_share_image():
    page = TestClient(app).get('/products/massage-course/').text
    assert 'og-course.jpg' in page and 'og-gut-guide.jpg' not in page
