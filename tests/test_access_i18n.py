"""Every admin string of the users / groups / permissions / links-page screens has a Russian translation with the same placeholders."""
import re
from pathlib import Path

from app import permissions, shortlinks, users
from app.locale.ru import RU
from app.routers import admin_groups, admin_users

ROOT = Path(__file__).resolve().parent.parent / 'app'
TEMPLATES = [ROOT / 'templates' / 'admin' / name for name in ('users.html', 'user_form.html', 'groups.html', 'group_form.html', 'links_page.html')]
SOURCES = [ROOT / 'users.py', ROOT / 'groups.py', ROOT / 'shortlinks.py']

CALL = re.compile(r"""\b_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
USER_ERROR = re.compile(r"""UserError\(\s*(?:Message\()?(?:f?'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
PLACEHOLDER = re.compile(r'%\((\w+)\)s')


def template_keys():
    for path in TEMPLATES:
        for single, double in CALL.findall(path.read_text(encoding='utf-8')):
            yield path.name, single or double


def source_keys():
    yield from (('users.py', text) for text in [users.FORBIDDEN, *users.LAST_ADMIN.values()])
    for path in SOURCES:
        for single, double in USER_ERROR.findall(path.read_text(encoding='utf-8')):
            yield path.name, single or double
    yield from (('shortlinks.py', text) for text in (shortlinks.TAKEN, shortlinks.DUPLICATE, shortlinks.DUPLICATE_TITLED))
    yield from (('shortlinks.py', shortlinks.validate(slug, url)) for slug, url in (('BAD CODE', 'https://example.com/x'), ('ok', 'nope')))
    yield from (('routers', text) for text in [*admin_users.NOTICES.values(), *admin_users.ERRORS.values(), *admin_users.STATUS_LABELS.values(),
                                               *admin_groups.NOTICES.values(), *admin_groups.ERRORS.values(), *permissions.ROLE_LABELS.values()])
    yield from (('table', column.label) for column in admin_users.USERS_TABLE.columns)
    for section, label in permissions.PERMISSIONS.values():
        yield 'permissions.py', section
        yield 'permissions.py', label


def test_the_screens_have_something_to_translate():
    assert len(list(template_keys())) > 80 and len(list(source_keys())) > 40


def test_every_string_has_a_russian_translation():
    missing = sorted({f'{where}: {text}' for where, text in [*template_keys(), *source_keys()] if text not in RU})
    assert not missing, missing


def test_translations_keep_the_placeholders():
    wrong = [text for _, text in [*template_keys(), *source_keys()] if text in RU and set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(RU[text]))]
    assert not wrong, wrong


def test_the_source_strings_are_english():
    cyrillic = sorted({text for _, text in [*template_keys(), *source_keys()] if re.search('[а-яіїєґ]', text, re.IGNORECASE)})
    assert not cyrillic, cyrillic


def test_the_files_have_no_ukrainian_left():
    for path in [*TEMPLATES, *SOURCES, ROOT / 'permissions.py', ROOT / 'audit.py', ROOT / 'auth.py', ROOT / 'google_login.py',
                 ROOT / 'routers' / 'admin_users.py', ROOT / 'routers' / 'admin_groups.py']:
        found = re.findall('[а-яіїєґ]+', path.read_text(encoding='utf-8'), re.IGNORECASE)
        assert not found, (path.name, found[:5])
