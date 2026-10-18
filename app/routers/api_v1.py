"""JSON API, version 1 — mounted at /api/v1.

Planned resources (added as the features land): /orders, /payments/{provider}/webhooks.
"""
from fastapi import APIRouter, HTTPException, Request

from ..catalog import PRODUCTS, Product, listed
from ..core.templating import static
from ..learn import GUIDES, SELF_TESTS, SelfTest
from ..schemas import AttemptIn, AttemptOut, BandOut, NoteOut, PlanOut, ProductDetailOut, ProductOut, SelfTestOut, TestQuestionOut
from ..test_attempts import record_attempt

router = APIRouter(prefix='/api/v1', tags=['v1'])


def _product_fields(request: Request, product: Product) -> dict:
    return {
        'slug': product.slug,
        'type': product.type,
        'title': product.title,
        'tagline': product.tagline,
        'url': str(request.app.url_path_for('product_detail', slug=product.slug)),
        'image_url': static(product.image),
    }


def _get(slug: str) -> Product:
    product = PRODUCTS.get(slug)
    if product is None or not product.public:
        raise HTTPException(status_code=404, detail='Product not found')
    return product


@router.get('/products', response_model=list[ProductOut], name='api_product_list')
async def list_products(request: Request):
    return [ProductOut(**_product_fields(request, p)) for p in listed().values()]


@router.get('/products/{slug}', response_model=ProductDetailOut, name='api_product_detail')
async def get_product(request: Request, slug: str):
    product = _get(slug)
    return ProductDetailOut(**_product_fields(request, product), summary=product.summary,
                            plans=_plans(product))


@router.get('/products/{slug}/plans', response_model=list[PlanOut], name='api_product_plans')
async def list_plans(slug: str):
    return _plans(_get(slug))


def _plans(product: Product) -> list[PlanOut]:
    """A price is published only once the product is on sale (the buy box shows none before)."""
    return [_plan(p) for p in product.plans] if product.on_sale else []


def _plan(plan) -> PlanOut:
    return PlanOut(id=plan.id, title=plan.title, price_usd=plan.price, features=list(plan.features),
                   gifts=list(plan.gifts), featured=plan.featured, checkout_url=plan.checkout_url)


# ---------------------------------------------------------------- tests

def _test(slug: str) -> SelfTest:
    test = SELF_TESTS.get(slug)
    if test is None:
        raise HTTPException(status_code=404, detail='Test not found')
    return test


def _public_product_url(request: Request, slug: str | None) -> str | None:
    product = PRODUCTS.get(slug) if slug else None
    return str(request.app.url_path_for('product_detail', slug=slug)) if product is not None and product.public else None


def evaluate(request: Request, test: SelfTest, answers: list[int]) -> AttemptOut:
    """Score an attempt; shared by the JSON API and the HTML form. Never diagnoses: a warning sign turns the advice into
    "see a doctor first" and takes the massage and paid-material links away."""
    score = test.score(answers)
    band = test.band_for(score)
    alert = test.alerted(answers)
    guide = GUIDES.get(test.guide_slug) if test.guide_slug else None
    massage = band.massage and not alert
    return AttemptOut(
        score=score, max_score=test.max_score,
        band=BandOut(key=band.key, title=band.title, text=band.text, tips=list(band.tips), doctor=band.doctor),
        notes=[NoteOut(text=text, warning=warning) for text, warning in test.notes_for(answers)],
        alert=alert, massage=massage,
        guide_url=str(request.app.url_path_for('guide', slug=guide.slug)) if guide else None,
        paid_guide_url=_public_product_url(request, test.paid_guide_slug) if massage else None,
        course_url=_public_product_url(request, test.course_slug) if massage else None,
    )


@router.get('/tests/{slug}', response_model=SelfTestOut, name='api_test_detail')
async def get_test(slug: str):
    test = _test(slug)
    return SelfTestOut(slug=test.slug, title=test.title,
                       questions=[TestQuestionOut(text=q.text, options=[label for label, _ in q.options]) for q in test.questions])


@router.post('/tests/{slug}/evaluate', response_model=AttemptOut, name='api_test_evaluate')
async def evaluate_test(request: Request, slug: str, attempt: AttemptIn):
    """Score the answers and keep them, anonymously, for the admin's statistics."""
    test = _test(slug)
    if problems := test.validate(attempt.answers):
        raise HTTPException(status_code=422, detail=problems)
    result = evaluate(request, test, attempt.answers)
    await record_attempt(request, test, attempt.answers)
    return result
