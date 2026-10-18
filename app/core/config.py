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
    # Where the images and files of the guides are served from. Production: a media origin in front of the bucket.
    # Default: /media/, which the app serves itself from MEDIA_DIR (local development).
    media_url: str = field(default_factory=lambda: os.getenv('MEDIA_URL', '/media').rstrip('/'))
    media_dir: str = field(default_factory=lambda: os.getenv('MEDIA_DIR', 'media'))
    # The daily database backup (app/backup.py): a private bucket whose key can only write, and BACKUP_KEY (32 bytes, base64)
    # that seals every dump. Keep a copy of BACKUP_KEY somewhere else: without it the backups cannot be opened.
    backup_s3_key_id: str = field(default_factory=lambda: os.getenv('BACKUP_S3_KEY_ID', ''))
    backup_s3_secret: str = field(default_factory=lambda: os.getenv('BACKUP_S3_APPLICATION_KEY', ''))
    backup_s3_bucket: str = field(default_factory=lambda: os.getenv('BACKUP_S3_BUCKET', ''))
    backup_s3_endpoint: str = field(default_factory=lambda: os.getenv('BACKUP_S3_ENDPOINT', ''))
    backup_s3_region: str = field(default_factory=lambda: os.getenv('BACKUP_S3_REGION', ''))
    backup_key: str = field(default_factory=lambda: os.getenv('BACKUP_KEY', ''))
    backup_hour: int = field(default_factory=lambda: int(os.getenv('BACKUP_HOUR', '3')))     # UTC


settings = Settings()

if not settings.media_url.startswith(('/', 'https://')):
    raise ValueError(f'MEDIA_URL must be a /path or an https:// origin, got {settings.media_url!r}')

if settings.secret_key == _DEV_SECRET:
    # on a real (https) site the dev key is public (it is in the repository): sessions and cookies would be forgeable
    if settings.site_url.startswith('https://'):
        raise RuntimeError('SECRET_KEY must be set on an https site (see .env.example)')
    log.warning('SECRET_KEY is not set - using an insecure development key')
if not settings.visitor_keys:
    log.warning('VISITOR_KEYS is not set - the visitor id cookie is signed with a key derived from SECRET_KEY')
