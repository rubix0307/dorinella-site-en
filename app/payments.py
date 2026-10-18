"""Paid access (guides, the course) through Stripe Checkout, and the orders behind it.

The life of an order: `created` (the buyer was sent to Stripe's page) -> `paid` | `expired` | `failed`; `paid` -> `partially_refunded` | `refunded`.
Stripe is told nothing but the order number, the price and the buyer's e-mail; it tells us back by a signed webhook (app/routers/payments.py), and the
page the buyer returns to asks Stripe once more, so a late webhook costs nothing. A status changes in `_paid` / `_refunded` / `apply_event` only, and every
webhook is stored once by its Stripe event id (`payment_events`), so a repeated delivery changes nothing.

Paying opens the product (`entitlements`); a full refund closes it in the same transaction. With a test key the orders are test ones: they open the
product for admins and PAYMENTS_TESTERS only and are not revenue. Until PAYMENTS_PUBLIC is on (and the product is `on_sale`), only those people can
start a payment. Receipts are Stripe's own e-mails (the order carries the buyer's address as `receipt_email`). Card data never reaches this site.

The interface the rest of the site uses:
    has_entitlement(session, user, slug)  -> bool   whether the account bought the product ('' = the course)
    shown_to(user, slug)                  -> bool   whether the buy button is shown to this person
    offer_for(session, user, slug)        -> dict | None   what the product page shows about buying
    money(amount)                         -> str    dollars: «$1,234.50»
"""
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import catalog, course, legal, stripe_api
from .core.config import settings
from .models import AdminUser, Entitlement, Order, PaymentEvent

log = logging.getLogger(__name__)

CREATED, PAID, EXPIRED, FAILED, PARTIAL, REFUNDED = 'created', 'paid', 'expired', 'failed', 'partially_refunded', 'refunded'
STATUSES = (CREATED, PAID, PARTIAL, REFUNDED, EXPIRED, FAILED)
STATUS_LABELS = {CREATED: 'Awaiting payment', PAID: 'Paid', PARTIAL: 'Partially refunded', REFUNDED: 'Refunded', EXPIRED: 'Expired', FAILED: 'Failed'}
HELD = (PAID, PARTIAL, REFUNDED)               # money was taken
ORDER_TTL = timedelta(minutes=35)              # Stripe lets a Checkout Session live at least 30 minutes
NUMBER = re.compile(r'LK-\d{6}-[A-Z2-9]{8}')
MODES = (('live', 'Live'), ('test', 'Test'), ('all', 'All'))


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def money(amount) -> str:
    """Dollars: 1234.5 -> «$1,234.50»."""
    return f'${float(amount):,.2f}'


def cents(amount: int) -> str:
    return money(amount / 100)


