"""The /links/ page (the link in social bios): buttons and the ads card, edited in the admin.

A button can only point at an admin short link (/l/<slug>), so every click is in the analytics
table. The bio-* short links lead to the page itself, one per social network, so the analytics
also shows where visitors of the page came from.

A button can have sub-links (sitelinks): small second-level links under it. They follow
the same rule — a short link only — and are shown while their button is, and their short link is active.

A button's image is one of: a network's own logo (static/img/brands/<key>.png), any Lucide icon (app/core/lucide.json, scripts/build_lucide_catalog.mjs), or a photo
uploaded in the admin (kept in the database, served from /links/photo/<id>-<hash>).
"""
import hashlib
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from markupsafe import Markup
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from .i18n import Message, translate
from .models import BioButton, BioPage, BioSublink, ShortLink
from .shortlinks import PREFIX
from .site import AUTHOR_JOB, BRAND, DOMAIN, INSTAGRAM_HANDLE

LOGOS = {'telegram': 'Telegram', 'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube'}
PHOTO = 'photo'
PHOTO_TYPES = {b'RIFF': 'image/webp', b'\x89PNG': 'image/png', b'\xff\xd8\xff': 'image/jpeg'}   # magic → type
MAX_PHOTO = 300 * 1024      # the admin shrinks a photo to 192 px webp in the browser: ~10–30 KB
MAX_BUTTONS = 20


@cache
def lucide() -> dict[str, tuple[str, str]]:
    """name → (inner SVG, search tags)."""
    return json.loads((Path(__file__).parent / 'core' / 'lucide.json').read_text(encoding='utf-8'))


def lucide_svg(name: str, css_class: str = 'icon') -> Markup:
    return Markup('<svg class="{}" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" '
                  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                  ).format(css_class) + Markup(lucide()[name][0]) + Markup('</svg>')  # noqa: S704 — vendored SVG of the icon set


def photo_type(data: bytes) -> str | None:
    return next((kind for magic, kind in PHOTO_TYPES.items() if data.startswith(magic)), None)


@dataclass(frozen=True)
class Sublink:
    href: str
    title: str
    external: bool
    image: str = ''        # optional: a logo key, a Lucide icon name or 'photo'
    photo_url: str = ''

    @property
    def kind(self) -> str:
        return 'logo' if self.image in LOGOS else PHOTO if self.image == PHOTO else 'icon' if self.image in lucide() else ''

    @property
    def svg(self) -> Markup:
        return lucide_svg(self.image)


@dataclass(frozen=True)
class Button:
    href: str
    title: str
    note: str
    image: str
    primary: bool
    external: bool
    photo_url: str = ''
    sublinks: tuple[Sublink, ...] = ()

    @property
    def kind(self) -> str:
        return 'logo' if self.image in LOGOS else PHOTO if self.image == PHOTO else 'icon'

    @property
    def svg(self) -> Markup:
        return lucide_svg(self.image) if self.image in lucide() else lucide_svg('link')


@dataclass(frozen=True)
class Header:
    name: str
    tagline: str
    photo_url: str      # '' = the site's own illustration


@dataclass(frozen=True)
class Ads:
    title: str
    text: str
    button: str
    href: str


def sublink_photo_url(s: BioSublink) -> str:
    return f'/links/photo/s{s.id}-{s.photo_hash}'


def photo_url(b: BioButton) -> str:
    return f'/links/photo/{b.id}-{b.photo_hash}'


def _external(link: ShortLink) -> bool:
    return not link.target_url.startswith((f'https://{DOMAIN}/', f'https://www.{DOMAIN}/'))


def header_photo_url(row: BioPage) -> str:
    return f'/links/photo/header-{row.photo_hash}' if row.photo_hash else ''


async def page(session: AsyncSession | None) -> tuple[list[Button], Ads | None, Header]:
    """What the public page shows: visible buttons whose short link is active, the ads card, the header."""
    default = Header(BRAND, f'{INSTAGRAM_HANDLE} · {AUTHOR_JOB}', '')
    if session is None:
        return [], None, default
    rows = (await session.scalars(select(BioButton).order_by(BioButton.position, BioButton.id))).all()
    buttons = []
    for b in rows:
        if not b.is_visible or (b.short_link and not b.short_link.is_active):
            continue
        subs = tuple(Sublink(PREFIX + s.short_link.slug, s.title, _external(s.short_link), s.image,
                     sublink_photo_url(s) if s.image == PHOTO else '')
                     for s in b.sublinks if s.short_link.is_active)
        if b.short_link is None and not subs:
            continue                                    # a group whose sub-links are all off has nothing to show
        buttons.append(Button(PREFIX + b.short_link.slug if b.short_link else '', b.title, b.note, b.image, b.is_primary,
                              _external(b.short_link) if b.short_link else False,
                              photo_url(b) if b.image == PHOTO else '', subs))
    row = await session.get(BioPage, 1)
    ads = None
    if row and row.ads_short_link and row.ads_short_link.is_active:
        ads = Ads(row.ads_title, row.ads_text, row.ads_button, PREFIX + row.ads_short_link.slug)
    header = Header(row.name, row.tagline, header_photo_url(row)) if row else default
    return buttons, ads, header


