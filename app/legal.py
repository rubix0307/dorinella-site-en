"""Who sells on the site: the identification of the seller that the law and the payment gateway (Portmone) require on the public pages (the offer, «Оплата, доставка та повернення»,
«Контакти», the footer of every page).

The same data the bot shows in its «Контактна інформація» and its offer. A bank account is never published here.
What the gateway asks to be on the site: the brand and the full name of the person from the application form, the legal and the actual address, the tax number (or the licence),
the phones of the management and of the support, the e-mail, the logos of Visa and Mastercard, the Ukrainian language, the full description of the service and its price in the
state currency. Here the legal and the actual address, and the phone of the management and of the support, are the same: when they differ, change the fields below.
"""
from dataclasses import dataclass

from .core.config import settings


@dataclass(frozen=True)
class Seller:
    brand: str = 'dorinellla'
    name: str = 'Фізична особа-підприємець Марченко Дарина Василівна'
    short: str = 'ФОП Марченко Д. В.'
    tax_id: str = '3756605148'                 # РНОКПП / ІПН
    legal_address: str = '04209, м. Київ, вул. Анни Ахматової, 43'
    actual_address: str = '04209, м. Київ, вул. Анни Ахматової, 43'
    phone: str = '+380 (99) 707-74-22'         # the management
    phone_link: str = '+380997077422'
    support_phone: str = '+380 (99) 707-74-22'
    support_phone_link: str = '+380997077422'
    email: str = 'dorinellla007@gmail.com'

    @property
    def address(self) -> str:
        """One line for the places that name a single address (the legal one)."""
        return self.legal_address

    @property
    def same_address(self) -> bool:
        return self.legal_address == self.actual_address

    @property
    def same_phone(self) -> bool:
        return self.phone_link == self.support_phone_link


SELLER = Seller()
OFFER_DATE = '18.10.2026'


def context() -> dict:
    """What the legal pages need besides their text: the seller and the days a payment can be returned."""
    return {'seller': SELLER, 'offer_date': OFFER_DATE, 'refund_days': settings.refund_days}
