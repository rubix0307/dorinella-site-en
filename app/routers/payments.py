"""Paying on the site: the button sends a signed-in person to Stripe's hosted Checkout; Stripe sends them back and, separately, tells the site by a signed webhook.

The page the buyer returns to is never believed: it only names an order, and the status comes from Stripe (the webhook, or one direct question,
see app/payments.py). The webhook is public, so it first checks the signature and answers 2xx only for an event it has stored.
"""
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, db, payments, stripe_api
from ..auth import current_admin, same_origin
from ..core.config import settings
from ..core.templating import absolute_url, render

log = logging.getLogger(__name__)

router = APIRouter(default_response_class=HTMLResponse, include_in_schema=False)
NO_STORE = {'Cache-Control': 'private, no-store', 'X-Robots-Tag': 'noindex, nofollow'}
MAX_BODY = 256 * 1024                                       # an event is a few kilobytes


def _to(request: Request, name: str, **params) -> RedirectResponse:
    return RedirectResponse(str(request.app.url_path_for(name, **params)), status_code=303, headers=NO_STORE)


@router.get('/refund/', name='refund')
async def refund_policy(request: Request):
    return render(request, 'pages/refund.html')


@router.post('/checkout/{slug}', name='checkout')
async def checkout(request: Request, slug: str, agree: str = Form(''), session: AsyncSession | None = Depends(db.get_optional_session)):
    """Makes the order and sends the buyer to Stripe's page. The box with the Terms, Privacy Policy and Refund Policy has to be ticked."""
    user = await current_admin(request, session) if session is not None else None
    if user is None or session is None:
        return _to(request, 'login')
    if not same_origin(request):
        raise HTTPException(status_code=403)
    if not payments.can_pay(user, slug) or payments.product_for(slug) is None:
        raise HTTPException(status_code=404)
    back = str(request.app.url_path_for('product_detail', slug=slug))
    if agree != '1':                                        # the consent is asked on the page and checked here
        return RedirectResponse(back + '#buy', status_code=303, headers=NO_STORE)
    await audit.use_actor(session, 'user', user.id)
    try:
        order = await payments.create_order(session, user, slug)
    except ValueError:                                      # already bought
        return RedirectResponse(back + '#buy', status_code=303, headers=NO_STORE)
    success = absolute_url(request, str(request.app.url_path_for('checkout_result', number=order.number)))
    try:
        url = await payments.open_session(session, order, user.email, success, absolute_url(request, back))
    except stripe_api.StripeError:                          # the result page says it did not start
        return _to(request, 'checkout_result', number=order.number)
    return RedirectResponse(url, status_code=303, headers=NO_STORE)


@router.get('/checkout/result/{number}', name='checkout_result')
async def checkout_result(request: Request, number: str, session: AsyncSession | None = Depends(db.get_optional_session)):
    """What became of an order, for the person who made it."""
    user = await current_admin(request, session) if session is not None else None
    if user is None or session is None:
        return _to(request, 'login')
    order = await payments.by_number(session, number)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404)
    if order.status == payments.CREATED:                    # the webhook may be a moment late: ask Stripe itself
        audit.use_system('stripe')
        await payments.settle(session, order)
        await session.refresh(order)
    product = payments.product_for(order.product_slug)
    response = render(request, 'pages/checkout_result.html', order=order, product=product, price=payments.cents(order.amount),
                      access=await payments.has_entitlement(session, user, order.product_slug))
    response.headers.update(NO_STORE)
    return response


@router.post('/stripe/webhook', name='stripe_webhook')
async def stripe_webhook(request: Request, session: AsyncSession | None = Depends(db.get_optional_session)):
    """Stripe's notification. Signed with the endpoint secret; a repeated event id is answered 200 and changes nothing. A failure to apply answers 500,
    so Stripe delivers the event again."""
    if session is None or not settings.stripe_webhook_secret:
        return JSONResponse({'error': 'payments are not set up'}, status_code=503)
    if int(request.headers.get('content-length') or 0) > MAX_BODY:
        return JSONResponse({'error': 'too big'}, status_code=413)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({'error': 'too big'}, status_code=413)
    try:
        event = stripe_api.verify_webhook(body, request.headers.get('stripe-signature', ''), settings.stripe_webhook_secret)
    except stripe_api.SignatureError as exc:
        log.warning('payments: a webhook was refused: %s', exc)
        return JSONResponse({'error': 'bad signature'}, status_code=400)
    audit.use_system('stripe')
    try:
        result = await payments.apply_event(session, event)
    except Exception:
        log.exception('payments: event %s could not be applied', event.get('id'))
        return JSONResponse({'error': 'could not be applied'}, status_code=500)
    return JSONResponse({'received': True, 'result': result})
