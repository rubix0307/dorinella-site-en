"""Where the files of the Telegram posts are kept: a PUBLIC S3-compatible bucket (TG_MEDIA_S3_*), or a folder (dev), or memory (tests).

A file gets a key `<prefix>/<post id>/<24 random hex>.<ext>` (nobody can guess it; the real file name is NOT in the public address, it stays in the database row), is
stored with its Content-Type and a long Cache-Control, and is
read by anybody from `public_url(key)`: the editor shows it from there and Telegram may fetch a small one itself. Nothing is listed, and the only things
the key of the bucket must be able to do are write, read and delete objects. Old files (keys `posts/<id>/<hex>.<ext>`, the private store of app/tg_store.py)
stay readable and deletable: every function here sends such a key to tg_store.

Everything here is blocking: call it through asyncio.to_thread. Failures of `delete` are logged, never raised (a file left behind is clutter only).
"""
import contextlib
import hashlib
import io
import logging
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from boto3.session import Session
from botocore.config import Config

from . import tg_store
from .core.config import normalise_media_prefix, settings

log = logging.getLogger('media_store')

# New keys are `<24hex>.<ext>`; keys of the older form `<24hex>-<safe name>` stay valid.
KEY = re.compile(r'^[A-Za-z0-9._-]{1,40}(?:/[A-Za-z0-9._-]{1,40}){0,2}/\d{1,9}/[0-9a-f]{24}(?:-[A-Za-z0-9._-]{1,80}|\.[a-z0-9]{1,8})?$')
CACHE_CONTROL = 'public, max-age=31536000, immutable'
CHUNK = 1024 * 1024
# What a browser may run when it opens the object from our public origin: such a type is never stored as it is.
UNSAFE_MIMES = ('text/html', 'application/xhtml+xml', 'image/svg+xml', 'text/xml', 'application/xml', 'text/javascript', 'application/javascript', 'application/x-javascript', 'text/css')
MEMORY: dict[str, tuple[bytes, str]] = {}          # the fake bucket of the tests: key -> (bytes, content type)


class MediaStorageError(RuntimeError):
    """The storage is configured wrongly (the message names the settings to fix)."""


@dataclass(frozen=True)
class Stored:
    key: str
    public_url: str | None         # None: the file is only served by the site (the folder backend, old files)
    size: int
    mime: str
    sha256: str


def s3_problem() -> str | None:
    """None when the bucket settings are complete (or no bucket is wanted at all); else a sentence naming the missing environment variables.
    Wanted: TG_MEDIA_BACKEND=s3, or any of the bucket / key id / secret is set."""
    missing = [name for name, value in (('TG_MEDIA_S3_BUCKET', settings.tg_media_bucket), ('TG_MEDIA_S3_KEY_ID', settings.tg_media_key_id),
                                        ('TG_MEDIA_S3_APPLICATION_KEY', settings.tg_media_secret)) if not value]
    wanted = settings.tg_media_backend == 's3' or len(missing) < 3
    if settings.tg_media_backend in ('local', 'memory') or not wanted or not missing:
        return None
    return 'Сховище файлів (S3) налаштоване не повністю: не задано ' + ', '.join(missing)


def backend_name() -> str:
    """'s3' (a COMPLETE bucket config), 'memory' / 'local' (TG_MEDIA_BACKEND), else 'local'. A half-set bucket is never reported as s3 (see s3_problem)."""
    forced = settings.tg_media_backend
    if forced in ('local', 'memory'):
        return forced
    return 's3' if settings.tg_media_bucket and not s3_problem() else 'local'


def storage_status() -> dict:
    """{'backend': 's3'|'local'|'memory', 'public': files have an address Telegram and the editor can read, 'problem': a sentence or None}.
    For the router / template: a banner when `problem` is set or `public` is False in production."""
    problem = s3_problem()
    backend = backend_name()
    public = public_url(f'{settings.tg_media_prefix}/0/{"0" * 24}.jpg') is not None
    if backend == 's3' and not public and not problem:
        problem = 'Не вдається побудувати публічну адресу файлів: задайте TG_MEDIA_PUBLIC_URL, TG_MEDIA_S3_ENDPOINT або TG_MEDIA_S3_REGION'
    return {'backend': backend, 'public': public, 'problem': problem}


