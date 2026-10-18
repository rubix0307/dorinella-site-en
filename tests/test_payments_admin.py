"""The admin's Orders pages: the list and its filters, the totals (test orders are not revenue), notes, and the refund through Stripe."""
from app import payments
from tests.stripe_fake import PRICE, anonymous, order_of, paid_order, send

pytest_plugins = ['tests.stripe_fake']          # the fixtures `stripe` and `people`


def test_the_list_hides_test_orders_unless_asked_and_filters(accounts, people, stripe):
    order = paid_order(accounts, people)
    live = people['viewer'].get('/admin/orders')
    assert live.status_code == 200 and order['number'] not in live.text and 'No orders found' in live.text
    everything = people['viewer'].get('/admin/orders?mode=all')
    assert order['number'] in everything.text and 'boss@example.test' in everything.text
    assert order['number'] in people['viewer'].get('/admin/orders?mode=test&status=paid').text
    assert order['number'] not in people['viewer'].get('/admin/orders?mode=all&status=refunded').text
    assert order['number'] in people['viewer'].get('/admin/orders?mode=all&q=BOSS@').text
    assert order['number'] not in people['viewer'].get('/admin/orders?mode=all&q=nobody-like-this').text
    assert people['viewer'].get('/admin/orders?mode=all&status=nonsense&page=x').status_code == 200


def test_the_totals_count_live_money_only(accounts, people, stripe):
    paid_order(accounts, people)
    assert people['viewer'].get('/admin/orders?mode=live').text.count('$0.00') >= 3
    assert '$49.00' in people['viewer'].get('/admin/orders?mode=test').text
    accounts.exec('UPDATE orders SET test_mode = 0')
    live = people['viewer'].get('/admin/orders?mode=live').text
    assert '$49.00' in live


def test_the_pages_need_the_right_and_a_signed_in_admin(accounts, people, stripe):
    order = paid_order(accounts, people)
    assert people['buyer'].get('/admin/orders').status_code == 403
    assert people['buyer'].get(f'/admin/orders/{order["id"]}').status_code == 403
    assert anonymous().get('/admin/orders').status_code == 303 and anonymous().get(f'/admin/orders/{order["id"]}').status_code == 303
    assert people['viewer'].get(f'/admin/orders/{order["id"]}').status_code == 200
    assert people['viewer'].get('/admin/orders/99999').status_code == 404


def test_the_order_page_shows_the_order_and_what_stripe_said(accounts, people, stripe):
    order = paid_order(accounts, people)
    page = people['cashier'].get(f'/admin/orders/{order["id"]}').text
    assert order['number'] in page and 'pi_test_1' in page and 'checkout.session.completed' in page and 'Refund' in page
    viewer_page = people['viewer'].get(f'/admin/orders/{order["id"]}').text
    assert 'name="reason"' not in viewer_page and 'Add a note' not in viewer_page          # viewing is not writing


def test_a_refund_goes_through_stripe_and_takes_the_access_away(accounts, people, stripe):
    order = paid_order(accounts, people)
    url = f'/admin/orders/{order["id"]}/refund'
    done = people['cashier'].post(url, data={'reason': 'The buyer asked for a refund', 'note': 'by e-mail', 'key': 'k1'})
    assert done.status_code == 303 and 'notice=refunded' in done.headers['location']
    assert stripe.refunds == [{'payment_intent': 'pi_test_1', 'amount': PRICE, 'key': f'refund-{order["number"]}-k1', 'metadata': {'order': order['number']}}]
    row = order_of(accounts)
    assert row['status'] == 'refunded' and row['refunded_amount'] == PRICE and 'by e-mail' in row['note']
    assert accounts.rows('SELECT revoked_at FROM entitlements')[0]['revoked_at']
    again = people['cashier'].post(url, data={'reason': 'Other', 'key': 'k1'})                   # nothing left to refund
    assert 'error=not_paid' in again.headers['location'] and len(stripe.refunds) == 1
    send({'id': 'evt_r', 'type': 'charge.refunded', 'data': {'object': {'payment_intent': 'pi_test_1', 'amount_refunded': PRICE}}})
    assert order_of(accounts)['status'] == 'refunded'                                             # the webhook that follows finds it done


def test_a_refund_needs_the_right_a_note_a_paid_order_and_a_working_stripe(accounts, people, stripe):
    order = paid_order(accounts, people)
    url = f'/admin/orders/{order["id"]}/refund'
    assert people['viewer'].post(url, data={'reason': 'Other', 'note': 'x', 'key': 'a'}).status_code == 403
    assert people['buyer'].post(url, data={'reason': 'Other', 'note': 'x', 'key': 'a'}).status_code == 403
    assert 'error=note_needed' in people['cashier'].post(url, data={'key': 'a'}).headers['location']
    stripe.down = True
    assert 'error=stripe_failed' in people['cashier'].post(url, data={'reason': 'Other', 'key': 'b'}).headers['location']
    assert order_of(accounts)['status'] == 'paid' and not accounts.rows('SELECT 1 FROM entitlements WHERE revoked_at IS NOT NULL')
    stripe.down = False
    assert 'error=not_found' in people['cashier'].post('/admin/orders/99999/refund', data={'reason': 'Other', 'key': 'c'}).headers['location']
    accounts.exec("UPDATE orders SET status = 'created', stripe_payment_intent = NULL")
    assert 'error=not_paid' in people['cashier'].post(url, data={'reason': 'Other', 'key': 'd'}).headers['location']
    assert not stripe.refunds


def test_a_note_is_saved_for_those_who_may_refund(accounts, people, stripe):
    order = paid_order(accounts, people)
    url = f'/admin/orders/{order["id"]}/note'
    assert people['viewer'].post(url, data={'note': 'nope'}).status_code == 403
    assert 'error=not_found' in people['cashier'].post(url, data={'note': '   '}).headers['location']
    assert 'notice=noted' in people['cashier'].post(url, data={'note': 'Wrote to the buyer'}).headers['location']
    assert 'Wrote to the buyer' in order_of(accounts)['note']
    assert 'Wrote to the buyer' in people['viewer'].get(f'/admin/orders/{order["id"]}').text


def test_a_message_code_in_the_address_shows_only_known_text(accounts, people, stripe):
    order = paid_order(accounts, people)
    page = people['cashier'].get(f'/admin/orders/{order["id"]}?notice=<script>alert(1)</script>&error=noted').text
    assert '<script>alert(1)</script>' not in page and 'The note is saved.' in page


def test_every_message_code_has_a_russian_translation():
    from app.locale.ru import RU
    for text in list(payments.MESSAGES.values()) + list(payments.STATUS_LABELS.values()):
        assert text in RU, text


def test_the_menu_entry_leads_to_the_orders():
    from app import permissions
    assert permissions.first_section({'orders.view'}) == 'admin_orders'