def new_number() -> str:
    """LK-261018-K7M2XQ4P: not guessable, short enough for a receipt and a support message."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return f'LK-{now():%y%m%d}-' + ''.join(secrets.choice(alphabet) for _ in range(8))


def product_for(slug: str = '') -> catalog.Product | None:
    """A product that has a price. '' is the course."""
    product = catalog.PRODUCTS.get(slug or course.PRODUCT)
    return product if product is not None and product.plans else None


def price_cents(product: catalog.Product) -> int:
    return product.plans[0].price * 100


# ---------------------------------------------------------------- who may buy

def is_tester(user: Any) -> bool:
    """The accounts the owner named in PAYMENTS_TESTERS: they may pay while the sales are closed to everybody else."""
    return user is not None and (getattr(user, 'email', '') or '').lower() in settings.payments_testers


def _sale_open(product: catalog.Product) -> bool:
    """On sale to everybody: payments are public and the product is switched on. A customer of the course would get nothing while its access is
    «admin», so then nobody is offered to pay."""
    if not (settings.payments_public and product.on_sale):
        return False
    return product.slug != course.PRODUCT or course.access_mode() != 'admin'


def sales_open(slug: str = '') -> bool:
    product = product_for(slug)
    return product is not None and _sale_open(product)


def shown_to(user, slug: str = '') -> bool:
    """Whether the buy button is shown: to everybody when the product is on sale, else to admins and testers (they see why it does not work yet)."""
    product = product_for(slug)
    return user is not None and product is not None and (_sale_open(product) or course.is_staff(user) or is_tester(user))


def can_pay(user, slug: str = '') -> bool:
    return stripe_api.configured() and shown_to(user, slug)


def warn_misconfigured() -> None:
    """Settings with which a customer pays and gets nothing are logged loudly (nothing is changed, the site still starts)."""
    if not settings.payments_public:
        return
    if not stripe_api.configured():
        log.critical('payments: PAYMENTS_PUBLIC is on but STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET is empty: nobody can pay')
    elif stripe_api.test_mode():
        log.critical('payments: PAYMENTS_PUBLIC is on but the Stripe key is a test one: customers pay nothing and get NO access; use the live key')
    if course.access_mode() == 'admin':
        log.critical('payments: PAYMENTS_PUBLIC is on but COURSE_ACCESS=admin: the course is not offered; set COURSE_ACCESS=paid')


async def offer_for(session: AsyncSession, user, slug: str = '') -> dict | None:
    """What the product page shows about buying, to this person: None (nothing), or a dict for the card."""
    product = product_for(slug)
    if product is None or not shown_to(user, product.slug):
        return None
    return {'slug': product.slug, 'title': product.title, 'price': money(product.plans[0].price), 'bought': await has_entitlement(session, user, product.slug),
            'ready': stripe_api.configured(), 'test': stripe_api.test_mode(), 'refund_days': settings.refund_days}


# ---------------------------------------------------------------- access

async def _open_entitlement(session: AsyncSession, user_id: int, slug: str) -> Entitlement | None:
    return await session.scalar(select(Entitlement).where(Entitlement.user_id == user_id, Entitlement.product_slug == slug, Entitlement.revoked_at.is_(None)))


async def has_entitlement(session: AsyncSession, user: AdminUser | None, slug: str = '') -> bool:
    """Whether this person has an open purchase of the product. A purchase made with a test key counts for admins and testers only."""
    if user is None:
        return False
    row = await _open_entitlement(session, user.id, slug or course.PRODUCT)
    return row is not None and (not row.test_mode or course.is_staff(user) or is_tester(user))


async def _grant(session: AsyncSession, order: Order) -> bool:
    """Opens the product for the buyer. False when they already have it open (a second paid order of the same product): the money is real, an admin looks."""
    if await session.scalar(select(Entitlement.id).where(Entitlement.order_id == order.id)) is not None:
        return True
    held = await _open_entitlement(session, order.user_id, order.product_slug)
    if held is not None:
        if not (held.test_mode and not order.test_mode):             # an open test purchase makes way for a real one; anything else is a second purchase
            return False
        held.revoked_at = now()
    session.add(Entitlement(user_id=order.user_id, product_slug=order.product_slug, order_id=order.id, test_mode=order.test_mode))
    return True


async def _revoke(session: AsyncSession, order: Order) -> None:
    row = await session.scalar(select(Entitlement).where(Entitlement.order_id == order.id))
    if row is not None and row.revoked_at is None:
        row.revoked_at = now()


# ---------------------------------------------------------------- checkout

async def by_number(session: AsyncSession, number: str, *, lock: bool = False) -> Order | None:
    query = select(Order).where(Order.number == number[:32])
    return await session.scalar(query.with_for_update() if lock else query)


async def create_order(session: AsyncSession, user: AdminUser, slug: str) -> Order:
    """The order for one purchase of the product, with the terms the buyer ticked. PermissionError: not allowed to pay; ValueError: already bought."""
    product = product_for(slug)
    if product is None or not can_pay(user, product.slug):
        raise PermissionError('payment is not available')
    held = await _open_entitlement(session, user.id, product.slug)
    if held is not None and not (held.test_mode and not stripe_api.test_mode()):     # a test purchase does not block a real one
        raise ValueError('already bought')
    for old in (await session.scalars(select(Order).where(Order.user_id == user.id, Order.product_slug == product.slug, Order.status == CREATED))).all():
        old.status = EXPIRED                                         # the earlier unpaid try: one open order at a time
    order = Order(number=new_number(), user_id=user.id, product_slug=product.slug, amount=price_cents(product), currency='usd', status=CREATED,
                  test_mode=stripe_api.test_mode(), terms_version=legal.TERMS_VERSION, terms_accepted_at=now())
    session.add(order)
    await session.commit()
    return order


def session_params(order: Order, product: catalog.Product, email: str, success_url: str, cancel_url: str) -> dict:
    meta = {'order': order.number, 'product': order.product_slug, 'terms_version': order.terms_version}
    return {
        'mode': 'payment', 'client_reference_id': order.number, 'customer_email': email, 'payment_method_types': ['card'],
        'line_items': [{'quantity': 1, 'price_data': {'currency': order.currency, 'unit_amount': order.amount,
                                                      'product_data': {'name': product.title, 'description': product.tagline[:300]}}}],
        'success_url': success_url, 'cancel_url': cancel_url, 'expires_at': int(time.time() + ORDER_TTL.total_seconds()), 'metadata': meta,
        'payment_intent_data': {'receipt_email': email, 'description': f'{product.title} ({order.number})', 'metadata': meta},
    }


async def open_session(session: AsyncSession, order: Order, email: str, success_url: str, cancel_url: str) -> str:
    """Makes the order's Stripe Checkout Session; returns the address of Stripe's page. The order becomes `failed` when Stripe cannot be reached
    (StripeError is raised after that; the order stays for the record, the buyer starts again)."""
    product = product_for(order.product_slug)
    assert product is not None
    try:
        created = await stripe_api.client().create_checkout_session(session_params(order, product, email, success_url, cancel_url), order.number)
        url = str(created['url'])
        order.stripe_session_id = str(created['id'])[:100]
    except (stripe_api.StripeError, KeyError) as exc:
        order.status = FAILED
        await session.commit()
        log.error('payments: the Checkout Session of %s was not created: %s', order.number, exc)
        raise stripe_api.StripeError('checkout session') from exc
    await session.commit()
    return url


# ---------------------------------------------------------------- what Stripe says

def _id(value: Any) -> str | None:
    """An expandable Stripe field is an id or an object with one."""
    if isinstance(value, dict):
        value = value.get('id')
    return str(value)[:100] if value else None


async def _paid(session: AsyncSession, order: Order, obj: dict) -> tuple[str, str | None]:
    """A Checkout Session says it was paid. Returns (result, error)."""
    if obj.get('payment_status') != 'paid':
        return 'ignored', None                                       # a delayed method that is not settled yet: another event follows
    if obj.get('amount_total') != order.amount or str(obj.get('currency', '')).lower() != order.currency:
        order.needs_review = True                                    # a mismatch is never a payment, whatever Stripe took
        return 'rejected', f'the amount differs: the order {order.amount} {order.currency}, Stripe {obj.get("amount_total")} {obj.get("currency")}'
    if order.status in HELD:
        return 'ignored', None
    late = order.status in (EXPIRED, FAILED)
    order.status, order.paid_at = PAID, now()
    order.stripe_payment_intent = _id(obj.get('payment_intent')) or order.stripe_payment_intent
    await session.flush()
    if not await _grant(session, order) or late:
        order.needs_review = True                                    # the same person paid twice for one product, or the money came after we gave up
    return 'applied', None


async def _refunded(session: AsyncSession, order: Order, total: int) -> str:
    """Stripe says `total` cents of the order went back (a running total, so a repeat or an out-of-order event is harmless). A full refund closes the access."""
    if order.status not in HELD:
        order.needs_review = True
        return 'ignored'
    total = min(max(total, order.refunded_amount), order.amount)
    if total == order.refunded_amount:
        return 'ignored'
    order.refunded_amount = total
    order.status = REFUNDED if total >= order.amount else PARTIAL
    await session.flush()
    if order.status == REFUNDED:
        await _revoke(session, order)
    return 'applied'


async def _order_for(session: AsyncSession, kind: str, obj: dict) -> Order | None:
    if kind.startswith('checkout.session.'):
        number = str(obj.get('client_reference_id') or (obj.get('metadata') or {}).get('order') or '')
        return await by_number(session, number, lock=True) if NUMBER.fullmatch(number) else None
    intent = _id(obj.get('payment_intent'))
    if intent is None:
        return None
    return await session.scalar(select(Order).where(Order.stripe_payment_intent == intent).with_for_update())


async def _apply(session: AsyncSession, kind: str, obj: dict) -> tuple[Order | None, str, str | None]:
    order = await _order_for(session, kind, obj)
    if order is None:
        return None, 'ignored', None
    if kind in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        return (order, *await _paid(session, order, obj))
    if kind == 'checkout.session.expired':
        if order.status == CREATED:
            order.status = EXPIRED
            return order, 'applied', None
    elif kind == 'checkout.session.async_payment_failed':
        if order.status == CREATED:
            order.status = FAILED
            return order, 'applied', None
    elif kind == 'charge.refunded':
        return order, await _refunded(session, order, int(obj.get('amount_refunded') or 0)), None
    elif kind == 'charge.dispute.created':
        order.needs_review = True                                    # the card holder disputes the charge: an admin decides about the access
        return order, 'applied', None
    return order, 'ignored', None


def _safe(kind: str, obj: dict) -> dict:
    """The few fields of an event that are kept: ids and amounts, never an address or a name."""
    return {'type': kind, 'object': str(obj.get('id') or '')[:100], 'status': obj.get('payment_status') or obj.get('status'),
            'amount': obj.get('amount_total', obj.get('amount')), 'refunded': obj.get('amount_refunded'), 'currency': obj.get('currency')}


async def apply_event(session: AsyncSession, event: dict) -> str:
    """One verified webhook delivery: stored once by its Stripe id, applied, committed. Returns 'duplicate' for a repeat, else what happened
    ('applied' / 'ignored' / 'rejected'). Raises on a failure to apply, after rolling back: the event is then not stored and Stripe delivers it again."""
    key = str(event.get('id') or '')[:100]
    kind = str(event.get('type') or '')[:60]
    raw = (event.get('data') or {}).get('object')
    obj = raw if isinstance(raw, dict) else {}
    if not key or not kind:
        return 'ignored'
    row = PaymentEvent(event_key=key, kind=kind, payload=_safe(kind, obj), result='ignored')
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return 'duplicate'                                           # the savepoint is undone; the rest of the transaction is untouched
    try:
        order, row.result, row.error = await _apply(session, kind, obj)
        row.order_id = order.id if order else None
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return row.result


FETCH_GAP = 8.0                                                      # seconds: Stripe is asked about one order at most this often (the return page is public to its owner)
_fetched: dict[str, float] = {}


def _may_fetch(number: str) -> bool:
    moment = time.monotonic()
    if len(_fetched) > 2000:
        for key in [k for k, t in _fetched.items() if moment - t > FETCH_GAP]:
            del _fetched[key]
    last = _fetched.get(number)
    if last is not None and moment - last < FETCH_GAP:
        return False
    _fetched[number] = moment
    return True


async def settle(session: AsyncSession, order: Order) -> None:
    """The buyer is back from Stripe's page and the webhook has not arrived yet: ask Stripe itself about the Checkout Session (what the answer says is as
    good as a webhook, and applying it twice is harmless). A failure changes nothing: the webhook will come."""
    if order.status != CREATED or not order.stripe_session_id or not stripe_api.configured() or not _may_fetch(order.number):
        return
    try:
        obj = await stripe_api.client().retrieve_checkout_session(order.stripe_session_id)
    except stripe_api.StripeError as exc:
        log.warning('payments: %s could not be checked: %s', order.number, exc)
        return
    locked = await by_number(session, order.number, lock=True)
    if locked is None:
        return
    if obj.get('payment_status') == 'paid':
        await _paid(session, locked, obj)
    elif obj.get('status') == 'expired' and locked.status == CREATED:
        locked.status = EXPIRED
    await session.commit()


async def expire_stale(session: AsyncSession) -> int:
    """Open orders whose time is out become `expired` (a late payment is still accepted, see `_paid`)."""
    rows = (await session.scalars(select(Order).where(Order.status == CREATED, Order.created_at < now() - ORDER_TTL))).all()
    for order in rows:
        order.status = EXPIRED
    if rows:
        await session.commit()
    return len(rows)


# ---------------------------------------------------------------- refunds (by an admin)

# what the admin is told after a refund: code -> English text (the admin pages translate it, see app/locale/ru.py)
MESSAGES = {
    'refunded': 'The money is on its way back and the access is closed.',
    'noted': 'The note is saved.',
    'note_needed': 'Write a note: why the order is refunded.',
    'not_found': 'Order not found.',
    'not_paid': 'Only a paid order can be refunded.',
    'all_refunded': 'The whole amount has been refunded already.',
    'no_payment': 'The order has no Stripe payment to refund.',
    'stripe_failed': 'Stripe did not accept the refund. Try again, or refund the payment in the Stripe dashboard.',
}


def refund_problem(order: Order) -> str | None:
    """Why this order cannot be refunded here now (a code of MESSAGES), or None."""
    if order.status not in (PAID, PARTIAL):
        return 'not_paid'
    if order.amount - order.refunded_amount <= 0:
        return 'all_refunded'
    if not order.stripe_payment_intent:
        return 'no_payment'
    return None


async def refund_order(session: AsyncSession, order_id: int, note: str, key: str) -> tuple[bool, str]:
    """Refunds the rest of an order through Stripe and, at once, closes the access (the `charge.refunded` webhook that follows finds it done).
    The same `key` twice reaches Stripe as one refund. Returns (ok, a code of MESSAGES)."""
    note = note.strip()
    if not note:
        return False, 'note_needed'
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        return False, 'not_found'
    problem = refund_problem(order)
    if problem or not order.stripe_payment_intent:
        return False, problem or 'no_payment'
    amount = order.amount - order.refunded_amount
    number, intent = order.number, order.stripe_payment_intent
    await session.commit()                                           # no row lock held while Stripe is slow
    try:
        answer = await stripe_api.client().create_refund(intent, amount, f'refund-{number}-{key}'[:255], {'order': number})
    except stripe_api.StripeError as exc:
        log.error('payments: the refund of %s failed: %s', number, exc)
        return False, 'stripe_failed'
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        return False, 'not_found'
    order.note = f'{order.note}\n{now():%Y-%m-%d} refund: {note[:800]}'.strip()
    if answer.get('status') in ('succeeded', 'pending'):
        await _refunded(session, order, order.refunded_amount + amount)
    await session.commit()
    return True, 'refunded'


async def add_note(session: AsyncSession, order_id: int, note: str) -> bool:
    note = note.strip()[:800]
    order = await session.get(Order, order_id)
    if order is None or not note:
        return False
    order.note = f'{order.note}\n{now():%Y-%m-%d} {note}'.strip()
    await session.commit()
    return True


# ---------------------------------------------------------------- the admin's list and the account page

@dataclass
class Totals:
    revenue: int = 0
    refunded: int = 0
    paid: int = 0
    review: int = 0

    @property
    def net(self) -> int:
        return self.revenue - self.refunded


def _scope(query, status: str, mode: str, q: str):
    if status:
        query = query.where(Order.status == status)
    if mode != 'all':
        query = query.where(Order.test_mode.is_(mode == 'test'))
    if q:
        like = f'%{q.lower().replace("%", "").replace("_", "")}%'
        query = query.where(or_(func.lower(Order.number).like(like), func.lower(AdminUser.email).like(like), func.lower(Order.product_slug).like(like)))
    return query


async def orders_page(session: AsyncSession, *, status: str = '', mode: str = 'live', q: str = '', page: int = 1, per: int = 50) -> tuple[list[tuple[Order, str]], int, bool]:
    """(the page of rows (order, buyer's e-mail), the number of matches, whether an older page follows)."""
    base = _scope(select(Order, AdminUser.email).join(AdminUser, AdminUser.id == Order.user_id), status, mode, q)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (await session.execute(base.order_by(Order.created_at.desc(), Order.id.desc()).limit(per + 1).offset((page - 1) * per))).all()
    return [(order, email) for order, email in rows[:per]], total, len(rows) > per


async def totals(session: AsyncSession, mode: str = 'live') -> Totals:
    query = select(Order).where(Order.status.in_(HELD))
    if mode != 'all':
        query = query.where(Order.test_mode.is_(mode == 'test'))
    result = Totals()
    for order in (await session.scalars(query)).all():
        result.paid += 1
        result.revenue += order.amount
        result.refunded += order.refunded_amount
    review = select(func.count()).select_from(Order).where(Order.needs_review.is_(True))
    if mode != 'all':
        review = review.where(Order.test_mode.is_(mode == 'test'))
    result.review = await session.scalar(review) or 0
    return result


async def purchases_for(session: AsyncSession, user: AdminUser) -> list[dict]:
    """What the account page lists: this person's paid orders, newest first, with where to open each product while the access is open."""
    rows = (await session.scalars(select(Order).where(Order.user_id == user.id, Order.status.in_(HELD)).order_by(Order.paid_at.desc(), Order.id.desc()))).all()
    out = []
    for order in rows:
        product = catalog.PRODUCTS.get(order.product_slug)
        out.append({'number': order.number, 'title': product.title if product else order.product_slug, 'price': cents(order.amount), 'paid_at': order.paid_at,
                    'status': order.status, 'label': STATUS_LABELS[order.status], 'test': order.test_mode, 'refunded': cents(order.refunded_amount) if order.refunded_amount else '',
                    'open_path': product.open_path if product and await has_entitlement(session, user, order.product_slug) else '',
                    'refundable_until': aware(order.paid_at) + timedelta(days=settings.refund_days) if order.paid_at and order.status == PAID else None})
    return out