async def settings(session: AsyncSession) -> BioPage:
    row = await session.get(BioPage, 1)
    if row is None:
        row = BioPage(id=1)
        session.add(row)
    return row


@dataclass
class SublinkInput:
    short_link_id: int
    title: str
    image: str = ''
    photo: bytes | None = None          # a new upload
    keep_photo_of: int | None = None    # or the photo of this existing sub-link


@dataclass
class ButtonInput:
    short_link_id: int
    title: str
    note: str
    image: str
    primary: bool
    visible: bool
    photo: bytes | None = None      # a new upload
    keep_photo_of: int | None = None  # or the photo of this existing button
    sublinks: list[SublinkInput] = field(default_factory=list)


async def save(session: AsyncSession, buttons: list[ButtonInput], ads: dict, header: dict | None = None) -> str | None:
    """Replace the buttons, the ads card and the header. An error message (an i18n.Message, nothing written) or None.
    header: {name, tagline, photo: new bytes or None, reset_photo: back to the site's portrait}."""
    header = header or {}
    if header.get('photo') and (photo_type(header['photo']) is None or len(header['photo']) > MAX_PHOTO):
        return Message('The photo at the top must be a webp, png or jpeg image up to %(kb)s KB.', kb=MAX_PHOTO // 1024)
    allowed = set((await session.scalars(select(ShortLink.id))).all())
    old = {b.id: b for b in (await session.scalars(select(BioButton).options(undefer(BioButton.photo)))).all()}
    old_subs = {s.id: s for s in (await session.scalars(select(BioSublink).options(undefer(BioSublink.photo)))).all()}
    sub_photos: list[list[tuple[bytes, str] | None]] = []
    if len(buttons) > MAX_BUTTONS:
        return Message('At most %(max)s buttons.', max=MAX_BUTTONS)
    photos: list[tuple[bytes, str] | None] = []
    for n, b in enumerate(buttons, 1):
        name = f'"{b.title}"' if b.title else str(n)
        if not b.title:
            return Message('Button %(name)s: a title is required.', name=n)
        if b.short_link_id and b.short_link_id not in allowed:
            return Message('Button %(name)s: pick a link from the analytics list.', name=name)
        if not b.short_link_id and not b.sublinks:
            return Message('Button %(name)s has no link, so it is a group: add at least one sub-link to it.', name=name)
        photo: tuple[bytes, str] | None = None
        if b.image == PHOTO:
            if b.photo:
                kind = photo_type(b.photo)
                if kind is None or len(b.photo) > MAX_PHOTO:
                    return Message('Button %(name)s: the photo must be a webp, png or jpeg image up to %(kb)s KB.', name=name, kb=MAX_PHOTO // 1024)
                photo = (b.photo, kind)
            elif b.keep_photo_of is not None and (kept_button := old.get(b.keep_photo_of)) is not None and kept_button.photo:
                photo = (kept_button.photo, kept_button.photo_type)
            else:
                return Message('Button %(name)s: upload a photo.', name=name)
        elif b.image not in LOGOS and b.image not in lucide():
            return Message('Button %(name)s: pick an icon from the list.', name=name)
        kept: list[tuple[bytes, str] | None] = []
        for s in b.sublinks:
            if not s.title:
                return Message('Button %(name)s: a sub-link needs a title.', name=name)
            if s.short_link_id not in allowed:
                return Message('Button %(name)s, sub-link "%(sub)s": pick a link from the analytics list.', name=name, sub=s.title)
            if s.image == PHOTO:
                if s.photo:
                    kind = photo_type(s.photo)
                    if kind is None or len(s.photo) > MAX_PHOTO:
                        return Message('Button %(name)s, sub-link "%(sub)s": the photo must be a webp, png or jpeg image up to %(kb)s KB.',
                                       name=name, sub=s.title, kb=MAX_PHOTO // 1024)
                    kept.append((s.photo, kind))
                elif s.keep_photo_of is not None and (kept_sub := old_subs.get(s.keep_photo_of)) is not None and kept_sub.photo:
                    kept.append((kept_sub.photo, kept_sub.photo_type))
                else:
                    return Message('Button %(name)s, sub-link "%(sub)s": upload a photo.', name=name, sub=s.title)
            elif s.image and s.image not in LOGOS and s.image not in lucide():
                return Message('Button %(name)s, sub-link "%(sub)s": pick an icon from the list.', name=name, sub=s.title)
            else:
                kept.append(None)
        photos.append(photo)
        sub_photos.append(kept)
    if ads['short_link_id'] is not None and ads['short_link_id'] not in allowed:
        return Message('Ads card: pick a link from the analytics list.')
    if ads['short_link_id'] is not None and not ads['button']:
        return Message('Ads card: the button needs a text.')

    await session.execute(delete(BioSublink))
    await session.execute(delete(BioButton))
    for n, (b, photo, kept) in enumerate(zip(buttons, photos, sub_photos, strict=True), 1):
        session.add(BioButton(
            position=n, short_link_id=b.short_link_id or None, title=b.title[:80], note=b.note[:120], image=b.image,
            is_primary=b.primary, is_visible=b.visible,
            photo=photo[0] if photo else None, photo_type=photo[1] if photo else '',
            photo_hash=hashlib.sha256(photo[0]).hexdigest()[:16] if photo else '',
            sublinks=[BioSublink(position=m, short_link_id=s.short_link_id, title=s.title[:60], image=s.image,
                                 photo=sp[0] if sp else None, photo_type=sp[1] if sp else '',
                                 photo_hash=hashlib.sha256(sp[0]).hexdigest()[:16] if sp else '')
                      for m, (s, sp) in enumerate(zip(b.sublinks, kept, strict=True), 1)]))
    row = await settings(session)
    row.ads_title, row.ads_text, row.ads_button = ads['title'][:80], ads['text'], ads['button'][:60]
    row.ads_short_link_id = ads['short_link_id']
    if 'name' in header:
        row.name, row.tagline = (header['name'] or BRAND)[:60], header['tagline'][:160]
    if header.get('photo'):
        row.photo, row.photo_type = header['photo'], photo_type(header['photo']) or ''
        row.photo_hash = hashlib.sha256(header['photo']).hexdigest()[:16]
    elif header.get('reset_photo'):
        row.photo, row.photo_type, row.photo_hash = None, '', ''
    await session.commit()
    return None


# Groups of the icon panel, as on lucide.dev (app/core/lucide_categories.json: icon → group, sorted by
# Sonnet agents from the names and tags). Order = order in the panel; (label, icon of its tab).
CATEGORIES = {
    'emoji': ('Emoji', 'smile'), 'people': ('People', 'users'), 'food-beverage': ('Food and drink', 'apple'),
    'nature': ('Nature', 'leaf'), 'animals': ('Animals', 'cat'), 'medical': ('Medical', 'heart-pulse'),
    'sports': ('Sports', 'dumbbell'), 'home': ('Home', 'house'), 'shopping': ('Shopping', 'shopping-bag'),
    'social': ('Social', 'share-2'), 'communication': ('Communication', 'message-circle'), 'mail': ('Mail', 'mail'),
    'multimedia': ('Multimedia', 'music'), 'photography': ('Photography', 'camera'), 'travel': ('Travel', 'plane'),
    'transportation': ('Transport', 'car'), 'weather': ('Weather', 'cloud-sun'), 'seasons': ('Seasons', 'snowflake'),
    'time': ('Time', 'clock'), 'finance': ('Finance', 'wallet'), 'text': ('Text', 'type'), 'files': ('Files', 'file'),
    'layout': ('Layout', 'layout-grid'), 'design': ('Design', 'palette'), 'shapes': ('Shapes', 'shapes'),
    'arrows': ('Arrows', 'arrow-right'), 'navigation': ('Navigation', 'compass'), 'buildings': ('Buildings', 'building'),
    'charts': ('Charts', 'chart-column'), 'devices': ('Devices', 'smartphone'), 'connectivity': ('Connectivity', 'wifi'),
    'notifications': ('Notifications', 'bell'), 'security': ('Security', 'lock'), 'account': ('Account', 'circle-user'),
    'accessibility': ('Accessibility', 'accessibility'), 'cursors': ('Cursors', 'mouse-pointer'),
    'development': ('Development', 'code'), 'gaming': ('Games', 'gamepad-2'), 'math': ('Math', 'calculator'),
    'science': ('Science', 'flask-conical'), 'tools': ('Tools', 'wrench'), 'sustainability': ('Sustainability', 'recycle'),
    'brands': ('Brands', 'badge'), 'other': ('Other', 'ellipsis'),
}


@cache
def lucide_categories() -> dict[str, str]:
    path = Path(__file__).parent / 'core' / 'lucide_categories.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def icon_groups(lang: str = 'en') -> list[dict]:
    """The panel's groups in order: key, label (in the admin's language), tab icon (inner SVG), icon names."""
    cats = lucide_categories()
    names: dict[str, list[str]] = {key: [] for key in CATEGORIES}
    for name in lucide():
        group = cats.get(name)
        names[group if group is not None and group in CATEGORIES else 'other'].append(name)
    return [{'key': key, 'label': translate(label, lang), 'tab': lucide()[tab if tab in lucide() else 'circle'][0], 'icons': names[key]}
            for key, (label, tab) in CATEGORIES.items() if names[key]]


@cache
def catalog_version() -> str:
    """In the icons.json URL: a new catalog or new groups, a new URL (it is cached by the browser for a day)."""
    core = Path(__file__).parent / 'core'
    digest = hashlib.sha256()
    for name in ('lucide.json', 'lucide_categories.json'):
        path = core / name
        digest.update(path.read_bytes() if path.exists() else b'')
    return digest.hexdigest()[:10]

