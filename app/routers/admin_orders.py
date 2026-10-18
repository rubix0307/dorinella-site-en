"""Admin: orders — the numbers, the list with filters, one order with its Stripe messages, the note and the refund.

`orders.view` looks, `orders.refund` writes notes and returns money. The numbers leave out test orders unless the mode says otherwise (a test payment is not
revenue). Money is stored in cents and shown in dollars (app/payments.py `cents`).
"""
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, catalog, db, payments, stripe_api
from ..auth import authorize
from ..models import AdminUser, Order, PaymentEvent
from .admin import NO_INDEX, _page, _to_login

router = APIRouter(prefix='/admin/orders', default_response_class=HTMLResponse, include_in_schema=False)

REASONS = ('The buyer asked for a refund', 'The access could not be opened', 'Paid twice', 'Other')    # English keys of the refund form (app/locale/ru.py)


def _context(user, **extra) -> dict:
    return dict(user=user, cents=payments.cents, labels=payments.STATUS_LABELS, stripe_ready=stripe_api.configured(), test_keys=stripe_api.test_mode(), **extra)


def _title(slug: str) -> str:
    product = catalog.PRODUCTS.get(slug)
    return product.title if product else slug


@router.get('', name='admin_orders')
async def orders(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'orders.view')
    if user is None:
        return _to_login(request)
    await payments.expire_stale(session)
    status = request.query_params.get('status', '')
    status = status if status in payments.STATUSES else ''
    mode = request.query_params.get('mode', 'live')
    mode = mode if mode in dict(payments.MODES) else 'live'
    q = request.query_params.get('q', '').strip()[:80]
    try:
        page = min(100_000, max(1, int(request.query_params.get('page', '1'))))
    except ValueError:
        page = 1
    rows, total, more = await payments.orders_page(session, status=status, mode=mode, q=q, page=page)
    return _page(request, 'admin/orders.html', **_context(user, rows=rows, total=total, more=more, page=page, status=status, mode=mode, q=q, statuses=payments.STATUSES,
                                                          modes=payments.MODES, totals=await payments.totals(session, mode), title=_title))


@router.get('/{order_id:int}', name='admin_order')
async def order_detail(request: Request, order_id: int, notice: str = '', error: str = '', session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'orders.view')
    if user is None:
        return _to_login(request)
    order = await session.get(Order, order_id)
    if order is None:
        return _page(request, 'admin/order.html', status_code=404, **_context(user, order=None, notice='', error=payments.MESSAGES['not_found']))
    buyer = await session.get(AdminUser, order.user_id)
    events = (await session.scalars(select(PaymentEvent).where(PaymentEvent.order_id == order.id).order_by(PaymentEvent.received_at.desc(), PaymentEvent.id.desc()))).all()
    problem = payments.refund_problem(order)
    return _page(request, 'admin/order.html', **_context(user, order=order, buyer=buyer, events=events, product_title=_title(order.product_slug), reasons=REASONS,
                                                         problem=payments.MESSAGES[problem] if problem else '', key=secrets.token_hex(16),
                                                         can_refund=user.can('orders.refund'), notice=payments.MESSAGES.get(notice, ''), error=payments.MESSAGES.get(error, '')))


def _back(request: Request, order_id: int, ok: bool, code: str) -> RedirectResponse:
    where = str(request.app.url_path_for('admin_order', order_id=order_id))
    return RedirectResponse(f'{where}?{"notice" if ok else "error"}={code}', status_code=303, headers=NO_INDEX)


@router.post('/{order_id:int}/note', name='admin_order_note')
async def order_note(request: Request, order_id: int, note: str = Form(''), session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'orders.refund', post=True)
    if user is None:
        return _to_login(request)
    await audit.use_actor(session, 'user', user.id)
    saved = await payments.add_note(session, order_id, note)
    return _back(request, order_id, saved, 'noted' if saved else 'not_found')


@router.post('/{order_id:int}/refund', name='admin_order_refund')
async def order_refund(request: Request, order_id: int, reason: str = Form(''), note: str = Form(''), key: str = Form(''), session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'orders.refund', post=True)
    if user is None:
        return _to_login(request)
    await audit.use_actor(session, 'user', user.id)
    text = ' - '.join(part for part in (reason.strip()[:100] if reason in REASONS else '', note.strip()[:800]) if part)
    ok, code = await payments.refund_order(session, order_id, text, key.strip()[:64] or secrets.token_hex(16))
    return _back(request, order_id, ok, code)
