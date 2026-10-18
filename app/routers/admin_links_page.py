"""Admin: the /links/ page — its buttons (order, text, image, short link) and the ads card.

One form. A button can only point at one of the analytics short links: the choice is a list of
them, and the server checks it again on save. The image is picked in an emoji-picker-like
panel (static/js/admin-links-page.js): a network logo, any Lucide icon, or an uploaded photo.
"""
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from .. import db, i18n, links, shortlinks
from ..auth import authorize
from ..core.forms import field
from ..models import BioButton, ShortLink
from ..shortlinks import PREFIX
from ..site import BRAND
from .admin import NO_INDEX, _page, _save, _to_login

router = APIRouter(prefix='/admin/links', default_response_class=HTMLResponse, include_in_schema=False)

EMPTY_ROWS = 2          # blank rows under the buttons, for new ones
# the first row of the icon panel, like the reactions you use most
POPULAR = ['heart', 'heart-handshake', 'star', 'sparkles', 'gift', 'shopping-bag', 'book-open', 'graduation-cap',
           'chef-hat', 'utensils', 'salad', 'apple', 'leaf', 'sprout', 'flower-2', 'sun', 'moon', 'music', 'video',
           'camera', 'mic', 'newspaper', 'mail', 'phone', 'message-circle', 'send', 'globe', 'house', 'link',
           'calendar', 'coffee', 'dumbbell', 'baby', 'smile', 'crown', 'flame', 'zap', 'circle-help', 'megaphone']


