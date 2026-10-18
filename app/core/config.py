"""Settings read from the environment (see .env.example)."""
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_DEV_SECRET = 'dev-only-insecure-secret'  # noqa: S105 — a development default, production refuses it


@dataclass(frozen=True)
class Settings:
    # Public origin for canonical / Open Graph URLs, e.g. https://lolakrissss.com.
    # Empty → derived from the request (fine for local development).
    site_url: str = field(default_factory=lambda: os.getenv('SITE_URL', '').rstrip('/'))
    # SQLAlchemy async URL, e.g. postgresql+asyncpg://user:pass@db:5432/lolakrissss.
    # Empty → analytics is disabled (outbound links still redirect).
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', ''))
    # Signs outbound redirect links so /go/ can't be abused as an open redirect.
    secret_key: str = field(default_factory=lambda: os.getenv('SECRET_KEY', _DEV_SECRET))
    # Sign the visitor id cookie (app/visitors.py), comma-separated: the first signs, any of them is accepted,
    # so a key is rotated by putting the new one first. Empty → derived from SECRET_KEY.
    visitor_keys: tuple[str, ...] = field(default_factory=lambda: tuple(
        k.strip() for k in os.getenv('VISITOR_KEYS', '').split(',') if k.strip()))
    # Sign-in with a Google account (app/google_login.py): the OAuth client from Google Cloud Console.
    # Authorized redirect URI there: <SITE_URL>/admin/login/google/callback. Empty → nobody can sign in.
    google_client_id: str = field(default_factory=lambda: os.getenv('GOOGLE_CLIENT_ID', '').strip())
    google_client_secret: str = field(default_factory=lambda: os.getenv('GOOGLE_CLIENT_SECRET', '').strip())
    # Who may open the course: admin (staff only, the default) | paid (staff and buyers) | public (everybody).
    course_access: str = field(default_factory=lambda: os.getenv('COURSE_ACCESS', 'admin').strip().lower())
    # The daily database backup (app/backup.py): a private bucket whose key can only write, and BACKUP_KEY (32 bytes, base64)
    # that seals every dump. Keep a copy of BACKUP_KEY somewhere else: without it the backups cannot be opened.
    backup_s3_key_id: str = field(default_factory=lambda: os.getenv('BACKUP_S3_KEY_ID', ''))
    backup_s3_secret: str = field(default_factory=lambda: os.getenv('BACKUP_S3_APPLICATION_KEY', ''))
    backup_s3_bucket: str = field(default_factory=lambda: os.getenv('BACKUP_S3_BUCKET', ''))
    backup_s3_endpoint: str = field(default_factory=lambda: os.getenv('BACKUP_S3_ENDPOINT', ''))
    backup_s3_region: str = field(default_factory=lambda: os.getenv('BACKUP_S3_REGION', ''))
    backup_key: str = field(default_factory=lambda: os.getenv('BACKUP_KEY', ''))
    backup_hour: int = field(default_factory=lambda: int(os.getenv('BACKUP_HOUR', '3')))     # UTC
    # Card payments through Stripe Checkout (app/payments.py). A key that starts with sk_test_ is the test mode: no money moves.
    # Without a secret key nobody can pay; without a webhook secret (whsec_…) a payment could never be confirmed.
    stripe_secret_key: str = field(default_factory=lambda: os.getenv('STRIPE_SECRET_KEY', '').strip())
    stripe_webhook_secret: str = field(default_factory=lambda: os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())
    # False (default): only admins and PAYMENTS_TESTERS can start a payment; True: everybody.
    payments_public: bool = field(default_factory=lambda: os.getenv('PAYMENTS_PUBLIC', '0').strip() == '1')
    # E-mail addresses (comma-separated) that may pay while the sales are closed to everybody else.
    payments_testers: tuple[str, ...] = field(default_factory=lambda: tuple(
        e.strip().lower() for e in os.getenv('PAYMENTS_TESTERS', '').split(',') if e.strip()))
    # Days after the payment when a refund is promised on the Refund Policy page (the text shows this number).
    refund_days: int = field(default_factory=lambda: int(os.getenv('REFUND_DAYS', '14')))


settings = Settings()

_WEAK_SECRETS = {_DEV_SECRET, 'change-me', 'changeme', 'secret', ''}


def check_secret_key(key: str, *, production: bool) -> None:
    """The key signs the admin session, the sign-in flow and the tracked links: a known or short one lets anybody forge a root session.
    Production (a real database, or an https site) refuses to start with it; development only warns about the built-in one."""
    weak = key.strip().lower() in _WEAK_SECRETS
    if production and (weak or len(key) < 32):
        raise RuntimeError('SECRET_KEY must be a random value of at least 32 characters (see .env.example)')
    if key == _DEV_SECRET:
        log.warning('SECRET_KEY is not set - using an insecure development key')


check_secret_key(settings.secret_key, production=bool(settings.database_url) or settings.site_url.startswith('https://'))
if not settings.visitor_keys:
    log.warning('VISITOR_KEYS is not set - the visitor id cookie is signed with a key derived from SECRET_KEY')
