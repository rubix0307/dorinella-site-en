"""Paying through Stripe Checkout: who may start a payment, what the signed webhook changes (once), refunds that take the access away, the buyer's pages."""
import dataclasses

from app import catalog, course, payments
from tests.stripe_fake import PRICE, SLUG, anonymous, order_of, paid_order, refund_event, sellable, send, session_event, start

pytest_plugins = ['tests.stripe_fake']          # the fixtures `stripe` and `people`


def test_the_price_in_cents_is_the_catalog_price():
    assert PRICE == catalog.PRODUCTS[SLUG].plans[0].price * 100 == payments.price_cents(catalog.PRODUCTS[SLUG])
    assert payments.money(49) == '$49.00' and payments.cents(4900) == '$49.00'


def test_only_staff_and_testers_can_start_a_payment_while_sales_are_closed(accounts, people, stripe, monkeypatch):
    assert people['buyer'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 404
    login = anonymous().post(f'/checkout/{SLUG}', data={'agree': '1'})
    assert login.status_code == 303 and login.headers['location'] == '/login'
    assert not accounts.rows('SELECT 1 FROM orders')
    assert people['boss'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 303
    assert len(accounts.rows('SELECT 1 FROM orders')) == 1
    for module in (payments, payments.stripe_api):          # a named tester may pay too
        monkeypatch.setattr(module, 'settings', dataclasses.replace(module.settings, payments_testers=('buyer@example.test',)))
    assert people['buyer'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 303
    assert people['other'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 404


def test_the_box_with_the_legal_texts_has_to_be_ticked(accounts, people, stripe):
    refused = people['boss'].post(f'/checkout/{SLUG}')
    assert refused.status_code == 303 and refused.headers['location'] == f'/products/{SLUG}/#buy'
    assert not accounts.rows('SELECT 1 FROM orders') and not stripe.created


def test_a_forged_post_from_another_site_is_refused(accounts, people, stripe):
    people['boss'].headers['origin'] = 'https://evil.example'
    assert people['boss'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 403
    assert not accounts.rows('SELECT 1 FROM orders')


def test_starting_a_payment_makes_the_order_and_sends_the_buyer_to_stripes_page(accounts, people, stripe):
    response = start(people['boss'])
    assert response.status_code == 303 and response.headers['location'] == 'https://checkout.stripe.test/c/pay/cs_test_1'
    row = order_of(accounts)
    assert row['status'] == 'created' and row['amount'] == PRICE and row['currency'] == 'usd' and row['test_mode'] == 1
    assert row['terms_version'] and row['terms_accepted_at'] and row['stripe_session_id'] == 'cs_test_1'
    sent = stripe.created[0]
    assert sent['client_reference_id'] == row['number'] and sent['customer_email'] == 'boss@example.test' and sent['mode'] == 'payment'
    assert sent['line_items'][0]['price_data']['unit_amount'] == PRICE and sent['line_items'][0]['price_data']['currency'] == 'usd'
    assert sent['payment_intent_data']['receipt_email'] == 'boss@example.test'            # Stripe e-mails the receipt
    assert sent['success_url'].endswith(f'/checkout/result/{row["number"]}') and sent['cancel_url'].endswith(f'/products/{SLUG}/')
    assert sent['idempotency_key'] == row['number']


def test_a_second_try_replaces_the_unpaid_first_one(accounts, people, stripe):
    start(people['boss'])
    start(people['boss'])
    assert [r['status'] for r in accounts.rows('SELECT status FROM orders ORDER BY id')] == ['expired', 'created']


def test_when_stripe_is_down_the_order_fails_and_nothing_breaks(accounts, people, stripe):
    stripe.down = True
    response = start(people['boss'])
    number = order_of(accounts)['number']
    assert response.status_code == 303 and response.headers['location'] == f'/checkout/result/{number}'
    assert order_of(accounts)['status'] == 'failed'
    page = people['boss'].get(f'/checkout/result/{number}')
    assert page.status_code == 200 and 'did not go through' in page.text


def test_the_public_sale_needs_the_switch_the_product_and_an_open_course(accounts, people, stripe, monkeypatch):
    def public(value=True, mode='paid'):
        for module in (payments, course):
            monkeypatch.setattr(module, 'settings', dataclasses.replace(module.settings, payments_public=value, course_access=mode))
    sellable(monkeypatch)
    assert people['buyer'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 404            # PAYMENTS_PUBLIC is off
    public()
    assert people['buyer'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 303
    public(mode='admin')                                                                                # a customer would get nothing: not offered
    assert people['other'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 404
    public()
    sellable(monkeypatch, on_sale=False)                                                                # the product is switched off
    assert people['other'].post(f'/checkout/{SLUG}', data={'agree': '1'}).status_code == 404


# ---------------------------------------------------------------- the webhook

def test_a_webhook_with_a_bad_signature_is_refused_and_changes_nothing(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    assert send(session_event(number), secret='whsec_wrong').status_code == 400
    assert anonymous().post('/stripe/webhook', content=b'{}').status_code == 400
    assert order_of(accounts)['status'] == 'created' and not accounts.rows('SELECT 1 FROM payment_events')


def test_without_a_webhook_secret_the_endpoint_is_off(accounts, people, stripe, monkeypatch):
    from app.routers import payments as router
    monkeypatch.setattr(router, 'settings', dataclasses.replace(router.settings, stripe_webhook_secret=''))
    assert send(session_event('LK-000000-AAAAAAAA')).status_code == 503


def test_a_paid_session_opens_the_product_once_however_often_it_is_delivered(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    event = session_event(number, event_id='evt_same')
    assert send(event).json() == {'received': True, 'result': 'applied'}
    assert send(event).json()['result'] == 'duplicate'                                    # Stripe retries: the same event id
    send(session_event(number))                                                           # another event about the same payment
    row = order_of(accounts)
    assert row['status'] == 'paid' and row['paid_at'] and row['stripe_payment_intent'] == 'pi_test_1' and not row['needs_review']
    assert len(accounts.rows('SELECT * FROM entitlements')) == 1
    assert len(accounts.rows("SELECT * FROM payment_events WHERE result = 'applied'")) == 1


def test_only_the_stored_fields_of_an_event_are_kept(accounts, people, stripe):
    start(people['boss'])
    event = session_event(order_of(accounts)['number'])
    event['data']['object'].update({'customer_details': {'name': 'Jane Doe', 'email': 'jane@example.test', 'address': {'line1': '1 Main St'}}})
    send(event)
    stored = accounts.rows('SELECT payload FROM payment_events')[0]['payload']
    assert 'Jane' not in stored and 'Main St' not in stored and 'jane@' not in stored


def test_a_payment_in_another_amount_is_not_a_payment(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    assert send(session_event(number, amount=100)).json()['result'] == 'rejected'
    assert send(session_event(number, currency='eur')).json()['result'] == 'rejected'
    row = order_of(accounts)
    assert row['status'] == 'created' and row['needs_review'] == 1 and not accounts.rows('SELECT 1 FROM entitlements')


def test_an_unpaid_session_and_unknown_orders_change_nothing(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    assert send(session_event(number, status='unpaid')).json()['result'] == 'ignored'
    assert send(session_event('LK-000000-AAAAAAAA')).json()['result'] == 'ignored'
    assert send(session_event('not-an-order')).json()['result'] == 'ignored'
    assert send({'id': 'evt_x', 'type': 'customer.created', 'data': {'object': {}}}).json()['result'] == 'ignored'
    assert order_of(accounts)['status'] == 'created' and not accounts.rows('SELECT 1 FROM entitlements')


def test_an_expired_session_closes_the_order_and_a_late_payment_still_opens_the_access(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    send(session_event(number, kind='checkout.session.expired', status='unpaid'))
    assert order_of(accounts)['status'] == 'expired'
    send(session_event(number))                                                           # the money arrived after we gave up: it is real
    row = order_of(accounts)
    assert row['status'] == 'paid' and row['needs_review'] == 1 and len(accounts.rows('SELECT 1 FROM entitlements')) == 1


def test_a_failed_delayed_payment_fails_the_order(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    assert send(session_event(number, kind='checkout.session.async_payment_failed', status='unpaid')).json()['result'] == 'applied'
    assert order_of(accounts)['status'] == 'failed'


def test_a_second_paid_order_of_the_same_product_is_flagged_for_a_look(accounts, people, stripe):
    first = paid_order(accounts, people)
    second_start = people['boss'].post(f'/checkout/{SLUG}', data={'agree': '1'})
    assert second_start.headers['location'] == f'/products/{SLUG}/#buy'                    # already bought: no second order from the button
    accounts.exec("INSERT INTO orders (number, user_id, product_slug, amount, currency, status, test_mode, terms_version, terms_accepted_at) "
                  "SELECT 'LK-000000-BBBBBBBB', user_id, product_slug, amount, currency, 'created', test_mode, terms_version, terms_accepted_at FROM orders WHERE id = :i", i=first['id'])
    send(session_event('LK-000000-BBBBBBBB'))
    row = accounts.rows("SELECT * FROM orders WHERE number = 'LK-000000-BBBBBBBB'")[0]
    assert row['status'] == 'paid' and row['needs_review'] == 1 and len(accounts.rows('SELECT 1 FROM entitlements')) == 1


# ---------------------------------------------------------------- access and refunds

def test_a_test_purchase_opens_the_course_for_staff_and_testers_only(accounts, people, stripe, monkeypatch):
    paid_order(accounts, people, 'boss')
    assert people['boss'].get(course.BASE).status_code == 200
    accounts.exec("UPDATE orders SET user_id = (SELECT id FROM admin_users WHERE email = 'buyer@example.test')")
    accounts.exec("UPDATE entitlements SET user_id = (SELECT id FROM admin_users WHERE email = 'buyer@example.test')")
    assert people['buyer'].get(course.BASE).status_code == 404                              # a test order opens nothing for a customer
    for module in (payments, payments.stripe_api):
        monkeypatch.setattr(module, 'settings', dataclasses.replace(module.settings, payments_testers=('buyer@example.test',)))
    assert people['buyer'].get(course.BASE).status_code == 200                              # a named tester sees what they paid for


def test_a_full_refund_closes_the_access_and_a_repeat_changes_nothing(accounts, people, stripe):
    paid_order(accounts, people, 'boss')
    event = refund_event(event_id='evt_refund')
    assert send(event).json()['result'] == 'applied'
    assert send(event).json()['result'] == 'duplicate'
    row = order_of(accounts)
    assert row['status'] == 'refunded' and row['refunded_amount'] == PRICE
    assert accounts.rows('SELECT revoked_at FROM entitlements')[0]['revoked_at']


def test_a_partial_refund_keeps_the_access_and_a_late_second_one_finishes_it(accounts, people, stripe):
    paid_order(accounts, people)
    send(refund_event(refunded=1000))
    row = order_of(accounts)
    assert row['status'] == 'partially_refunded' and row['refunded_amount'] == 1000
    assert accounts.rows('SELECT revoked_at FROM entitlements')[0]['revoked_at'] is None
    send(refund_event(refunded=500))                                                        # an older, smaller running total arrives late: ignored
    assert order_of(accounts)['refunded_amount'] == 1000
    send(refund_event(refunded=PRICE))
    assert order_of(accounts)['status'] == 'refunded' and accounts.rows('SELECT revoked_at FROM entitlements')[0]['revoked_at']


def test_a_dispute_flags_the_order(accounts, people, stripe):
    paid_order(accounts, people)
    send({'id': 'evt_d', 'type': 'charge.dispute.created', 'data': {'object': {'id': 'dp_1', 'payment_intent': 'pi_test_1', 'amount': PRICE}}})
    assert order_of(accounts)['needs_review'] == 1


# ---------------------------------------------------------------- the buyer's pages

def test_the_page_the_buyer_returns_to_asks_stripe_when_the_webhook_is_late(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    waiting = people['boss'].get(f'/checkout/result/{number}')
    assert waiting.status_code == 200 and 'Waiting for the payment' in waiting.text and stripe.retrieved == ['cs_test_1']
    stripe.session_state = {'payment_status': 'paid', 'status': 'complete'}
    done = people['boss'].get(f'/checkout/result/{number}')
    assert 'your payment went through' in done.text and order_of(accounts)['status'] == 'paid'
    assert len(accounts.rows('SELECT 1 FROM entitlements')) == 1
    assert people['boss'].get(f'/checkout/result/{number}').status_code == 200            # the order is settled: no more questions
    assert len(stripe.retrieved) == 2


def test_the_result_page_is_private_to_the_buyer(accounts, people, stripe):
    start(people['boss'])
    number = order_of(accounts)['number']
    assert people['other'].get(f'/checkout/result/{number}').status_code == 404
    assert anonymous().get(f'/checkout/result/{number}').status_code == 303
    assert people['boss'].get('/checkout/result/LK-000000-AAAAAAAA').status_code == 404
    assert 'no-store' in people['boss'].get(f'/checkout/result/{number}').headers['cache-control']


def test_the_account_lists_the_purchases(accounts, people, stripe):
    paid_order(accounts, people)
    import asyncio

    from app import db
    from app.routers.account import account_page  # noqa: F401 — the page passes `purchases`

    async def run():
        async with db.new_session() as session:
            from sqlalchemy import select

            from app.models import AdminUser
            user = await session.scalar(select(AdminUser).where(AdminUser.email == 'boss@example.test'))
            return await payments.purchases_for(session, user)

    items = asyncio.run(run())
    assert len(items) == 1 and items[0]['title'] == catalog.PRODUCTS[SLUG].title and items[0]['price'] == '$49.00' and items[0]['test']
    assert items[0]['refundable_until'] is not None and items[0]['open_path'] == course.BASE