def _id(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _form(request: Request, session: AsyncSession, user, rows: list[dict], ads: dict, header: dict,
                error: str = '', notice: str = '', status_code: int = 200) -> HTMLResponse:
    short_links = (await session.scalars(select(ShortLink).order_by(ShortLink.slug))).all()
    rows = rows + [{'id': None, 'short_link_id': None, 'title': '', 'note': '', 'image': 'link', 'photo_url': '',
                    'primary': False, 'visible': True, 'sublinks': []}] * EMPTY_ROWS
    saved = await links.settings(session)
    header = {**header, 'photo_url': links.header_photo_url(saved)}      # the photo as saved (a failed upload is not kept)
    return _page(request, 'admin/links_page.html', status_code=status_code, user=user, rows=rows, ads=ads, header=header,
                 short_links=short_links, logos=links.LOGOS, svg=links.lucide_svg, lucide=links.lucide(),
                 popular=[n for n in POPULAR if n in links.lucide()], icons_version=links.catalog_version(), prefix=PREFIX, error=error, notice=notice)


@router.get('/', name='admin_links_page')
async def page_form(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'links_page.manage')
    if user is None:
        return _to_login(request)
    buttons = (await session.scalars(select(BioButton).order_by(BioButton.position, BioButton.id))).all()
    rows = [{'id': b.id, 'short_link_id': b.short_link_id, 'title': b.title, 'note': b.note, 'image': b.image,
             'photo_url': links.photo_url(b) if b.image == links.PHOTO else '',
             'primary': b.is_primary, 'visible': b.is_visible,
             'sublinks': [{'id': s.id, 'short_link_id': s.short_link_id, 'title': s.title, 'image': s.image,
                                                                         'photo_url': links.sublink_photo_url(s) if s.image == links.PHOTO else ''} for s in b.sublinks]} for b in buttons]
    page = await links.settings(session)
    ads = {'short_link_id': page.ads_short_link_id, 'title': page.ads_title or '', 'text': page.ads_text or '',
           'button': page.ads_button or ''}
    header = {'name': page.name or BRAND, 'tagline': page.tagline or ''}
    return await _form(request, session, user, rows, ads, header, notice=request.query_params.get('notice', ''))


@router.get('/icons.json', name='admin_links_page_icons')
async def page_icons(request: Request, session: AsyncSession = Depends(db.get_session)):
    """Every Lucide icon for the panel: {icons: name → [inner SVG, search tags], groups: [...]} (the group names in the admin's language)."""
    if await authorize(request, session, 'links_page.manage', api=True) is None:
        return JSONResponse({}, status_code=403)
    return JSONResponse({'icons': links.lucide(), 'groups': links.icon_groups(i18n.pick(request))},
                        headers={'Cache-Control': 'private, max-age=86400', 'Vary': 'Cookie, Accept-Language'})


@router.post('/save', name='admin_links_page_save')
async def page_save(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'links_page.manage', post=True)
    if user is None:
        return _to_login(request)
    form = await request.form()
    rows: list[dict[str, Any]] = []
    for i in range(min(_id(field(form, 'count')) or 0, links.MAX_BUTTONS + EMPTY_ROWS)):
        get = lambda key: field(form, f'b{i}_{key}').strip()  # noqa: E731, B023
        if get('delete') or (not get('link') and not get('title')):
            continue                                                    # removed, or an empty row
        upload = form.get(f'b{i}_photo')
        photo = await upload.read(links.MAX_PHOTO + 1) if isinstance(upload, UploadFile) and upload.filename else None
        sublinks: list[dict[str, Any]] = []
        for j in range(min(_id(get('sub_count')) or 0, 100)):
            sub_link, sub_title = _id(get(f's{j}_link')), get(f's{j}_title')
            if sub_link or sub_title:                                    # an empty sub-row is dropped
                sub_upload = form.get(f'b{i}_s{j}_photo')
                sub_photo = await sub_upload.read(links.MAX_PHOTO + 1) if isinstance(sub_upload, UploadFile) and sub_upload.filename else None
                sublinks.append({'id': _id(get(f's{j}_id')), 'short_link_id': sub_link, 'title': sub_title,
                                 'image': get(f's{j}_image'), 'photo': sub_photo or None, 'photo_url': ''})
        rows.append({'order': _id(get('order')) or 999, 'n': i, 'id': _id(get('id')), 'short_link_id': _id(get('link')),
                     'title': get('title'), 'note': get('note'), 'image': get('image'), 'photo': photo or None,
                     'photo_url': '', 'primary': bool(get('primary')), 'visible': bool(get('visible')),
                     'sublinks': sublinks})
    rows.sort(key=lambda r: (r['order'], r['n']))
    ads = {'short_link_id': _id(form.get('ads_link')), 'title': field(form, 'ads_title').strip(),
           'text': field(form, 'ads_text').strip(), 'button': field(form, 'ads_button').strip()}

    upload = form.get('header_photo')
    header = {'name': field(form, 'header_name').strip(), 'tagline': field(form, 'header_tagline').strip(),
              'photo': await upload.read(links.MAX_PHOTO + 1) if isinstance(upload, UploadFile) and upload.filename else None,
              'reset_photo': bool(form.get('header_reset'))}
    error = await links.save(session, [
        links.ButtonInput(r['short_link_id'] or 0, r['title'], r['note'], r['image'], r['primary'], r['visible'],
                          photo=r['photo'], keep_photo_of=r['id'],
                          sublinks=[links.SublinkInput(s['short_link_id'] or 0, s['title'], s['image'], s['photo'], s['id']) for s in r['sublinks']])
        for r in rows], ads, header)
    if error:
        return await _form(request, session, user, rows, ads, header, error=error, status_code=422)
    return RedirectResponse(request.app.url_path_for('admin_links_page') + '?notice=saved', status_code=303,
                            headers=NO_INDEX)


@router.post('/short-link', name='admin_links_page_short_link')
async def page_short_link(request: Request, session: AsyncSession = Depends(db.get_session)):
    """A new analytics short link, made from the link picker without leaving the page (same checks as in
    the analytics section). JSON in and out: {id, slug, target_url, title} or {error}."""
    user = await authorize(request, session, 'links_page.manage', post=True, api=True)
    if user is None:
        return JSONResponse({'error': 'Your session has ended. Please sign in again.'}, status_code=403)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    slug = shortlinks.normalize_slug(str(data.get('slug') or ''))[:64]
    target_url = str(data.get('target_url') or '').strip()[:2048]
    title = str(data.get('title') or '').strip()[:200]
    error = shortlinks.validate(slug, target_url)
    if not error and slug and await shortlinks.slug_taken(session, slug):
        error = shortlinks.taken_message(slug)
    if error:
        return JSONResponse({'error': error}, status_code=422)
    if not data.get('allow_duplicate') and (same := await shortlinks.duplicate(session, target_url)):
        return JSONResponse({'error': shortlinks.duplicate_message(same), 'duplicate': {
            'id': same.id, 'slug': PREFIX + same.slug, 'target_url': same.target_url, 'title': same.title}}, status_code=409)
    link = ShortLink(slug=slug or await shortlinks.free_auto_slug(session), target_url=target_url, title=title,
                     created_by=user.id)
    session.add(link)
    if not await _save(session):
        return JSONResponse({'error': shortlinks.taken_message(link.slug)}, status_code=422)
    return JSONResponse({'id': link.id, 'slug': PREFIX + link.slug, 'target_url': link.target_url,
                         'title': link.title}, headers=NO_INDEX)
