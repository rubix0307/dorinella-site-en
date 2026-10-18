"""Who sells on the site, and the version of the legal texts: what the Privacy Policy, the Terms of Service and the Refund Policy name.

Every value that only the owner knows is a placeholder marked TODO(owner): the pages print it as it is, so a launch with a placeholder left is visible
(tests/test_legal.py lists them). A bank account is never published here. Stripe shows the business name and the support address on the card
statement and in its receipt e-mails from its own dashboard settings, not from this file.
"""
from dataclasses import dataclass

from . import auth, google_login, site, visitors
from .core.config import settings


@dataclass(frozen=True)
class Seller:
    brand: str = site.BRAND
    # TODO(owner): the legal name of whoever receives the money (a company, or you as a sole proprietor) and its type
    name: str = '[Legal name of the seller]'
    # TODO(owner): the registered address; the EU and the UK expect a postal address on the site, California expects a contact for privacy requests
    address: str = '[Street, city, postal code, country]'
    # TODO(owner): the registration / tax number if the seller has one (company number, VAT ID, EIN); empty = the line is not shown
    tax_id: str = ''
    email: str = site.CONTACT_EMAIL
    # TODO(owner): the country whose law governs the Terms and whose courts decide a dispute (consumers keep the protection of their own country)
    governing_law: str = '[Country or state]'

    @property
    def placeholders(self) -> list[str]:
        """The values that are still brackets: the owner has to fill them before the sales open."""
        return [label for label, value in (('name', self.name), ('address', self.address), ('governing_law', self.governing_law)) if value.startswith('[')]


SELLER = Seller()

# The date the texts last changed. It is also the version a buyer accepted at checkout (orders.terms_version): change it with every change of
# the Terms, the Privacy Policy or the Refund Policy that a buyer should agree to again.
TERMS_VERSION = '2026-10-18'
UPDATED = 'October 18, 2026'

# Where the seller's data goes, named on the privacy page. Only processors that the code really calls are listed.
PROCESSORS = (
    ('Stripe, Inc.', 'takes card payments on its own page and sends receipts; we never see or store card numbers', 'https://stripe.com/privacy'),
    ('Google', 'sign-in with a Google account (we receive your e-mail address and name)', 'https://policies.google.com/privacy'),
)


def cookies() -> list[dict]:
    """Every cookie the site's code sets for a visitor, as the privacy page lists them (names come from the code that sets them, so the page cannot drift).
    The admin's own preference cookies (language, time zone, chart) are set only for signed-in staff and are not listed here."""
    return [
        {'name': visitors.COOKIE, 'purpose': 'A random, signed visitor ID. It counts visitors and returning visits without your name, and keeps your test results together; '
                                              'it moves into your account when you sign in. Not set for bots, the admin or the sign-in pages.',
         'lifetime': f'{visitors.MAX_AGE // 86400} days, renewed while you keep visiting', 'essential': False},
        {'name': auth.SESSION_COOKIE, 'purpose': 'Keeps you signed in after you sign in with Google. Not set if you never sign in.',
         'lifetime': f'{auth.SESSION_TTL // 86400} days', 'essential': True},
        {'name': google_login.FLOW_COOKIE, 'purpose': 'Protects the sign-in with Google while it is in progress.',
         'lifetime': f'{google_login.FLOW_TTL // 60} minutes', 'essential': True},
    ]


def context() -> dict:
    """What the legal pages need besides their text: the seller, the version date and the number of refund days."""
    return {'seller': SELLER, 'updated': UPDATED, 'refund_days': settings.refund_days, 'processors': PROCESSORS, 'cookies': cookies()}
