"""Sign-in with a Google account (OpenID Connect, authorization-code flow with PKCE).

The only way to sign in. Google proves that the person owns an e-mail address; the account of that address is
found (case-insensitively, which is how the existing accounts keep their access) or, for a new address, made as a
plain account. What an account may open is decided by its role and rights, the admin included.

The flow keeps its state in one short-lived signed cookie (`state`, `nonce`, the PKCE verifier): the callback must
bring the same `state` back, so a callback that this browser did not start is refused.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import Request

from .core.config import settings
from .core.templating import absolute_url

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'  # noqa: S105 — a URL, not a secret
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
CALLBACK_PATH = '/admin/login/google/callback'
FLOW_COOKIE = 'dori_google'
FLOW_TTL = 10 * 60


class GoogleError(Exception):
    """The sign-in did not go through; the text is safe to show."""


def configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(request: Request) -> str:
    return absolute_url(request, CALLBACK_PATH)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), f'google-flow\n{payload}'.encode(), hashlib.sha256).hexdigest()


def start(request: Request) -> tuple[str, str]:
    """(the URL to send the browser to, the value of the flow cookie)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    expires = int(time.time()) + FLOW_TTL
    payload = json.dumps([state, nonce, verifier, expires], separators=(',', ':'))
    cookie = f'{_b64(payload.encode())}.{_sign(payload)}'
    query = urllib.parse.urlencode({
        'client_id': settings.google_client_id, 'redirect_uri': redirect_uri(request), 'response_type': 'code',
        'scope': 'openid email profile', 'state': state, 'nonce': nonce, 'prompt': 'select_account',
        'code_challenge': _b64(hashlib.sha256(verifier.encode()).digest()), 'code_challenge_method': 'S256',
    })
    return f'{AUTH_URL}?{query}', cookie


def _read_flow(cookie: str) -> tuple[str, str, str]:
    try:
        raw, signature = cookie.split('.')
        payload = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode()
        state, nonce, verifier, expires = json.loads(payload)
    except (ValueError, TypeError):
        raise GoogleError('Your sign-in session was lost. Please try again.') from None
    if not hmac.compare_digest(_sign(payload), signature) or int(expires) < time.time():
        raise GoogleError('Your sign-in session was lost. Please try again.')
    return state, nonce, verifier


def _post_form(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method='POST',  # noqa: S310 — a fixed https URL
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:      # noqa: S310 — a fixed https URL
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise GoogleError('Google is not available right now. Please try again.') from None


def claims_of(id_token: str) -> dict:
    """The payload of the ID token. It comes straight from Google's token endpoint over TLS in answer to our own
    request, which Google's documentation accepts without checking the signature; what it says is checked below."""
    try:
        body = id_token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except (IndexError, ValueError):
        raise GoogleError('Google sent a reply that could not be read.') from None


def check_claims(claims: dict, nonce: str | None = None) -> tuple[str, str]:
    """The verified e-mail of the person and the name Google gives (may be empty), or GoogleError. `nonce` None: the token did not come from a request of ours
    (One Tap), so there is none to compare."""
    try:
        expired = int(claims.get('exp', 0)) < time.time()
    except (TypeError, ValueError):
        expired = True
    if (claims.get('iss') not in ISSUERS or claims.get('aud') != settings.google_client_id or expired
            or (nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')), nonce))):
        raise GoogleError('The reply from Google failed the checks.')
    email = str(claims.get('email', '')).strip().lower()
    if not email or str(claims.get('email_verified')).lower() != 'true':
        raise GoogleError('The e-mail of this Google account is not verified.')
    return email, ' '.join(str(claims.get('name') or '').split())[:100]


async def finish(request: Request, code: str, state: str) -> tuple[str, str]:
    """Handle the callback: the e-mail Google vouches for and the name."""
    saved_state, nonce, verifier = _read_flow(request.cookies.get(FLOW_COOKIE, ''))
    if not state or not hmac.compare_digest(saved_state, state):
        raise GoogleError('Your sign-in session was lost. Please try again.')
    answer = await asyncio.to_thread(_post_form, TOKEN_URL, {
        'code': code, 'client_id': settings.google_client_id, 'client_secret': settings.google_client_secret,
        'redirect_uri': redirect_uri(request), 'grant_type': 'authorization_code', 'code_verifier': verifier})
    if 'id_token' not in answer:
        raise GoogleError('Google did not confirm the sign-in.')
    return check_claims(claims_of(answer['id_token']), nonce)


# ---------------------------------------------------------------- One Tap

TOKENINFO_URL = 'https://oauth2.googleapis.com/tokeninfo'
# Google's One Tap suggestion is offered on every page for a signed-out browser, except these: the money and admin pages, the sign-in
# endpoints and the legal pages (they must stay free of third-party scripts and of pop-ups over the text a person is reading).
NO_ONE_TAP = ('/admin', '/checkout', '/auth', '/api', '/privacy', '/terms', '/refund')


def _get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:      # noqa: S310 — a fixed https URL
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise GoogleError('Google did not confirm the sign-in.') from None


async def verify_credential(credential: str) -> tuple[str, str]:
    """The e-mail and name behind a One Tap ID token. The token came through the browser, so Google itself is asked to check
    its signature (tokeninfo); the answer is then checked the same way as at the callback."""
    if not credential or len(credential) > 4096:
        raise GoogleError('Google did not confirm the sign-in.')
    claims = await asyncio.to_thread(_get_json, f'{TOKENINFO_URL}?{urllib.parse.urlencode({"id_token": credential})}')
    return check_claims(claims)


def one_tap(request: Request):
    """The tags that show Google's One Tap — on every page except NO_ONE_TAP (the privacy page says so), and nothing for a signed-in browser
    or when Google is not set up. After a sign-in the page reloads (/login forwards the person to their page, any other page just shows
    the signed-in state)."""
    from markupsafe import Markup, escape

    from .auth import SESSION_COOKIE
    if not configured() or request.cookies.get(SESSION_COOKIE) or request.url.path.startswith(NO_ONE_TAP):
        return ''
    return Markup('<script src="https://accounts.google.com/gsi/client" async></script>\n'
                  '<script src="/static/js/google-one-tap.js" defer data-client-id="{}" data-url="/auth/google/one-tap"></script>'
                  ).format(escape(settings.google_client_id))


from .core.templating import templates  # noqa: E402  (after the module's own names: templating imports nothing from here)

templates.env.globals['google_one_tap'] = one_tap
