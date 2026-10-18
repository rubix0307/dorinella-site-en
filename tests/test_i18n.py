"""The admin's languages (app/i18n.py, app/locale/ru.py): how a language is picked, how a string is translated, and that every string of the
shell, the dashboard, the pages report and the profile has a Russian text with the same placeholders. Strings of other screens are listed when
they have no translation (a gap is never an error for the visitor: the English text shows), but only a placeholder mismatch fails here."""
import re
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import dashboard_stats, i18n, page_stats, permissions, stats
from app.locale.ru import JS, RU
from app.main import app
from app.routers import admin, admin_profile

ROOT = Path(__file__).resolve().parent.parent / 'app'
MINE = ('base', '_bar', '_filter', '_table', '_toast', 'forbidden', 'dashboard', 'profile', 'pages')
CALL = re.compile(r"""\b_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
SCRIPT_CALL = re.compile(r"""\bt\(\s*'((?:[^'\\]|\\.)*)'""")
PLACEHOLDER = re.compile(r'%\((\w+)\)s')


def template_keys(names=None):
    for path in sorted((ROOT / 'templates' / 'admin').glob('*.html')):
        if names is None or path.stem in names:
            for single, double in CALL.findall(path.read_text(encoding='utf-8')):
                yield path.name, (single or double).replace("\\'", "'")


def script_keys():
    for path in sorted((ROOT / 'static' / 'js').glob('admin*.js')):
        for text in SCRIPT_CALL.findall(path.read_text(encoding='utf-8')):
            yield path.name, text.replace("\\'", "'")


def code_keys():
    """The strings the Python side of my screens hands to the templates."""
    yield from (('stats', text) for text in [*stats.SOURCE_LABELS.values(), *stats.STATUS_LABELS.values(), *(label for _, _, label in stats.DEVICES),
                                             *stats._AREAS.values(), *(column.label for column in stats.LINKS_TABLE.columns)])
    yield from (('page_stats', label) for label, _, _ in page_stats.SECTIONS.values())
    yield from (('admin', text) for text in admin.NOTICES.values())
    yield ('dashboard_stats', dashboard_stats.DIRECT)
    yield from (('permissions', text) for text in permissions.ROLE_LABELS.values())
    for section, label in permissions.PERMISSIONS.values():
        yield 'permissions', section
        yield 'permissions', label


def test_a_language_is_picked_from_the_cookie_then_the_browser():
    def pick(cookies=None, accept=''):
        return i18n.pick(SimpleNamespace(cookies=cookies or {}, headers={'accept-language': accept}))    # type: ignore[arg-type]
    assert pick() == 'en'
    assert pick(accept='ru-RU,ru;q=0.9') == 'ru' and pick(accept='uk') == 'ru' and pick(accept='be') == 'ru'
    assert pick(accept='en-US,en;q=0.9,ru;q=0.5') == 'en' and pick(accept='de') == 'en'
    assert pick(cookies={'admin_lang': 'ru'}, accept='en') == 'ru'          # the choice beats the browser
    assert pick(cookies={'admin_lang': 'en'}, accept='ru') == 'en'
    assert pick(cookies={'admin_lang': 'klingon'}, accept='ru') == 'ru'     # an unknown value is ignored


def test_translate_falls_back_to_english_and_fills_the_placeholders():
    assert i18n.translate('Users', 'ru') == 'Пользователи' and i18n.translate('Users', 'en') == 'Users'
    assert i18n.translate('A sentence nobody translated', 'ru') == 'A sentence nobody translated'
    assert i18n.translate('Link %(n)s', 'ru', n=3) == 'Ссылка 3' and i18n.translate('Link %(n)s', 'en', n=3) == 'Link 3'
    assert i18n.translate('100% sure', 'ru') == '100% sure'                 # a % without values is not a placeholder


def test_a_message_reads_as_english_and_is_translated_with_its_values():
    message = i18n.Message('Link %(n)s', n=3)
    assert message == 'Link 3' and isinstance(message, str)
    assert i18n.translate(message, 'ru') == 'Ссылка 3'
    nested = i18n.Message('%(area)s · %(detail)s', area=i18n.Message('Header'), detail='x')
    assert nested == 'Header · x' and i18n.translate(nested, 'ru') == 'Шапка · x'


def test_the_screens_have_something_to_translate():
    assert len(list(template_keys(MINE))) > 100 and len(list(script_keys())) > 30 and len(list(code_keys())) > 30


