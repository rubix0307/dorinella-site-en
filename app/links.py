"""The /links/ page (the link in social bios): buttons and the ads card, edited in the admin.

A button can only point at an admin short link (/l/<slug>), so every click is in the analytics
table. The bio-* short links lead to the page itself, one per social network, so the analytics
also shows where visitors of the page came from. The first version was made by a migration
(20261004_links_page).

A button can have sub-links (sitelinks): small second-level links under it. They follow
the same rule — a short link only — and are shown while their button is, and their short link is active.

A button's image is one of: a network's own logo (static/img/brands/<key>.png, from the old
linktree), any Lucide icon (app/core/lucide.json, scripts/build_lucide_catalog.mjs), or a photo
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

from .models import BioButton, BioPage, BioSublink, ShortLink
from .shortlinks import PREFIX

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
    photo_url: str      # '' = the site's own portrait


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
    return not link.target_url.startswith(('https://dorinellla.com/', 'https://www.dorinellla.com/'))


def header_photo_url(row: BioPage) -> str:
    return f'/links/photo/header-{row.photo_hash}' if row.photo_hash else ''


async def page(session: AsyncSession | None) -> tuple[list[Button], Ads | None, Header]:
    """What the public page shows: visible buttons whose short link is active, the ads card, the header."""
    default = Header('Dori', '@dorinellla · бодіпозитивний нутриціолог', '')
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
    """Replace the buttons, the ads card and the header. An error message (nothing written) or None.
    header: {name, tagline, photo: new bytes or None, reset_photo: back to the site's portrait}."""
    header = header or {}
    if header.get('photo') and (photo_type(header['photo']) is None or len(header['photo']) > MAX_PHOTO):
        return f'Фото вгорі: картинка (webp, png, jpeg) до {MAX_PHOTO // 1024} КБ.'
    allowed = set((await session.scalars(select(ShortLink.id))).all())
    old = {b.id: b for b in (await session.scalars(select(BioButton).options(undefer(BioButton.photo)))).all()}
    old_subs = {s.id: s for s in (await session.scalars(select(BioSublink).options(undefer(BioSublink.photo)))).all()}
    sub_photos: list[list[tuple[bytes, str] | None]] = []
    if len(buttons) > MAX_BUTTONS:
        return f'Не більше {MAX_BUTTONS} кнопок.'
    photos: list[tuple[bytes, str] | None] = []
    for n, b in enumerate(buttons, 1):
        name = f'«{b.title}»' if b.title else str(n)
        if not b.title:
            return f'Кнопка {n}: потрібна назва.'
        if b.short_link_id and b.short_link_id not in allowed:
            return f'Кнопка {name}: оберіть посилання зі списку аналітики.'
        if not b.short_link_id and not b.sublinks:
            return f'Кнопка {name} без посилання — це група: додайте їй хоча б одне підпосилання.'
        photo: tuple[bytes, str] | None = None
        if b.image == PHOTO:
            if b.photo:
                kind = photo_type(b.photo)
                if kind is None or len(b.photo) > MAX_PHOTO:
                    return f'Кнопка {name}: фото має бути картинкою (webp, png, jpeg) до {MAX_PHOTO // 1024} КБ.'
                photo = (b.photo, kind)
            elif b.keep_photo_of is not None and (kept_button := old.get(b.keep_photo_of)) is not None and kept_button.photo:
                photo = (kept_button.photo, kept_button.photo_type)
            else:
                return f'Кнопка {name}: завантажте фото.'
        elif b.image not in LOGOS and b.image not in lucide():
            return f'Кнопка {name}: оберіть іконку зі списку.'
        kept: list[tuple[bytes, str] | None] = []
        for s in b.sublinks:
            if not s.title:
                return f'Кнопка {name}: у підпосилання потрібна назва.'
            if s.short_link_id not in allowed:
                return f'Кнопка {name}, підпосилання «{s.title}»: оберіть посилання зі списку аналітики.'
            if s.image == PHOTO:
                if s.photo:
                    kind = photo_type(s.photo)
                    if kind is None or len(s.photo) > MAX_PHOTO:
                        return f'Кнопка {name}, підпосилання «{s.title}»: фото має бути картинкою (webp, png, jpeg) до {MAX_PHOTO // 1024} КБ.'
                    kept.append((s.photo, kind))
                elif s.keep_photo_of is not None and (kept_sub := old_subs.get(s.keep_photo_of)) is not None and kept_sub.photo:
                    kept.append((kept_sub.photo, kept_sub.photo_type))
                else:
                    return f'Кнопка {name}, підпосилання «{s.title}»: завантажте фото.'
            elif s.image and s.image not in LOGOS and s.image not in lucide():
                return f'Кнопка {name}, підпосилання «{s.title}»: оберіть іконку зі списку.'
            else:
                kept.append(None)
        photos.append(photo)
        sub_photos.append(kept)
    if ads['short_link_id'] is not None and ads['short_link_id'] not in allowed:
        return 'Реклама: оберіть посилання зі списку аналітики.'
    if ads['short_link_id'] is not None and not ads['button']:
        return 'Реклама: потрібен текст кнопки.'

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
        row.name, row.tagline = (header['name'] or 'Dori')[:60], header['tagline'][:160]
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
    'emoji': ('Емоції', 'smile'), 'people': ('Люди', 'users'), 'food-beverage': ('Їжа та напої', 'apple'),
    'nature': ('Природа', 'leaf'), 'animals': ('Тварини', 'cat'), 'medical': ('Медицина', 'heart-pulse'),
    'sports': ('Спорт', 'dumbbell'), 'home': ('Дім', 'house'), 'shopping': ('Покупки', 'shopping-bag'),
    'social': ('Соцмережі', 'share-2'), 'communication': ('Спілкування', 'message-circle'), 'mail': ('Пошта', 'mail'),
    'multimedia': ('Мультимедіа', 'music'), 'photography': ('Фото', 'camera'), 'travel': ('Подорожі', 'plane'),
    'transportation': ('Транспорт', 'car'), 'weather': ('Погода', 'cloud-sun'), 'seasons': ('Пори року', 'snowflake'),
    'time': ('Час', 'clock'), 'finance': ('Фінанси', 'wallet'), 'text': ('Текст', 'type'), 'files': ('Файли', 'file'),
    'layout': ('Макет', 'layout-grid'), 'design': ('Дизайн', 'palette'), 'shapes': ('Фігури', 'shapes'),
    'arrows': ('Стрілки', 'arrow-right'), 'navigation': ('Навігація', 'compass'), 'buildings': ('Будівлі', 'building'),
    'charts': ('Графіки', 'chart-column'), 'devices': ('Пристрої', 'smartphone'), 'connectivity': ('Зв’язок', 'wifi'),
    'notifications': ('Сповіщення', 'bell'), 'security': ('Безпека', 'lock'), 'account': ('Акаунт', 'circle-user'),
    'accessibility': ('Доступність', 'accessibility'), 'cursors': ('Курсори', 'mouse-pointer'),
    'development': ('Розробка', 'code'), 'gaming': ('Ігри', 'gamepad-2'), 'math': ('Математика', 'calculator'),
    'science': ('Наука', 'flask-conical'), 'tools': ('Інструменти', 'wrench'), 'sustainability': ('Екологія', 'recycle'),
    'brands': ('Бренди', 'badge'), 'other': ('Інше', 'ellipsis'),
}


