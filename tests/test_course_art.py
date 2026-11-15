"""The artwork of the «Gut Massage» course: every picture the data names exists, stickers are decorative, the pages show them."""
import json
import re
from pathlib import Path

from PIL import Image

from app import course
from tests.test_course import BASE, admin, walk  # noqa: F401 — the fixture and the walker are shared

IMG = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'img' / 'course'
ART = IMG / 'art'


def _named_stickers():
    names = {p.mark for p in course.PAGES if p.mark}
    for page in course.PAGES:
        for block in page.blocks:
            d = block.d
            names.update(d.get('stickers') or ())
            if d.get('sticker'):
                names.add(d['sticker'])
            if block.kind == 'gets':
                names.update(str(row[2]) for row in d.get('rows', ()) if row[2] not in course.PHOTOS)
    names.update(str(g['sticker']) for g in course.GOALS)
    return sorted(names)


def test_every_named_sticker_is_a_svg_file():
    for name in _named_stickers():
        assert (ART / f'{name}.svg').is_file(), name


def test_the_photos_exist_and_their_sizes_match_the_files():
    used = {p.art for p in course.PAGES if p.art} | {g['photo'] for g in course.GOALS} | {
        row[2] for p in course.PAGES for b in p.blocks if b.kind == 'gets' for row in b.d['rows'] if row[2] in course.PHOTOS}
    used |= {b.d['photo'] for p in course.PAGES for b in p.blocks if b.d.get('photo')} | {'home-oats'}
    assert used <= set(course.PHOTOS), used - set(course.PHOTOS)
    for name, (width, height, alt) in course.PHOTOS.items():
        with Image.open(ART / f'{name}.webp') as im:
            assert im.size == (width, height), name
        assert alt.strip(), name


def test_every_photo_has_a_free_licence_in_the_credits():
    credits = {e['file']: e for e in json.loads((IMG / 'CREDITS.json').read_text(encoding='utf-8'))}
    for name in course.PHOTOS:
        entry = credits[f'art/{name}.webp']
        assert 'free for commercial use' in entry['license'] and entry['page_url'], name
    assert not any(e['license'].upper().startswith('NOT CONFIRMED') for e in credits.values())
    assert not list(IMG.glob('drink-*'))       # no unconfirmed-licence photos ship with the course


def test_the_pages_show_stickers_without_a_text_alternative_and_photos_with_one(admin):  # noqa: F811
    walk(admin)
    for page in course.PAGES:
        r = admin.get(f'{BASE}{page.slug}/')
        assert r.status_code == 200, page.slug
        html = r.text
        assert all(alt == '' for alt in re.findall(r'<img class="abm-sticker[^>]*? alt="([^"]*)"', html)), page.slug
        assert all(alt for alt in re.findall(r'<img class="abm-photo[^>]*? alt="([^"]*)"', html)), page.slug
    first = admin.get(f'{BASE}about/').text
    assert 'What you get in the course' in first and 'After the course' in first
    assert 'Start the course' in first and f'This is 1 of {len(course.PAGES)} sections' in first
    assert 'abm-head__photo' in first and 'Start the course' not in admin.get(f'{BASE}body/').text
    assert 'abm-photo--banner' in admin.get(f'{BASE}combining/').text


def test_the_home_page_shows_the_order_of_the_steps(admin):  # noqa: F811
    html = admin.get(BASE).text
    assert 'How the course works' in html and html.count('class="abm-flow__n"') == len(course.GROUPS)


def test_a_signed_in_person_gets_ticks_on_the_finished_pages_and_a_visitor_does_not(admin):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.main import app
    assert admin.post(f'{BASE}about/complete').status_code == 303
    html = admin.get(f'{BASE}safety/').text
    assert re.search(rf'<a href="{BASE}about/" data-abm-slug="about" class="is-done"', html)
    assert 'data-abm-slug="safety" class="is-done"' not in html
    visitor = TestClient(app, follow_redirects=False)
    assert 'class="is-done"' not in visitor.get(f'{BASE}about/').text
