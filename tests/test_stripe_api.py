"""The thin Stripe layer: the form encoding of a request and the check of a webhook signature (no network, no key)."""
import hashlib
import hmac
import json
import urllib.parse

import pytest

from app import stripe_api

SECRET = 'whsec_unit'


def sign(body: bytes, stamp: int, secret: str = SECRET) -> str:
    return f't={stamp},v1=' + hmac.new(secret.encode(), f'{stamp}.'.encode() + body, hashlib.sha256).hexdigest()


def test_nested_parameters_are_encoded_the_way_stripe_reads_them():
    encoded = stripe_api.form_encode({'mode': 'payment', 'line_items': [{'quantity': 1, 'price_data': {'currency': 'usd', 'unit_amount': 4900}}],
                                      'metadata': {'order': 'LK-1'}, 'flag': True, 'skip': None})
    pairs = dict(urllib.parse.parse_qsl(encoded))
    assert pairs == {'mode': 'payment', 'line_items[0][quantity]': '1', 'line_items[0][price_data][currency]': 'usd',
                     'line_items[0][price_data][unit_amount]': '4900', 'metadata[order]': 'LK-1', 'flag': 'true'}


def test_a_signed_event_is_accepted():
    body = json.dumps({'id': 'evt_1', 'type': 'charge.refunded'}).encode()
    assert stripe_api.verify_webhook(body, sign(body, 1000), SECRET, now=1100)['id'] == 'evt_1'


@pytest.mark.parametrize('case', ['wrong_secret', 'tampered', 'old', 'no_timestamp', 'no_signature', 'empty_secret', 'not_json'])
def test_everything_else_is_refused(case):
    body = json.dumps({'id': 'evt_1'}).encode()
    header, secret, now = sign(body, 1000), SECRET, 1100
    if case == 'wrong_secret':
        header = sign(body, 1000, 'whsec_other')
    elif case == 'tampered':
        body = body.replace(b'evt_1', b'evt_2')
    elif case == 'old':
        now = 1000 + stripe_api.TOLERANCE + 1
    elif case == 'no_timestamp':
        header = header.split(',', 1)[1]
    elif case == 'no_signature':
        header = 't=1000'
    elif case == 'empty_secret':
        secret = ''
    elif case == 'not_json':
        body = b'plain text'
        header = sign(body, 1000)
    with pytest.raises(stripe_api.SignatureError):
        stripe_api.verify_webhook(body, header, secret, now=now)


def test_a_new_signing_secret_can_overlap_the_old_one():
    """While a secret is being rotated Stripe signs with both: any matching v1 is enough."""
    body = b'{"id": "evt_1"}'
    good = sign(body, 1000).split(',v1=')[1]
    assert stripe_api.verify_webhook(body, f't=1000,v1={"0" * 64},v1={good}', SECRET, now=1000)['id'] == 'evt_1'


def test_the_mode_follows_the_key(monkeypatch):
    import dataclasses
    for key, test in (('', True), ('sk_test_x', True), ('sk_live_x', False), ('rk_live_x', False)):
        monkeypatch.setattr(stripe_api, 'settings', dataclasses.replace(stripe_api.settings, stripe_secret_key=key))
        assert stripe_api.test_mode() is test
    monkeypatch.setattr(stripe_api, 'settings', dataclasses.replace(stripe_api.settings, stripe_secret_key='sk_test_x', stripe_webhook_secret=''))
    assert not stripe_api.configured()
    monkeypatch.setattr(stripe_api, 'settings', dataclasses.replace(stripe_api.settings, stripe_secret_key=''))
    with pytest.raises(stripe_api.StripeError):
        stripe_api.client()