def test_every_string_of_the_shell_and_the_dashboard_has_a_russian_text():
    missing = sorted({f'{where}: {text}' for where, text in [*template_keys(MINE), *code_keys()] if text not in RU})
    assert not missing, missing


def test_every_string_of_the_scripts_has_a_russian_text():
    missing = sorted({f'{where}: {text}' for where, text in script_keys() if text not in JS})
    assert not missing, missing


def test_the_other_screens_gaps_are_listed():
    gaps = sorted({f'{where}: {text}' for where, text in template_keys() if text not in RU})
    if gaps:
        warnings.warn(f'{len(gaps)} admin strings without a Russian text: {gaps}', stacklevel=1)


def test_translations_keep_the_placeholders():
    wrong = [text for table in (RU, JS) for text in table if set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(table[text]))]
    assert not wrong, wrong


def test_my_files_have_no_ukrainian_left():
    letters = re.compile('[а-яіїєґ]', re.IGNORECASE)
    for path in [*(ROOT / 'templates' / 'admin' / f'{name}.html' for name in MINE), ROOT / 'routers' / 'admin.py', ROOT / 'routers' / 'admin_profile.py',
                 ROOT / 'stats.py', ROOT / 'live.py', ROOT / 'analytics.py', ROOT / 'page_stats.py', ROOT / 'dashboard_stats.py', ROOT / 'admin_tables.py',
                 ROOT / 'timezones.py', ROOT / 'cli.py', ROOT / 'backup.py', ROOT / 'static' / 'js' / 'admin.js', ROOT / 'static' / 'js' / 'admin-confirm.js',
                 ROOT / 'static' / 'js' / 'admin-i18n.js']:
        found = [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if letters.search(line)]
        assert not found, (path.name, found[:3])


@pytest.fixture
def admin_client(accounts):
    accounts.add('root@example.com', 'root')
    return accounts.login('root@example.com')


def test_the_admin_speaks_the_language_of_the_cookie(admin_client):
    english = admin_client.get('/admin/pages/').text
    assert '<html lang="en"' in english and '<title>Pages' in english and 'Sections of the site' in english
    admin_client.cookies.set('admin_lang', 'ru')
    russian = admin_client.get('/admin/pages/').text
    assert '<html lang="ru"' in russian and '<title>Страницы' in russian and 'Разделы сайта' in russian
    assert '"Confirm the action":' in russian                                # the scripts get their texts
    assert 'Sections of the site' not in russian


def test_the_scripts_get_texts_only_in_russian(admin_client):
    assert '<script type="application/json" id="admin-i18n">{}</script>' in admin_client.get('/admin/pages/').text
    admin_client.cookies.set('admin_lang', 'ru')
    assert 'id="admin-i18n">{}' not in admin_client.get('/admin/pages/').text


def test_the_public_site_never_translates():
    client = TestClient(app, headers={'accept-language': 'ru-RU,ru;q=0.9'})
    assert '<html lang="en"' in client.get('/').text


def test_the_language_switch_sets_the_cookie_and_goes_back(admin_client):
    response = admin_client.post('/admin/language', data={'lang': 'ru', 'next': '/admin/pages/?section=guides'})
    assert response.status_code == 303 and response.headers['location'] == '/admin/pages/?section=guides'
    assert 'admin_lang=ru' in response.headers['set-cookie'] and 'samesite=lax' in response.headers['set-cookie'].lower()
    assert admin_client.post('/admin/language', data={'lang': 'en', 'next': '/admin/'}).headers['set-cookie'].startswith('admin_lang=en')


@pytest.mark.parametrize('back', ['https://evil.example/', '//evil.example/', '/\\evil.example', '/somewhere-else/', '', 'admin'])
def test_the_language_switch_only_goes_back_into_the_admin(admin_client, back):
    response = admin_client.post('/admin/language', data={'lang': 'ru', 'next': back})
    assert response.status_code == 303 and response.headers['location'] == '/admin/'


def test_an_unknown_language_is_not_stored_and_a_foreign_site_cannot_switch(admin_client):
    assert 'set-cookie' not in admin_client.post('/admin/language', data={'lang': 'klingon', 'next': '/admin/'}).headers
    foreign = admin_client.post('/admin/language', data={'lang': 'ru'}, headers={'origin': 'https://evil.example'})
    assert foreign.headers['location'] == '/' and 'set-cookie' not in foreign.headers


def test_the_profile_messages_are_keys_of_the_dictionary():
    assert admin_profile.ROLE_LABELS is permissions.ROLE_LABELS
    for text in ('Time zone saved.', 'This time zone is not in the list.'):
        assert text in RU
