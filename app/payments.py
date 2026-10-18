"""Paid access (guides, the course). A placeholder until the card gateway is built: nobody has an entitlement and nobody is offered to pay.

The interface the rest of the site uses (kept stable so the real module can replace this file):
    has_entitlement(session, user, slug)  -> bool   whether the account bought the product
    shown_to(user)                        -> bool   whether the buy button is shown to this person
    offer_for(session, user, slug)        -> dict | None   what the product page shows about buying
    money(amount)                         -> str    «$1,234.50»
"""


async def has_entitlement(session, user, slug: str = '') -> bool:
    return False


def shown_to(user) -> bool:
    return False


async def offer_for(session, user, slug: str = '') -> dict | None:
    return None


def money(amount) -> str:
    return f'${float(amount):,.2f}'