def is_legacy(key: str) -> bool:
    return bool(tg_store.KEY.match(key))


_CLIENT = None
_CLIENT_SIG: tuple | None = None
_CLIENT_LOCK = threading.Lock()


def client():
    """One boto3 client for the process (built on first use, on its own Session: the default one is not thread-safe); rebuilt only when the settings change."""
    global _CLIENT, _CLIENT_SIG
    sig = (settings.tg_media_endpoint, settings.tg_media_region, settings.tg_media_key_id, settings.tg_media_secret)
    with _CLIENT_LOCK:
        if _CLIENT is None or sig != _CLIENT_SIG:
            session = Session()
            _CLIENT = session.client('s3', endpoint_url=settings.tg_media_endpoint or None, region_name=settings.tg_media_region or None,
                                     aws_access_key_id=settings.tg_media_key_id, aws_secret_access_key=settings.tg_media_secret,
                                     config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, connect_timeout=5, read_timeout=30, retries={'max_attempts': 2}))
            _CLIENT_SIG = sig
        return _CLIENT


def _extension(file_name: str) -> str:
    match = re.search(r'\.([A-Za-z0-9]{1,8})$', (file_name or '').strip())
    return match.group(1).lower() if match else ''


def new_key(post_id: int, file_name: str, prefix: str | None = None) -> str:
    """`<prefix>/<post id>/<24 random hex>.<ext>`: the original name never reaches the public address. `prefix`: another folder than the posts' (the articles)."""
    ext = _extension(file_name)
    return f'{normalise_media_prefix(prefix or settings.tg_media_prefix)}/{int(post_id)}/{secrets.token_hex(12)}' + (f'.{ext}' if ext else '')


def _check(key: str) -> None:
    if not KEY.match(key) or '..' in key.split('/'):
        raise ValueError(f'not a key of a post file: {key!r}')


def _local_path(key: str) -> Path:
    """Where the folder backend keeps the key; never outside the folder (a `..` segment or a link out of it is refused)."""
    root = Path(tg_store.local_root()).resolve()
    path = (root / key).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f'not a key of a post file: {key!r}')
    return path


_WARNED: set[str] = set()


def _warn_once(tag: str, message: str) -> None:
    if tag not in _WARNED:
        _WARNED.add(tag)
        log.warning(message)


def public_url(key: str) -> str | None:
    """Where anybody can read the file; None for a folder backend and for old files."""
    if is_legacy(key):
        return None
    name = backend_name()
    if name == 'local':
        return None
    if name == 'memory':
        return f'https://media.test/{quote(key)}'
    base = settings.tg_media_public_url.rstrip('/') or (f'{settings.tg_media_endpoint.rstrip("/")}/{settings.tg_media_bucket}' if settings.tg_media_endpoint else '')
    if not base and settings.tg_media_bucket and settings.tg_media_region:         # real AWS S3: the virtual-hosted address
        base = f'https://{settings.tg_media_bucket}.s3.{settings.tg_media_region}.amazonaws.com'
    if not base:
        _warn_once('no-public-url', 'the bucket has no public address: set TG_MEDIA_PUBLIC_URL (or TG_MEDIA_S3_ENDPOINT / TG_MEDIA_S3_REGION)')
        return None
    return f'{base}/{quote(key)}'


def current_url(key: str, stored: str | None) -> str | None:
    """The address to hand out for a file that was put into a public bucket (`stored` is what was saved then): rebuilt from the key and the config of NOW
    (a changed domain or CDN would make the saved one stale); the saved one only when the config cannot build one. A file the row has no address for
    (the folder backend, old files) has none: the site serves it, or the bytes are sent."""
    if not stored:
        return None
    return public_url(key) or stored


