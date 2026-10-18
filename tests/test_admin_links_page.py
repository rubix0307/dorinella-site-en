"""The admin screen of the links page: who may open it, the language, the picker's short link. The page itself (/links/) is tested with app/links.py."""
import pytest
from sqlalchemy import text


@pytest.fixture
def editor(accounts):
    accounts.add('editor@example.com', 'admin', groups=[accounts.group('Links', ['links_page.manage'])])
    accounts.add('root@example.com', 'root')
    accounts.add('bare@example.com', 'admin')
    accounts.exec('DELETE FROM bio_sublinks')
    accounts.exec('DELETE FROM bio_buttons')
    accounts.exec('DELETE FROM bio_page')
    return accounts


def test_the_screen_needs_the_links_page_right(editor):
    assert editor.login('bare@example.com').get('/admin/links/').status_code == 403
    assert editor.login('editor@example.com').get('/admin/links/').status_code == 200
    assert editor.login('root@example.com').get('/admin/links/').status_code == 200


def test_saving_and_the_picker_need_the_right_too(editor):
    bare = editor.login('bare@example.com')
    assert bare.post('/admin/links/save', data={'count': '0'}).status_code == 403
    assert bare.post('/admin/links/short-link', json={'target_url': 'https://example.com/a'}).status_code == 403
    assert bare.get('/admin/links/icons.json').status_code == 403


def test_the_screen_is_english_and_has_a_russian_version(editor):
    client = editor.login('editor@example.com')
    english = client.get('/admin/links/').text
    assert 'Links page' in english and 'Ads and partnerships' in english and 'Страница ссылок' not in english
    client.cookies.set('admin_lang', 'ru')
    russian = client.get('/admin/links/').text
    assert 'Страница ссылок' in russian and 'Реклама и сотрудничество' in russian and 'Ads and partnerships' not in russian


def test_the_icon_tooltips_are_the_english_icon_names(editor):
    page = editor.login('editor@example.com').get('/admin/links/').text
    assert 'data-tip="heart"' in page


def test_a_short_link_is_made_from_the_picker_and_listed(editor):
    client = editor.login('editor@example.com')
    reply = client.post('/admin/links/short-link', json={'target_url': 'https://example.com/shop', 'slug': 'Shop-1', 'title': 'Shop'})
    assert reply.status_code == 200 and reply.json()['slug'] == '/l/shop-1'
    row = editor.rows("SELECT created_by FROM short_links WHERE slug = 'shop-1'")[0]
    assert row['created_by'] is not None
    assert 'value="' in client.get('/admin/links/').text and '/l/shop-1' in client.get('/admin/links/').text
    taken = client.post('/admin/links/short-link', json={'target_url': 'https://example.com/other', 'slug': 'shop-1'})
    assert taken.status_code == 422 and 'already taken' in taken.json()['error']
    assert client.post('/admin/links/short-link', json={'target_url': 'not a url'}).status_code == 422


def test_the_same_address_twice_is_a_conflict_unless_allowed(editor):
    client = editor.login('editor@example.com')
    assert client.post('/admin/links/short-link', json={'target_url': 'https://example.com/a/', 'slug': 'one'}).status_code == 200
    again = client.post('/admin/links/short-link', json={'target_url': 'https://www.example.com/a', 'slug': 'two'})
    assert again.status_code == 409 and '/l/one already leads to this address.' in again.json()['error']
    anyway = client.post('/admin/links/short-link', json={'target_url': 'https://www.example.com/a', 'slug': 'two', 'allow_duplicate': True})
    assert anyway.status_code == 200
    with editor.engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM short_links')).scalar() == 2


def test_saving_a_button_that_points_at_a_short_link(editor):
    client = editor.login('editor@example.com')
    link = client.post('/admin/links/short-link', json={'target_url': 'https://example.com/x', 'slug': 'x1'}).json()
    data = {'count': '1', 'header_name': 'Gut Guide', 'header_tagline': '', 'b0_order': '1', 'b0_id': '', 'b0_image': 'link', 'b0_title': 'My shop',
            'b0_note': '', 'b0_link': str(link['id']), 'b0_sub_count': '0', 'b0_visible': '1'}
    reply = client.post('/admin/links/save', data=data)
    assert reply.status_code == 303 and reply.headers['location'].endswith('?notice=saved')
    assert editor.rows('SELECT title, is_visible FROM bio_buttons') == [{'title': 'My shop', 'is_visible': 1}]
    assert 'Saved. The page shows it at once.' in client.get('/admin/links/', params={'notice': 'saved'}).text


def test_a_button_without_a_known_link_is_refused_with_a_translated_message(editor):
    client = editor.login('editor@example.com')
    data = {'count': '1', 'header_name': 'Gut Guide', 'b0_order': '1', 'b0_image': 'link', 'b0_title': 'Ghost', 'b0_link': '99999', 'b0_sub_count': '0'}
    english = client.post('/admin/links/save', data=data)
    assert english.status_code == 422 and 'pick a link from the analytics list' in english.text
    client.cookies.set('admin_lang', 'ru')
    assert client.post('/admin/links/save', data=data).status_code == 422
