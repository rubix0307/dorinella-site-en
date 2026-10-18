"""Search engines: sitemap.xml, the Sitemap line of robots.txt, schema.org on the public pages, og:description that differs per page."""
import json
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.catalog import PRODUCTS
from app.learn import GUIDES, SELF_TESTS
from app.main import app

NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


@pytest.fixture(scope='module')
def client():
    return TestClient(app, follow_redirects=False)


def graph(html):
    scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    return [node for text in scripts for node in json.loads(text).get('@graph', [json.loads(text)])]


def test_the_sitemap_lists_public_pages_with_a_date_and_robots_names_it(client):
    response = client.get('/sitemap.xml')
    assert response.status_code == 200 and response.headers['content-type'].startswith('application/xml')
    urls = {u.find('s:loc', NS).text: getattr(u.find('s:lastmod', NS), 'text', None) for u in ET.fromstring(response.text).findall('s:url', NS)}   # no git and no lastmod.json: no date
    paths = {re.sub(r'^https?://[^/]+', '', loc) for loc in urls}
    assert {'/', '/products/', '/guides/', '/links/', '/privacy/', '/terms/'} <= paths
    assert all(f'/products/{slug}/' in paths for slug in PRODUCTS)
    assert all(f'/guides/{slug}/' in paths for slug in GUIDES) and all(f'/tests/{slug}/' in paths for slug in SELF_TESTS)
    assert all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', stamp) for stamp in urls.values() if stamp is not None)
    assert not any(p.startswith(('/admin', '/account', '/login', '/go/', '/l/', '/course', '/checkout')) for p in paths)
    assert re.search(r'^Sitemap: https?://\S+/sitemap\.xml$', client.get('/robots.txt').text, re.MULTILINE)


@pytest.mark.parametrize('path, kind', [('/', 'FAQPage'), ('/products/massage-course/', 'Course'), ('/products/gut-massage-guide/', 'Product'),
                                        ('/products/', 'CollectionPage'), ('/links/', 'WebPage')])
def test_public_pages_carry_schema_org(client, path, kind):
    nodes = graph(client.get(path).text)
    types = {node['@type'] for node in nodes}
    assert {'Organization', 'WebSite', kind} <= types, types


def test_the_markup_is_english_and_names_the_brand(client):
    nodes = {node['@type']: node for node in graph(client.get('/').text)}
    assert nodes['Organization']['name'] == 'Lola Krissss' and nodes['WebSite']['inLanguage'] == 'en-US'
    assert 'Who is the guide for' not in json.dumps(nodes) and nodes['FAQPage']['mainEntity'][0]['name'] == 'What is the Gut Massage Guide?'


def test_a_product_that_is_not_on_sale_publishes_no_price(client):
    for slug in ('gut-massage-guide', 'massage-course'):
        node = next(n for n in graph(client.get(f'/products/{slug}/').text) if n['@type'] in ('Product', 'Course'))
        assert 'offers' not in node, slug
        assert node['inLanguage'] == 'en-US'


def test_a_product_on_sale_has_dollar_offers_and_breadcrumbs(client, monkeypatch):
    import dataclasses

    from app import catalog
    monkeypatch.setitem(catalog.PRODUCTS, 'massage-course', dataclasses.replace(PRODUCTS['massage-course'], on_sale=True))
    nodes = {node['@type']: node for node in graph(client.get('/products/massage-course/').text)}
    assert nodes['Course']['offers'][0]['priceCurrency'] == 'USD' and len(nodes['BreadcrumbList']['itemListElement']) == 3


def test_every_page_names_its_own_description_and_title(client):
    paths = ('/', '/products/', '/products/gut-massage-guide/', '/products/massage-course/', '/links/', '/privacy/')
    descriptions = {path: re.search(r'property="og:description" content="([^"]*)"', client.get(path).text).group(1) for path in paths}
    titles = {path: re.search(r'<title>([^<]*)</title>', client.get(path).text).group(1) for path in paths}
    assert len(set(descriptions.values())) == len(paths) and len(set(titles.values())) == len(paths)
    assert all(len(d) <= 200 for d in descriptions.values())


def test_the_share_images_are_the_site_own_and_exist(client):
    from pathlib import Path
    for path in ('/', '/products/gut-massage-guide/'):
        image = re.search(r'property="og:image" content="[^"]*?/static/(img/[^?"]+)', client.get(path).text).group(1)
        assert (Path(__file__).resolve().parent.parent / 'app' / 'static' / image).exists(), image


@pytest.fixture(autouse=True)
def _fresh_sitemap():
    from app import seo
    seo.invalidate()
    yield
    seo.invalidate()


def test_a_page_without_a_date_has_no_lastmod():
    from datetime import date

    from app import seo
    root = ET.fromstring(seo.sitemap_xml('https://x.test', [('/a/', date(2026, 1, 2)), ('/b/', None)]))
    rows = {u.find('s:loc', NS).text: u.find('s:lastmod', NS) for u in root.findall('s:url', NS)}
    assert rows['https://x.test/a/'].text == '2026-01-02' and rows['https://x.test/b/'] is None


def test_without_git_the_code_pages_have_no_date(monkeypatch):
    from app import seo

    def no_git(*args, **kwargs):
        raise OSError('no git')
    seo._modified.cache_clear()
    monkeypatch.setattr(seo.subprocess, 'run', no_git)
    assert seo._modified('templates/pages/index.html') is None
    seo._modified.cache_clear()


def test_sitemap_and_robots_answer_head(client):
    for path in ('/sitemap.xml', '/robots.txt'):
        head = client.head(path)
        assert head.status_code == 200 and head.content == b'', path