def safe_mime(mime: str, kind: str = '') -> str:
    """The Content-Type an object is stored with. A document is opaque bytes (octet-stream, downloaded, never shown), and a type a browser would run
    (html, svg, xml, scripts) is never stored as such, whatever the uploader claimed."""
    mime = (mime or '').split(';')[0].strip().lower()
    if kind == 'document' or not mime or mime in UNSAFE_MIMES or mime.endswith(('+xml', '/xml')) or '/' not in mime:
        return 'application/octet-stream'
    return mime


def _digest(data) -> tuple[str, int]:
    """(sha256, size) of bytes or of a file (read from its start in chunks; the position is put back at the start)."""
    digest = hashlib.sha256()
    if isinstance(data, (bytes, bytearray, memoryview)):
        digest.update(data)
        return digest.hexdigest(), len(data)
    data.seek(0)
    size = 0
    while chunk := data.read(CHUNK):
        digest.update(chunk)
        size += len(chunk)
    data.seek(0)
    return digest.hexdigest(), size


def put(post_id: int, file_name: str, data, mime: str, kind: str = '', prefix: str | None = None) -> Stored:
    """Stores `data` (bytes, or a binary file read from its start) under a new key. `kind` 'document' (or an unsafe type) is stored as an attachment of
    type application/octet-stream. Raises whatever the backend raises: the caller turns it into a message and removes what it stored."""
    mime = safe_mime(mime, kind)
    problem = s3_problem()
    if problem:
        raise MediaStorageError(problem)
    key = new_key(post_id, file_name, prefix)
    _check(key)
    sha, size = _digest(data)
    body = io.BytesIO(data) if isinstance(data, (bytes, bytearray, memoryview)) else data
    name = backend_name()
    if name == 's3':
        extra = {'ContentType': mime, 'CacheControl': CACHE_CONTROL}
        if mime == 'application/octet-stream':
            extra['ContentDisposition'] = f"attachment; filename*=UTF-8''{quote(file_name or 'file', safe='')}"
        if settings.tg_media_acl:
            extra['ACL'] = settings.tg_media_acl
        client().upload_fileobj(body, settings.tg_media_bucket, key, ExtraArgs=extra)
    elif name == 'memory':
        MEMORY[key] = (body.read(), mime)
    else:
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.part')
        with tmp.open('wb') as out:
            shutil.copyfileobj(body, out, CHUNK)
        tmp.replace(path)
    return Stored(key, public_url(key), size, mime, sha)


def get(key: str) -> bytes:
    if is_legacy(key):
        return tg_store.get(key)
    _check(key)
    name = backend_name()
    local = _local_path(key)
    if name == 's3':
        try:
            return client().get_object(Bucket=settings.tg_media_bucket, Key=key)['Body'].read()
        except Exception:
            if local.is_file():
                return local.read_bytes()
            raise
    if name == 'memory':
        try:
            return MEMORY[key][0]
        except KeyError:
            raise FileNotFoundError(key) from None
    return local.read_bytes()


def delete(key: str) -> bool:
    """Best effort: True when the object is gone (or was never there), False when it could not be removed (logged, never raised)."""
    try:
        if is_legacy(key):
            tg_store.delete(key)
            return True
        _check(key)
        name = backend_name()
        if name == 's3':
            client().delete_object(Bucket=settings.tg_media_bucket, Key=key)
            _local_path(key).unlink(missing_ok=True)          # a leftover of the folder backend (before the switch to the bucket)
        elif name == 'memory':
            MEMORY.pop(key, None)
        else:
            _local_path(key).unlink(missing_ok=True)
        return True
    except Exception as error:  # noqa: BLE001 — never blocks the admin
        log.warning('could not delete %s: %s', key, type(error).__name__)
        return False


def delete_many(keys) -> int:
    """Deletes each key (best effort); returns how many could not be removed."""
    return sum(0 if delete(key) else 1 for key in keys)


def local_post_dirs(post_id: int) -> None:
    """The folder of a deleted post in the folder backends (empty by now; old private files of the post too)."""
    for prefix in (f'{settings.tg_media_prefix}/{int(post_id)}', f'posts/{int(post_id)}'):
        with contextlib.suppress(OSError):
            (Path(tg_store.local_root()) / prefix).rmdir()
