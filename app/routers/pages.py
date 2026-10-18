"""HTML pages: the home page, the products, the guide library, the tests, the links page and the legal pages."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from .. import content, course, db, links, payments
from ..auth import current_admin
from ..catalog import PRODUCTS
from ..core.forms import field
from ..core.templating import render
from ..learn import GUIDES, SELF_TESTS
from ..models import BioButton, BioPage, BioSublink
from ..test_attempts import record_attempt
from .api_v1 import evaluate

router = APIRouter(default_response_class=HTMLResponse)


@router.get('/', name='index')
async def index(request: Request):
    return render(request, 'pages/index.html', faq=content.GUIDE_FAQ, guide=PRODUCTS['gut-massage-guide'])


@router.get('/privacy/', name='privacy')
async def privacy(request: Request):
    return render(request, 'pages/privacy.html')


@router.get('/terms/', name='terms')
async def terms(request: Request):
    return render(request, 'pages/terms.html')


@router.get('/links/', name='links')
async def links_page(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    buttons, ads, header = await links.page(session)
    return render(request, 'pages/links.html', links=buttons, ads=ads, header=header)


@router.get('/links/photo/header-{digest}', name='links_header_photo')
async def links_header_photo(digest: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    """The photo at the top of /links/ (set in the admin)."""
    row = await session.get(BioPage, 1, options=[undefer(BioPage.photo)]) if session else None
    if row is None or not row.photo or row.photo_hash != digest:
        raise HTTPException(status_code=404)
    return Response(row.photo, media_type=row.photo_type, headers={
        'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff'})


@router.get('/links/photo/{button_id:int}-{digest}', name='links_photo')
async def links_photo(button_id: int, digest: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    """A button's uploaded photo. The URL carries its hash, so it can be cached for good."""
    button = await session.get(BioButton, button_id, options=[undefer(BioButton.photo)]) if session else None
    if button is None or not button.photo or button.photo_hash != digest:
        raise HTTPException(status_code=404)
    return Response(button.photo, media_type=button.photo_type, headers={
        'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff'})


@router.get('/links/photo/s{sublink_id:int}-{digest}', name='links_sublink_photo')
async def links_sublink_photo(sublink_id: int, digest: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    """A sub-link's uploaded photo, cached for good like a button's."""
    row = await session.get(BioSublink, sublink_id, options=[undefer(BioSublink.photo)]) if session else None
    if row is None or not row.photo or row.photo_hash != digest:
        raise HTTPException(status_code=404)
    return Response(row.photo, media_type=row.photo_type, headers={
        'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff'})


@router.get('/products/', name='product_list')
async def product_list(request: Request):
    return render(request, 'pages/products/index.html')


@router.get('/products/{slug}/', name='product_detail')
async def product_detail(request: Request, slug: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    product = PRODUCTS.get(slug)
    if product is None or not product.public:
        raise HTTPException(status_code=404)
    user = await current_admin(request, session) if session is not None else None
    pay = await payments.offer_for(session, user, slug) if session is not None and user is not None else None
    extra: dict = {'course': course} if slug == 'massage-course' else {}       # the course page lists its programme
    sales_open = payments.sales_open(slug)                               # no price and no buy button for the public before that
    response = render(request, product.template, product=product, faq=product.faq, signed_in=user is not None, pay=pay, sales_open=sales_open,
                      price=payments.money(product.plans[0].price) if product.plans and sales_open else '', **extra)
    if user is not None:
        response.headers['Cache-Control'] = 'private, no-store'          # the buy box differs per account
    return response


async def _owned_guides(session: AsyncSession | None, user) -> set[str]:
    """The slugs of the paid guides this account has bought."""
    if session is None or user is None:
        return set()
    return {g.slug for g in GUIDES.values() if g.product_slug and await payments.has_entitlement(session, user, g.product_slug)}


@router.get('/guides/', name='guide_list')
async def guide_list(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    user = await current_admin(request, session) if session is not None else None
    response = render(request, 'pages/learn/index.html', guides=GUIDES, tests=SELF_TESTS, owned=await _owned_guides(session, user))
    if user is not None:
        response.headers['Cache-Control'] = 'private, no-store'          # the locks differ per account
    return response


@router.get('/guides/{slug}/', name='guide')
async def guide(request: Request, slug: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    found = GUIDES.get(slug)
    if found is None:
        raise HTTPException(status_code=404)
    user = await current_admin(request, session) if session is not None else None
    unlocked = not found.paid or found.slug in await _owned_guides(session, user)
    product = PRODUCTS.get(found.product_slug) if found.product_slug else None
    buy_open = product is not None and product.public and product.on_sale and payments.shown_to(user, product.slug)
    response = render(request, 'pages/learn/guide.html', guide=found, guides=GUIDES, unlocked=unlocked, product=product, buy_open=buy_open,
                      price=payments.money(product.plans[0].price) if product is not None and product.plans else '')
    if found.paid:
        response.headers['Cache-Control'] = 'private, no-store'          # the full text must never sit in a shared cache
    return response


@router.get('/tests/', name='test_list')
async def test_list(request: Request):
    return RedirectResponse(request.app.url_path_for('guide_list') + '#tests', status_code=301)


@router.get('/tests/{slug}/', name='self_test')
async def self_test(request: Request, slug: str):
    test = SELF_TESTS.get(slug)
    if test is None:
        raise HTTPException(status_code=404)
    return render(request, 'pages/tests/test.html', test=test)


@router.post('/tests/{slug}/', name='self_test_submit')
async def self_test_submit(request: Request, slug: str):
    """No-JS fallback: the test form posts here and gets the result page back."""
    test = SELF_TESTS.get(slug)
    if test is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    try:
        answers = [int(field(form, f'q{i}') or '-1') for i in range(len(test.questions))]
    except ValueError:
        answers = []
    if test.validate(answers):
        return render(request, 'pages/tests/test.html', test=test, error='Please answer every question.',
                      status_code=422)
    result = evaluate(request, test, answers)
    consented = field(form, 'consent') == '1'      # answers about digestion are stored only with the person's agreement
    if consented:
        await record_attempt(request, test, answers)
    return render(request, 'pages/tests/test.html', test=test, result=result, saved=consented)
