"""A fake Stripe for the payment tests: no network, no key, no real charge. The fixtures set the shop up in test mode and replace `stripe_api.client`."""
import dataclasses
import hashlib
import hmac
import itertools
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import catalog, course, payments, stripe_api
from app.main import app
from app.routers import payments as payments_router

WEBHOOK_SECRET = 'whsec_test'
SLUG = course.PRODUCT
PRICE = 4900                                        # cents: catalog.PRODUCTS[SLUG].plans[0].price * 100


class FakeStripe:
    """What the site asks of Stripe, answered from memory; the test steers `down`, `paid`, `refund_status`."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.retrieved: list[str] = []
        self.refunds: list[dict] = []
        self.down = False
        self.session_state: dict[str, Any] = {'payment_status': 'unpaid', 'status': 'open'}
        self.refund_status = 'succeeded'

    async def create_checkout_session(self, params: dict, idempotency_key: str) -> dict:
        if self.down:
            raise stripe_api.StripeError('down')
        self.created.append({**params, 'idempotency_key': idempotency_key})
        n = len(self.created)
        return {'id': f'cs_test_{n}', 'url': f'https://checkout.stripe.test/c/pay/cs_test_{n}'}

    async def retrieve_checkout_session(self, session_id: str) -> dict:
        if self.down:
            raise stripe_api.StripeError('down')
        self.retrieved.append(session_id)
        order = self.created[-1]
        return {'id': session_id, 'client_reference_id': order['client_reference_id'], 'amount_total': PRICE, 'currency': 'usd', 'payment_intent': 'pi_test_1',
                **self.session_state}

    async def create_refund(self, payment_intent: str, amount: int, idempotency_key: str, metadata: dict) -> dict:
        if self.down:
            raise stripe_api.StripeError('down')
        self.refunds.append({'payment_intent': payment_intent, 'amount': amount, 'key': idempotency_key, 'metadata': metadata})
        return {'id': f're_test_{len(self.refunds)}', 'status': self.refund_status}


@pytest.fixture
def stripe(monkeypatch):
    """Stripe is set up with a test key, only admins and testers may pay, the course opens for buyers (COURSE_ACCESS=paid)."""
    def configured(settings):
        return dataclasses.replace(settings, stripe_secret_key='sk_test_unit', stripe_webhook_secret=WEBHOOK_SECRET, payments_public=False, payments_testers=(),
                                   refund_days=14, course_access='paid')

    for module in (payments, stripe_api, course, payments_router):
        monkeypatch.setattr(module, 'settings', configured(module.settings))
    fake = FakeStripe()
    monkeypatch.setattr(stripe_api, 'client', lambda: fake)
    monkeypatch.setattr(payments, 'FETCH_GAP', 0.0)
    return fake


@pytest.fixture
def people(accounts, stripe):
    accounts.add('boss@example.test', role='root')
    accounts.add('buyer@example.test', role='user')
    accounts.add('other@example.test', role='user')
    cashier = accounts.group('Orders', ['orders.view', 'orders.refund'])
    accounts.add('cashier@example.test', role='admin', groups=[cashier])
    viewer = accounts.group('Orders view', ['orders.view'])
    accounts.add('viewer@example.test', role='admin', groups=[viewer])
    return {name: accounts.login(f'{name}@example.test') for name in ('boss', 'buyer', 'other', 'cashier', 'viewer')}


def anonymous() -> TestClient:
    return TestClient(app, follow_redirects=False)


def sign(body: bytes, secret: str = WEBHOOK_SECRET, stamp: int | None = None) -> str:
    stamp = int(time.time()) if stamp is None else stamp
    return f't={stamp},v1=' + hmac.new(secret.encode(), f'{stamp}.'.encode() + body, hashlib.sha256).hexdigest()


_ids = itertools.count(1)


def session_event(number: str, *, kind: str = 'checkout.session.completed', status: str = 'paid', amount: int = PRICE, currency: str = 'usd',
                  event_id: str | None = None, intent: str = 'pi_test_1') -> dict:
    return {'id': event_id or f'evt_{next(_ids)}', 'type': kind, 'data': {'object': {
        'id': 'cs_test_1', 'client_reference_id': number, 'payment_status': status, 'amount_total': amount, 'currency': currency, 'payment_intent': intent}}}


def refund_event(intent: str = 'pi_test_1', refunded: int = PRICE, event_id: str | None = None) -> dict:
    return {'id': event_id or f'evt_{next(_ids)}', 'type': 'charge.refunded', 'data': {'object': {
        'id': 'ch_test_1', 'payment_intent': intent, 'amount': PRICE, 'amount_refunded': refunded}}}


def send(event: dict, secret: str = WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return anonymous().post('/stripe/webhook', content=body, headers={'stripe-signature': sign(body, secret), 'content-type': 'application/json'})


def order_of(accounts) -> dict:
    return accounts.rows('SELECT * FROM orders ORDER BY id DESC LIMIT 1')[0]


def start(client, slug: str = SLUG):
    """The buyer ticks the box and presses the button: returns (the order number, the response)."""
    response = client.post(f'/checkout/{slug}', data={'agree': '1'})
    return response


def paid_order(accounts, people, who: str = 'boss') -> dict:
    """An order made by `who` and paid by a webhook."""
    assert start(people[who]).status_code == 303
    number = order_of(accounts)['number']
    assert send(session_event(number)).status_code == 200
    return order_of(accounts)


def sellable(monkeypatch, on_sale: bool = True) -> None:
    """The course product is on sale (it is switched off in the catalog until it is ready)."""
    monkeypatch.setitem(catalog.PRODUCTS, SLUG, dataclasses.replace(catalog.PRODUCTS[SLUG], on_sale=on_sale))