def test_the_sitemap_survives_a_database_failure_with_the_code_pages(client, monkeypatch):
    from app import seo
    real = seo.entries

    async def broken(session):
        if session is not None:
            raise RuntimeError('database is down')
        return await real(None)
    monkeypatch.setattr(seo, 'entries', broken)
    response = client.get('/sitemap.xml')
    assert response.status_code == 200 and '<loc>' in response.text and 'max-age=300' in response.headers['cache-control']
    monkeypatch.setattr(seo, 'entries', real)
    assert 'max-age=600' in client.get('/sitemap.xml').headers['cache-control']               # the failed answer was not kept


def test_the_sitemap_is_kept_for_a_while_and_vary_host_without_site_url(client, monkeypatch):
    from app import seo
    from app.core.config import settings
    calls = []
    real = seo.entries

    async def counting(session):
        calls.append(1)
        return await real(session)
    monkeypatch.setattr(seo, 'entries', counting)
    client.get('/sitemap.xml')
    client.get('/sitemap.xml')
    assert len(calls) == 1
    seo.invalidate()
    client.get('/sitemap.xml')
    assert len(calls) == 2
    original = settings.site_url
    object.__setattr__(settings, 'site_url', '')
    try:
        assert 'Host' in client.get('/sitemap.xml').headers['vary'] and 'Host' in client.get('/robots.txt').headers['vary']
        object.__setattr__(settings, 'site_url', 'https://lolakrissss.com')
        assert 'Host' not in client.get('/robots.txt').headers.get('vary', '')
    finally:
        object.__setattr__(settings, 'site_url', original)


def test_script_escapes_the_characters_that_could_break_out_of_the_tag():
    from app import seo
    html = str(seo.script({'@type': 'Thing', 'name': '</script><b>&x'}))
    body = html.removeprefix('<script type="application/ld+json">').removesuffix('</script>')
    assert '<' not in body and '>' not in body and '&' not in body
    assert json.loads(body)['@graph'][0]['name'] == '</script><b>&x'


def test_every_disallowed_prefix_is_absent_from_the_sitemap(client):
    prefixes = re.findall(r'^Disallow: (\S+)$', client.get('/robots.txt').text, re.MULTILINE)
    assert '/admin' in prefixes          # the closed course is deliberately not named in robots.txt (tests/test_course.py): it is a 404 for the public
    paths = {re.sub(r'^https?://[^/]+', '', u.find('s:loc', NS).text) for u in ET.fromstring(client.get('/sitemap.xml').text).findall('s:url', NS)}
    assert not [p for p in paths if p.startswith(tuple(prefixes))]


def test_the_sitemap_cache_is_bounded_and_drops_the_expired_first(monkeypatch):
    from app import seo
    now = [1000.0]
    monkeypatch.setattr(seo.time, 'monotonic', lambda: now[0])
    for n in range(seo.SITEMAP_MAX * 3):
        seo._keep(f'http://host{n}.test', 'x')
        now[0] += 1
    assert len(seo._SITEMAP) == seo.SITEMAP_MAX and 'http://host0.test' not in seo._SITEMAP and f'http://host{seo.SITEMAP_MAX * 3 - 1}.test' in seo._SITEMAP
    now[0] += seo.SITEMAP_TTL * 10                                  # all expired: the next store leaves only itself
    seo._keep('http://last.test', 'y')
    assert list(seo._SITEMAP) == ['http://last.test']


def test_a_build_that_began_before_invalidate_is_not_kept(client, monkeypatch):
    import asyncio

    from app import seo
    real = seo.entries

    async def invalidated_midway(session):
        pages = await real(session)
        seo.invalidate()                                            # an article was hidden while this build ran
        return pages
    monkeypatch.setattr(seo, 'entries', invalidated_midway)
    body, complete = asyncio.run(seo.sitemap_body('https://gen.test', None))
    assert complete and '<loc>' in body and 'https://gen.test' not in seo._SITEMAP and not seo._LOCKS


def test_concurrent_requests_build_the_sitemap_once(monkeypatch):
    import asyncio

    from app import seo
    real = seo.entries
    calls = []

    async def slow(session):
        calls.append(1)
        await asyncio.sleep(0.05)
        return await real(session)
    monkeypatch.setattr(seo, 'entries', slow)

    async def many():
        return await asyncio.gather(*(seo.sitemap_body('https://burst.test', None) for _ in range(5)))
    results = asyncio.run(many())
    assert len(calls) == 1 and len({body for body, _ in results}) == 1


def test_lastmod_comes_from_the_baked_file_when_there_is_no_git(monkeypatch, tmp_path):
    from datetime import date

    from app import seo
    baked = tmp_path / 'lastmod.json'
    baked.write_text(json.dumps({'templates/pages/index.html': '2026-03-04', 'content.py': '2026-05-06'}), encoding='utf-8')
    monkeypatch.setattr(seo, 'LASTMOD_FILE', baked)
    seo._baked_days.cache_clear()
    seo._modified.cache_clear()
    try:
        monkeypatch.setattr(seo.Path, 'exists', lambda self: self.name != '.git')
        assert seo._modified('templates/pages/index.html', 'content.py') == date(2026, 5, 6)         # the newest file wins
        assert seo._baked(('templates\\pages\\index.html',)) == date(2026, 3, 4)                      # a Windows path works
        assert seo._modified('catalog.py') is None                                                     # not in the file: no date
        baked.write_text('not json', encoding='utf-8')
        seo._baked_days.cache_clear()
        seo._modified.cache_clear()
        assert seo._modified('content.py') is None                                                     # broken file: no date, no crash
    finally:
        seo._baked_days.cache_clear()
        seo._modified.cache_clear()
