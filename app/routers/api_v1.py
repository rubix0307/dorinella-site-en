"""JSON API, version 1 — mounted at /api/v1.

Planned resources (added as the features land): /orders, /payments/{provider}/webhooks.
"""
from fastapi import APIRouter, HTTPException, Request

from ..catalog import PRODUCTS, Product, listed
from ..core.templating import static
from ..learn import SELF_TESTS, SelfTest
from ..schemas import AttemptIn, AttemptOut, BandOut, PlanOut, ProductDetailOut, ProductOut, ScaleOut, SelfTestOut, TestQuestionOut
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
                            plans=[_plan(p) for p in product.plans])


@router.get('/products/{slug}/plans', response_model=list[PlanOut], name='api_product_plans')
async def list_plans(slug: str):
    return [_plan(p) for p in _get(slug).plans]


def _plan(plan) -> PlanOut:
    return PlanOut(id=plan.id, title=plan.title, price_uah=plan.price_uah, features=list(plan.features),
                   gifts=list(plan.gifts), featured=plan.featured, checkout_url=plan.checkout_url)


# ---------------------------------------------------------------- tests

def _test(slug: str) -> SelfTest:
    test = SELF_TESTS.get(slug)
    if test is None:
        raise HTTPException(status_code=404, detail='Test not found')
    return test


def evaluate(request: Request, test: SelfTest, answers: list[int]) -> AttemptOut:
    """Score an attempt; shared by the JSON API and the HTML form."""
    course_url = str(request.app.url_path_for('learn_course', slug=test.course_slug)) if test.course_slug else None
    product_url = str(request.app.url_path_for('product_detail', slug=test.product_slug)) if test.product_slug and PRODUCTS[test.product_slug].public else None
    if test.scales:
        return AttemptOut(scales=[
            ScaleOut(key=r.scale.key, title=r.scale.title, description=r.scale.description, score=r.score,
                     min_score=r.min_score, max_score=r.max_score, reference=r.scale.reference,
                     above_reference=r.above, text=r.scale.above_text if r.above else r.scale.typical_text)
            for r in test.scale_results(answers)
        ], course_url=course_url, product_url=product_url)
    score = test.score(answers)
    band = test.band_for(score)
    return AttemptOut(score=score, max_score=test.max_score, score_unit=test.score_unit,
                      band=BandOut(key=band.key, title=band.title, text=band.text),
                      course_url=course_url, product_url=product_url)


@router.get('/tests/{slug}', response_model=SelfTestOut, name='api_test_detail')
async def get_test(slug: str):
    test = _test(slug)
    return SelfTestOut(slug=test.slug, title=test.title, kind='scales' if test.scales else 'score',
                       questions=[TestQuestionOut(text=q.text, options=[label for label, _ in q.options])
                                  for q in test.questions])


@router.post('/tests/{slug}/attempts', response_model=AttemptOut, status_code=201, name='api_test_attempt')
async def create_attempt(request: Request, slug: str, attempt: AttemptIn):
    test = _test(slug)
    if problems := test.validate(attempt.answers):
        raise HTTPException(status_code=422, detail=problems)
    result = evaluate(request, test, attempt.answers)
    await record_attempt(request, test, attempt.answers)
    return result