@cache
def lucide_categories() -> dict[str, str]:
    path = Path(__file__).parent / 'core' / 'lucide_categories.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def icon_groups() -> list[dict]:
    """The panel's groups in order: key, label, tab icon (inner SVG), icon names."""
    cats = lucide_categories()
    names: dict[str, list[str]] = {key: [] for key in CATEGORIES}
    for name in lucide():
        group = cats.get(name)
        names[group if group is not None and group in CATEGORIES else 'other'].append(name)
    return [{'key': key, 'label': label, 'tab': lucide()[tab if tab in lucide() else 'circle'][0], 'icons': names[key]}
            for key, (label, tab) in CATEGORIES.items() if names[key]]


@cache
def catalog_version() -> str:
    """In the icons.json URL: a new catalog or new groups, a new URL (it is cached by the browser for a day)."""
    core = Path(__file__).parent / 'core'
    digest = hashlib.sha256()
    for name in ('lucide.json', 'lucide_categories.json', 'lucide_uk.json'):
        path = core / name
        digest.update(path.read_bytes() if path.exists() else b'')
    return digest.hexdigest()[:10]


@cache
def lucide_uk() -> dict[str, list[str]]:
    """name → [Ukrainian name (the tooltip), Ukrainian search words]; translated by Sonnet agents."""
    path = Path(__file__).parent / 'core' / 'lucide_uk.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
