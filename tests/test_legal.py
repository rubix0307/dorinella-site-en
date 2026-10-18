"""The legal pages: English only, the seller's placeholders are printed (so none can be forgotten), the cookies listed are the ones the code sets, and the checkout points at the texts."""
import dataclasses
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, catalog, google_login, legal, payments, visitors
from app.main import app

pytest_plugins = ['tests.stripe_fake']          # the fixtures `stripe` and `people`

PAGES = ('/privacy/', '/terms/', '/refund/')
APP = Path(__file__).resolve().parent.parent / 'app'


@pytest.fixture
def client(sync_db):
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize('path', PAGES)
def test_each_page_is_english_and_dated(client, path):
    page = client.get(path)
    assert page.status_code == 200 and '<html lang="en"' in page.text
    assert not re.search('[Ѐ-ӿ]', page.text), 'a Cyrillic letter on a public page'
    assert f'Last updated {legal.UPDATED}' in page.text


@pytest.mark.parametrize('path', PAGES)
def test_the_placeholders_of_the_seller_are_printed_not_hidden(client, path):
    page = client.get(path).text
    assert legal.SELLER.name in page and legal.SELLER.address in page
    assert 'direct message on Instagram' in page


def test_the_owner_can_see_which_placeholders_are_left():
    assert set(legal.SELLER.placeholders) == {'name', 'address', 'governing_law', 'email'}
    filled = dataclasses.replace(legal.SELLER, name='Jane Doe LLC', address='1 Main St, Springfield', governing_law='the State of Delaware', email='hi@example.test')
    assert filled.placeholders == []


def test_the_privacy_page_lists_exactly_the_cookies_the_code_sets(client):
    page = client.get('/privacy/').text
    for name in (visitors.COOKIE, auth.SESSION_COOKIE, google_login.FLOW_COOKIE):
        assert f'<code>{name}</code>' in page, name
    # every cookie the Python code sets is one of the listed (a new cookie must be added to the page's list in app/legal.py)
    listed = {'visitors.COOKIE', 'SESSION_COOKIE', 'google_login.FLOW_COOKIE'}
    staff_only = {'i18n.COOKIE'}                      # the admin's language switch: set for signed-in staff, named in one line of the page
    for path in APP.rglob('*.py'):
        for match in re.finditer(r'\.set_cookie\(\s*([\w.]+)', path.read_text(encoding='utf-8')):
            assert match.group(1) in listed | staff_only | {'COOKIE'}, f'{path.name}: set_cookie({match.group(1)}) is not on the privacy page'
    assert 'no advertising' in page.lower() and 'preference cookies in the admin' in page


def test_the_privacy_page_covers_gdpr_and_ccpa(client):
    page = client.get('/privacy/').text
    for needed in ('Your rights', 'GDPR', 'California', 'do not sell', 'Stripe', 'Google', 'withdraw consent', 'complain'):
        assert needed.lower() in page.lower(), needed


def test_the_terms_carry_the_medical_disclaimer_and_the_refund_promise(client):
    terms = client.get('/terms/').text
    assert 'id="medical"' in terms and 'not medical advice' in terms and 'pregnant' in terms
    assert 'Refund Policy' in terms and f'{legal.context()["refund_days"]} days' in terms


def test_the_refund_policy_says_the_days_and_how_to_ask(client):
    page = client.get('/refund/').text
    days = legal.context()['refund_days']
    assert f'within {days} days' in page and 'no questions' in page.lower() and 'Instagram' in page
    assert 'EU' in page and 'UK' in page


def test_the_refund_days_are_the_setting():
    from app.core.config import Settings
    assert Settings().refund_days == 14


def test_the_footer_links_the_three_texts_and_the_sitemap_lists_them(client):
    home = client.get('/').text
    for path in PAGES:
        assert f'href="{path}"' in home, path
    sitemap = client.get('/sitemap.xml').text
    for path in PAGES:
        assert path in sitemap, path


def test_the_buy_box_asks_for_the_texts_and_names_the_refund_days(accounts, people, stripe, monkeypatch):
    slug = 'gut-massage-guide'
    monkeypatch.setitem(catalog.PRODUCTS, slug, dataclasses.replace(catalog.PRODUCTS[slug], on_sale=True))
    page = people['boss'].get(f'/products/{slug}/')
    assert page.status_code == 200
    assert f'action="/checkout/{slug}"' in page.text and 'name="agree"' in page.text and 'required' in page.text
    for text in ('Terms of Service', 'Privacy Policy', 'Refund Policy', f'within {legal.context()["refund_days"]} days'):
        assert text in page.text, text
    assert 'payment-systems.svg' in page.text
    assert payments.sales_open(slug) is False


def test_the_privacy_page_discloses_google_the_course_checklist_and_watch_time(client):
    page = client.get('/privacy/').text
    for needed in ('accounts.google.com', 'safety conditions', 'watched each technique', 'id="tests"', 'visitor ID cookie is set the first time'):
        assert needed in page, needed


def test_the_lists_of_skipping_and_stopping_are_the_same_everywhere(client):
    from app import course, learn, safety
    terms = client.get('/terms/').text
    for item in safety.SKIP_CONDITIONS[:3] + safety.STOP_SIGNS[:3]:
        assert item.replace('’', "'").lower() in terms.replace('&#39;', "'").replace('’', "'").lower(), item
    guide = ' '.join(p for s in learn.GUT_MASSAGE_GUIDE.sections for p in s.paragraphs)
    assert all(item in guide for item in safety.SKIP_CONDITIONS)
    assert course.STOP_RULES == safety.STOP_SIGNS


def test_the_refund_days_never_go_below_the_statutory_fourteen(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setenv('REFUND_DAYS', '7')
    assert Settings().refund_days == 14
    monkeypatch.setenv('REFUND_DAYS', '30')
    assert Settings().refund_days == 30


def test_the_privacy_page_states_the_consent_the_forms_collect(client):
    page = client.get('/privacy/').text
    assert 'only with your explicit consent' in page and 'Nothing is stored without it' in page
    assert 'stored only if you tick the box' in page
    assert 'without asking' in page            # the visitor cookie is said to be set without a banner


def test_lists_with_an_inner_or_are_joined_with_semicolons():
    from app import safety
    text = safety.sentence(safety.ASK_FIRST)
    assert text == 'gallstones; irritable bowel syndrome; or blood-thinning medication or a blood-clotting problem'
    assert '; or ' in safety.sentence(safety.SKIP_CONDITIONS)
