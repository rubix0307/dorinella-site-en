"""The /links/ page (the link in social bios): what it shows, what it hides, the checks of app/links.py. The admin screen is in test_admin_links_page.py."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import i18n, links
from app.db import new_session
from app.main import app

STARTER = [   # a starter page as the first migration makes it: (slug, target, short-link title, button title, note, image, primary)
    ('links-guide', 'https://lolakrissss.com/products/gut-massage-guide/', 'Link · Guide', 'Gut Massage Guide', 'Gentle abdominal self-massage', 'book-open-text', 1),
    ('links-test', 'https://lolakrissss.com/tests/bristol/', 'Link · Bristol test', 'Free Bristol stool scale test', 'Tips matched to your result', 'circle-help', 0),
    ('links-instagram', 'https://www.instagram.com/lolakrissss/', 'Link · Instagram', 'Instagram', '@lolakrissss', 'instagram', 0),
]


@pytest.fixture
def page(sync_db):
    with sync_db.begin() as conn:
        for table in ('bio_sublinks', 'bio_buttons', 'bio_page', 'outbound_clicks', 'short_links'):
            conn.execute(text(f'DELETE FROM {table}'))
        for slug, url, title, *_ in STARTER:
            conn.execute(text('INSERT INTO short_links (slug, target_url, title, is_active, is_favorite) VALUES (:s, :u, :t, 1, 0)'), {'s': slug, 'u': url, 't': title})
        ids = dict(conn.execute(text('SELECT slug, id FROM short_links')).all())
        for n, (slug, _, _, title, note, image, primary) in enumerate(STARTER, 1):
            conn.execute(text('INSERT INTO bio_buttons (position, short_link_id, title, note, image, is_primary, is_visible) VALUES (:p, :l, :t, :n, :i, :pr, 1)'),
                         {'p': n, 'l': ids[slug], 't': title, 'n': note, 'i': image, 'pr': primary})
    return ids


def test_the_buttons_are_short_links_with_their_notes(page):
    html = TestClient(app).get('/links/').text
    for slug, _, _, title, note, *_ in STARTER:
        assert f'href="/l/{slug}"' in html and title in html and note in html
    assert '/brands/instagram.png' in html
    assert '<header class="site-header' not in html and '<footer' not in html     # a bare page, made for a bio


def test_the_header_is_the_brand_until_the_admin_changes_it(page):
    html = TestClient(app).get('/links/').text
    assert '<h1 class="links__name">Lola Krissss</h1>' in html and '@lolakrissss' in html
    assert 'class="links__avatar links__avatar--art"' in html                      # no photo: the site's illustration, never a stranger's portrait


def test_a_hidden_button_and_a_switched_off_link_are_not_shown(page, sync_db):
    with sync_db.begin() as conn:
        conn.execute(text("UPDATE bio_buttons SET is_visible = 0 WHERE title = 'Instagram'"))
        conn.execute(text("UPDATE short_links SET is_active = 0 WHERE slug = 'links-test'"))
    html = TestClient(app).get('/links/').text
    assert 'href="/l/links-instagram"' not in html and 'href="/l/links-test"' not in html and 'href="/l/links-guide"' in html


def test_the_ads_card_needs_a_link(page, sync_db):
    with sync_db.begin() as conn:
        conn.execute(text("INSERT INTO bio_page (id, name, tagline, ads_title, ads_text, ads_button, ads_short_link_id) VALUES (1, 'Lola', 'Gut coach', 'Work with me', 'Write to me.', 'Say hello', :l)"),
                     {'l': page['links-instagram']})
    html = TestClient(app).get('/links/').text
    assert '<h1 class="links__name">Lola</h1>' in html and 'Work with me' in html and 'Say hello' in html
    with sync_db.begin() as conn:
        conn.execute(text('UPDATE bio_page SET ads_short_link_id = NULL'))
    assert '<aside class="links__ads"' not in TestClient(app).get('/links/').text


def test_a_button_with_sub_links_is_a_group(page, sync_db):
    with sync_db.begin() as conn:
        conn.execute(text("INSERT INTO bio_buttons (position, short_link_id, title, note, image, is_primary, is_visible) VALUES (9, NULL, 'More', '', 'link', 0, 1)"))
        group = conn.execute(text("SELECT id FROM bio_buttons WHERE title = 'More'")).scalar()
        conn.execute(text('INSERT INTO bio_sublinks (button_id, position, short_link_id, title, image) VALUES (:b, 1, :l, :t, :i)'),
                     {'b': group, 'l': page['links-test'], 't': 'Take the test', 'i': 'circle-help'})
    html = TestClient(app).get('/links/').text
    assert 'links__item links__item--group' in html and 'Take the test' in html and '<ul class="links__subs"' in html


def test_the_page_is_never_empty_even_without_buttons(sync_db):
    with sync_db.begin() as conn:
        for table in ('bio_sublinks', 'bio_buttons', 'bio_page'):
            conn.execute(text(f'DELETE FROM {table}'))
    assert 'Visit lolakrissss.com' in TestClient(app).get('/links/').text


def test_every_logo_exists_and_the_icon_catalog_is_complete():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'img' / 'brands'
    assert all((root / f'{key}.png').exists() for key in links.LOGOS)
    assert len(links.lucide()) > 1500 and 'book-open-text' in links.lucide() and 'chef-hat' in links.lucide()


def test_the_icon_groups_are_named_in_the_language_of_the_admin():
    english = {g['key']: g['label'] for g in links.icon_groups()}
    russian = {g['key']: g['label'] for g in links.icon_groups('ru')}
    assert english['food-beverage'] == 'Food and drink' and russian['food-beverage'] != 'Food and drink'
    assert not any(any('а' <= ch <= 'я' for ch in label.lower()) for label in english.values())


def _save(buttons, ads=None, header=None):
    async def run():
        async with new_session() as session:
            return await links.save(session, buttons, ads or {'short_link_id': None, 'title': '', 'text': '', 'button': ''}, header)
    return asyncio.run(run())


def _error(*args, **kwargs) -> str:
    """What a refused save says (a save that went through is a failure of the test)."""
    result = _save(*args, **kwargs)
    assert result is not None
    return result


def test_saving_refuses_what_it_cannot_show_with_an_english_message(page):
    error = _save([links.ButtonInput(0, '', '', 'link', False, True)])
    assert isinstance(error, i18n.Message) and 'title is required' in error
    assert 'pick a link from the analytics list' in _error([links.ButtonInput(99999, 'Ghost', '', 'link', False, True)])
    assert 'group' in _error([links.ButtonInput(0, 'Empty group', '', 'link', False, True)])
    assert 'pick an icon' in _error([links.ButtonInput(page['links-guide'], 'Guide', '', 'not-an-icon', False, True)])
    assert 'At most' in _error([links.ButtonInput(page['links-guide'], f'B{n}', '', 'link', False, True) for n in range(links.MAX_BUTTONS + 1)])
    assert 'button needs a text' in _error([], {'short_link_id': page['links-guide'], 'title': 'Hi', 'text': '', 'button': ''})
    assert 'photo' in _error([], header={'name': 'Lola', 'tagline': '', 'photo': b'not an image'})


def test_a_valid_page_is_saved_and_shown(page):
    assert _save([links.ButtonInput(page['links-guide'], 'The guide', 'Start here', 'book-open-text', True, True)], header={'name': 'Lola', 'tagline': 'Gut coach'}) is None
    html = TestClient(app).get('/links/').text
    assert 'The guide' in html and 'Start here' in html and 'Gut coach' in html and 'Free Bristol' not in html


def test_the_uploaded_photos_are_served_only_with_their_hash(page):
    client = TestClient(app)
    assert client.get('/links/photo/header-abc').status_code == 404
    assert client.get('/links/photo/1-abc').status_code == 404
    assert client.get('/links/photo/s1-abc').status_code == 404
