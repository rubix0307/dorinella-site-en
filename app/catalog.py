"""Product catalog — single source of truth for the product pages and the JSON API.

Later this moves to a database; routes and templates only depend on these shapes. Prices are whole US dollars.
"""
from dataclasses import dataclass, field
from enum import StrEnum

from . import content


class ProductType(StrEnum):
    GUIDE = 'guide'
    COURSE = 'course'

    @property
    def label(self) -> str:
        return {'guide': 'Guide', 'course': 'Online course'}[self.value]

    @property
    def icon(self) -> str:
        return {'guide': 'book-open-text', 'course': 'circle-play'}[self.value]


@dataclass(frozen=True)
class CardImage:
    """Responsive image for the product card: sources are (static path, real pixel width)."""
    sources: tuple[tuple[str, int], ...]
    width: int
    height: int
    alt: str
    sizes: str
    media_class: str  # background / cropping modifiers of .product-card__media


@dataclass(frozen=True)
class Plan:
    id: str
    label: str            # "Plan 1"
    title: str            # "Basic"
    price: int            # whole US dollars
    features: tuple[str, ...]
    checkout_url: str = ''
    gifts: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    featured: bool = False


@dataclass(frozen=True)
class Product:
    slug: str
    type: ProductType
    title: str
    tagline: str
    summary: str
    template: str
    image: str                 # static path of the main product image
    og_image: str              # static path of the 1200x630 share image
    og_image_alt: str
    card: CardImage
    faq: tuple[tuple[str, str], ...] = ()
    plans: tuple[Plan, ...] = ()
    public: bool = True        # False: not shown anywhere (list, footer, sitemap, API) and the page answers 404; switch on when the product is ready
    on_sale: bool = True       # False: the buy buttons are switched off («Coming soon»)
    legacy_paths: tuple[str, ...] = field(default=())


PRODUCTS: dict[str, Product] = {p.slug: p for p in (
    Product(
        slug='gut-massage-guide',
        type=ProductType.GUIDE,
        title='Gut Massage Guide',
        tagline='A gentle, practical way to ease bloating and look after your gut',
        summary=('Not just a set of techniques, but a calm way to reconnect with your body, ease bloating and support your digestion. '
                 'Built on nutrition knowledge and body-based practice, so you can act with care and still get results.'),
        template='pages/products/gut-massage-guide.html',
        image='img/product-guide-900.webp',
        og_image='img/og-gut-guide.jpg',
        og_image_alt='The Gut Massage Guide',
        card=CardImage(
            sources=(('img/product-guide-500.webp', 500), ('img/product-guide-900.webp', 900)),
            width=500, height=592, alt='The Gut Massage Guide on a pink plate',
            sizes='(min-width: 961px) 390px, 290px',
            media_class='product-card__media--sage product-card__media--bleed',
        ),
        faq=tuple(content.GUIDE_FAQ),
        plans=(Plan(id='guide', label='Guide', title='Gut Massage Guide', price=19, features=('The full guide (PDF)', 'Lifetime access')),),
        on_sale=False,
    ),
    Product(
        slug='massage-course',
        type=ProductType.COURSE,
        title='Abdominal Massage Course',
        tagline='Step-by-step self-massage techniques with animations, preparation, nutrition and drinks for a healthy gut',
        summary=('An interactive online course that explains how the gut works, how to prepare safely and how to do abdominal self-massage '
                 'step by step, with animations. Separate sections cover safety, nutrition and drinks.'),
        template='pages/products/massage-course.html',
        image='img/product-guide-900.webp',
        og_image='img/og-gut-guide.jpg',
        og_image_alt='The Abdominal Massage Course',
        card=CardImage(
            sources=(('img/product-guide-500.webp', 500), ('img/product-guide-900.webp', 900)),
            width=500, height=592, alt='The Abdominal Massage Course',
            sizes='(min-width: 961px) 390px, 290px',
            media_class='product-card__media--sage product-card__media--bleed',
        ),
        plans=(Plan(id='course', label='Course', title='Abdominal Massage Course', price=49, features=('The whole course', 'Access while your account exists')),),
        on_sale=False,
    ),
)}


def listed() -> dict[str, Product]:
    """The products a visitor may see (the hidden ones are in PRODUCTS for the pages that already point at them)."""
    return {slug: p for slug, p in PRODUCTS.items() if p.public}
