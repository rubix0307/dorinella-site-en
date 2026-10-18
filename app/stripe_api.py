"""The only code that talks to Stripe: a thin client over its REST API and the check of a webhook signature.

No SDK: three calls (create and read a Checkout Session, create a refund) do not need one, and the signature scheme is a few lines. Everything else in the
site goes through `client()`, which the tests replace with a fake, so no test ever has a key, a network or a charge. Card data never reaches this site:
the buyer pays on the page Stripe hosts.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .core.config import settings

log = logging.getLogger(__name__)

API = 'https://api.stripe.com/v1'
API_VERSION = '2024-06-20'          # pinned: the fields read here (payment_status, amount_total, payment_intent) do not move under us
TIMEOUT = 15
TOLERANCE = 300                     # seconds a webhook timestamp may differ from now (Stripe's own default)


class StripeError(Exception):
    """Stripe refused or could not be reached. The message is safe to log, never to show a buyer."""


class SignatureError(ValueError):
    """The webhook did not come from Stripe (or is a replay)."""


def configured() -> bool:
    """A payment can be started and confirmed: the key to create it and the secret to believe the answer."""
    return bool(settings.stripe_secret_key and settings.stripe_webhook_secret)


def test_mode() -> bool:
    """Only a live key moves money: with a test key, or no key at all, every order is a test one."""
    return not settings.stripe_secret_key.startswith(('sk_live_', 'rk_live_'))


def form_encode(params: dict[str, Any]) -> str:
    """Stripe's form encoding: nested dicts as a[b]=…, lists as a[0]=…, booleans as true/false."""
    pairs: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(f'{prefix}[{key}]' if prefix else str(key), inner)
        elif isinstance(value, (list, tuple)):
            for index, inner in enumerate(value):
                walk(f'{prefix}[{index}]', inner)
        elif value is not None:
            pairs.append((prefix, 'true' if value is True else 'false' if value is False else str(value)))

    walk('', params)
    return urllib.parse.urlencode(pairs)


class StripeClient:
    def __init__(self, secret_key: str):
        self._key = secret_key

    def _request(self, method: str, path: str, params: dict | None, idempotency_key: str | None) -> dict:
        headers = {'Authorization': f'Bearer {self._key}', 'Stripe-Version': API_VERSION}
        data = None
        url = f'{API}{path}'
        if method == 'POST':
            data = form_encode(params or {}).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            if idempotency_key:
                headers['Idempotency-Key'] = idempotency_key
        request = urllib.request.Request(url, data=data, headers=headers, method=method)     # noqa: S310 — a fixed https URL
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:               # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get('error', {}).get('message', '')
            except (ValueError, AttributeError):
                detail = ''
            raise StripeError(f'Stripe answered {exc.code}: {detail}'[:300]) from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise StripeError(f'Stripe could not be reached: {type(exc).__name__}') from exc

    async def _call(self, method: str, path: str, params: dict | None = None, idempotency_key: str | None = None) -> dict:
        return await asyncio.to_thread(self._request, method, path, params, idempotency_key)

    async def create_checkout_session(self, params: dict, idempotency_key: str) -> dict:
        return await self._call('POST', '/checkout/sessions', params, idempotency_key)

    async def retrieve_checkout_session(self, session_id: str) -> dict:
        return await self._call('GET', f'/checkout/sessions/{urllib.parse.quote(session_id, safe="")}')

    async def create_refund(self, payment_intent: str, amount: int, idempotency_key: str, metadata: dict[str, str]) -> dict:
        return await self._call('POST', '/refunds', {'payment_intent': payment_intent, 'amount': amount, 'metadata': metadata}, idempotency_key)


def client() -> StripeClient:
    """The client the site uses. Tests replace this function."""
    if not settings.stripe_secret_key:
        raise StripeError('STRIPE_SECRET_KEY is not set')
    return StripeClient(settings.stripe_secret_key)


def verify_webhook(payload: bytes, header: str, secret: str, *, now: float | None = None) -> dict:
    """The event in a webhook body, after its `Stripe-Signature` header (t=…,v1=…) is checked against the endpoint secret. Raises SignatureError."""
    if not secret:
        raise SignatureError('no webhook secret')
    parts: dict[str, list[str]] = {}
    for item in header.split(','):
        key, _, value = item.strip().partition('=')
        parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts['t'][0])
    except (KeyError, ValueError, IndexError):
        raise SignatureError('no timestamp') from None
    if abs((time.time() if now is None else now) - stamp) > TOLERANCE:
        raise SignatureError('timestamp out of tolerance')
    expected = hmac.new(secret.encode(), f'{stamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get('v1', [])):
        raise SignatureError('signature mismatch')
    try:
        event = json.loads(payload)
    except ValueError:
        raise SignatureError('not JSON') from None
    if not isinstance(event, dict):
        raise SignatureError('not an event')
    return event
