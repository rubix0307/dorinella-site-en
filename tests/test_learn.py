"""The guide library: the free guide, the paid guide's locked and unlocked states."""
import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from app import catalog, payments
from app.learn import GUIDES, SELF_TESTS
from app.main import app

client = TestClient(app, follow_redirects=False)
PAID = next(g for g in GUIDES.values() if g.paid)
FREE = next(g for g in GUIDES.values() if not g.paid)


@pytest.mark.parametrize('path', ['/guides/', *[f'/guides/{s}/' for s in GUIDES], *[f'/tests/{s}/' for s in SELF_TESTS]])
def test_library_pages_render(path):
    response = client.get(path)
    assert response.status_code == 200 and '<h1' in response.text
    for asset in set(re.findall(r'/static/[^"\s?,]+', response.text)):
        assert client.get(asset).status_code == 200, asset


def test_the_library_lists_both_guides_and_marks_the_paid_one_locked():
    html = client.get('/guides/').text
    assert f'href="/guides/{FREE.slug}/"' in html and f'href="/guides/{PAID.slug}/"' in html
    assert 'free-card--guide is-locked' in html and 'Paid guide' in html and '/tests/bristol/' in html


def test_the_library_has_nothing_of_the_removed_features():
    html = client.get('/guides/').text.lower()
    for word in ('mini-course', 'recipe', 'telegram', 'newsletter'):
        assert word not in html


def test_the_free_guide_is_read_in_full():
    html = client.get(f'/guides/{FREE.slug}/').text
    assert all(section.title.replace('"', '&#34;') in html for section in FREE.sections)
    assert 'href="/tests/bristol/"' in html and 'id="lock-title"' not in html


def test_the_paid_guide_is_locked_for_a_visitor_and_never_sends_the_hidden_text():
    response = client.get(f'/guides/{PAID.slug}/')
    html = response.text
    assert PAID.sections[0].title in html and 'id="lock-title"' in html
    for section in PAID.sections[PAID.preview_sections:]:
        assert f'<h2 class="guide__h2">{section.title}</h2>' not in html and section.paragraphs[0][:40] not in html
    assert PAID.steps[0][1] not in html
    assert response.headers['cache-control'] == 'private, no-store'


def test_the_locked_page_says_coming_soon_while_the_product_is_not_on_sale(monkeypatch):
    monkeypatch.setitem(catalog.PRODUCTS, PAID.slug, dataclasses.replace(catalog.PRODUCTS[PAID.slug], on_sale=False))
    assert 'Coming soon' in client.get(f'/guides/{PAID.slug}/').text


def test_the_locked_page_offers_the_purchase_once_it_is_open(monkeypatch):
    monkeypatch.setattr(payments, 'shown_to', lambda user, slug='': True)
    monkeypatch.setitem(catalog.PRODUCTS, PAID.slug, dataclasses.replace(catalog.PRODUCTS[PAID.slug], on_sale=True))
    html = client.get(f'/guides/{PAID.slug}/').text
    assert 'Get the guide' in html and '$19.00' in html


def test_an_owner_reads_the_whole_paid_guide(monkeypatch, accounts):
    async def owns(session, user, slug=''):
        return slug == PAID.product_slug
    monkeypatch.setattr(payments, 'has_entitlement', owns)
    accounts.add('reader@example.com', 'admin')
    reader = accounts.login('reader@example.com')
    html = reader.get(f'/guides/{PAID.slug}/').text
    assert all(section.title in html for section in PAID.sections) and 'id="lock-title"' not in html
    assert 'Yours' in reader.get('/guides/').text


def test_unknown_guide_is_404():
    assert client.get('/guides/nope/').status_code == 404


def test_the_old_tests_address_leads_to_the_library():
    assert client.get('/tests/').headers['location'] == '/guides/#tests'


def test_health_copy_does_not_diagnose_or_promise():
    for band in SELF_TESTS['bristol'].bands:
        text = (band.text + ' '.join(band.tips)).lower()
        assert 'cure' not in text and 'you have ' not in text
        assert band.doctor
