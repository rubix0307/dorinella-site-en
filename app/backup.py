"""The daily backup of the database (docker-compose: backup; one copy only).

Once a day (at BACKUP_HOUR, UTC) it runs `pg_dump` (custom format, already compressed), seals the file with BACKUP_KEY
(AES-GCM in 4 MiB chunks, so memory stays small) and puts it to the private bucket (BACKUP_S3_*) as
db/<year>/<month>/lolakrissss-<time>.dump.enc. The key of the bucket can only write: nothing here lists, reads or deletes,
and nothing is deleted anywhere — old copies stay until someone removes them (or a lifecycle rule of the bucket does).
Every run leaves a row in `backup_runs` (shown nowhere); a run that failed is retried in half an hour, and a start
without a successful backup in the last day backs up at once.

    python -m app.backup            the worker
    python -m app.backup --once     one backup now (to check the setup)
Restore: scripts/backup_restore.py.
"""
import asyncio
import base64
import logging
import os
import struct
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import overload

import boto3
from botocore.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.engine import make_url

from . import db
from .core.config import settings
from .core.monitoring import metric
from .models import BackupRun

log = logging.getLogger('backup')

MAGIC = b'DORIBK1\n'
CHUNK = 4 * 1024 * 1024
RETRY_AFTER = timedelta(minutes=30)
DUMP_TIMEOUT = 30 * 60


def configured() -> bool:
    return bool(settings.backup_s3_bucket and settings.backup_s3_key_id and settings.backup_s3_secret and settings.backup_key)


def read_key(text: str) -> bytes:
    try:
        key = base64.b64decode(text, validate=True)
    except ValueError:
        key = b''
    if len(key) != 32:
        raise ValueError('BACKUP_KEY must be 32 bytes in base64')
    return key


# ---------------------------------------------------------------- the sealed file

def seal(source: Path, target: Path, key: bytes) -> None:
    """MAGIC, a random 8-byte prefix, then chunks (4-byte length + AES-GCM text). The nonce is prefix + chunk number; the
    prefix, and whether the chunk is the last one, are authenticated: a cut-short or re-ordered file does not open."""
    prefix, aes = os.urandom(8), AESGCM(key)
    with source.open('rb') as fin, target.open('wb') as fout:
        fout.write(MAGIC + prefix)
        block, index = fin.read(CHUNK), 0
        while True:
            following = fin.read(CHUNK)
            last = not following
            sealed = aes.encrypt(prefix + struct.pack('>I', index), block, MAGIC + prefix + (b'\x01' if last else b'\x00'))
            fout.write(struct.pack('>I', len(sealed)) + sealed)
            if last:
                return
            block, index = following, index + 1


def open_sealed(source: Path, target: Path, key: bytes) -> None:
    aes = AESGCM(key)
    with source.open('rb') as fin, target.open('wb') as fout:
        head = fin.read(len(MAGIC) + 8)
        if len(head) != len(MAGIC) + 8 or head[:len(MAGIC)] != MAGIC:
            raise ValueError('not a sealed backup')
        index = 0
        while True:
            size = fin.read(4)
            if len(size) != 4:
                raise ValueError('the file is cut short')
            sealed = fin.read(struct.unpack('>I', size)[0])
            last = not fin.read(1)
            if not last:
                fin.seek(-1, os.SEEK_CUR)
            fout.write(aes.decrypt(head[len(MAGIC):] + struct.pack('>I', index), sealed, head + (b'\x01' if last else b'\x00')))
            if last:
                return
            index += 1


# ---------------------------------------------------------------- one backup

def dump_command(url: str, target: Path) -> tuple[list[str], dict[str, str]]:
    link = make_url(url)
    command = ['pg_dump', '--format=custom', '--no-owner', '--host', link.host or 'localhost', '--port', str(link.port or 5432),
               '--username', link.username or '', '--dbname', link.database or '', '--file', str(target)]
    return command, {**os.environ, 'PGPASSWORD': link.password or ''}


def dump(command: list[str], env: dict[str, str] | None = None) -> None:
    done = subprocess.run(command, env=env, capture_output=True, timeout=DUMP_TIMEOUT, check=False)  # noqa: S603 — fixed argv from dump_command, no shell
    if done.returncode:
        raise RuntimeError(f'pg_dump failed: {done.stderr.decode(errors="replace").strip()[-300:]}')


def client():
    return boto3.client('s3', endpoint_url=settings.backup_s3_endpoint or None, region_name=settings.backup_s3_region or None,
                        aws_access_key_id=settings.backup_s3_key_id, aws_secret_access_key=settings.backup_s3_secret,
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 4}))


def object_key(now: datetime) -> str:
    return f'db/{now:%Y/%m}/lolakrissss-{now:%Y%m%d-%H%M%S}.dump.enc'


def make_backup(now: datetime) -> tuple[str, int]:
    """Dump, seal, upload (blocking). Returns the object key and the size of what was stored."""
    key = read_key(settings.backup_key)
    with tempfile.TemporaryDirectory(prefix='dori-backup-') as folder:
        plain, sealed = Path(folder, 'db.dump'), Path(folder, 'db.dump.enc')
        dump(*dump_command(os.environ['DATABASE_URL'].replace('+asyncpg', ''), plain))
        seal(plain, sealed, key)
        plain.unlink()                                   # the readable copy goes first
        name = object_key(now)
        client().upload_file(str(sealed), settings.backup_s3_bucket, name, ExtraArgs={'ContentType': 'application/octet-stream'})
        return name, sealed.stat().st_size


async def run_once(factory) -> bool:
    started = datetime.now(UTC)
    async with factory() as session:
        run = BackupRun(started_at=started)
        session.add(run)
        await session.commit()
        try:
            name, size = await asyncio.to_thread(make_backup, started)
            run.ok, run.object_key, run.bytes = True, name, size
            log.info('backup stored: %s (%d bytes)', name, size)
            metric('backup', result='ok')
        except Exception as problem:
            run.ok, run.error = False, str(problem)[:300]
            log.exception('backup failed')
            metric('backup', result='failed')
        run.finished_at = datetime.now(UTC)
        await session.commit()
        return bool(run.ok)


# ---------------------------------------------------------------- the schedule

def next_slot(after: datetime | None, hour: int | None = None) -> datetime:
    """The first BACKUP_HOUR:00 UTC after `after`; a start with no success yet (None) is due at once."""
    if after is None:
        return datetime.min.replace(tzinfo=UTC)
    slot = after.replace(hour=settings.backup_hour if hour is None else hour, minute=0, second=0, microsecond=0)
    return slot if slot > after else slot + timedelta(days=1)


@overload
def _utc(moment: datetime) -> datetime: ...
@overload
def _utc(moment: None) -> None: ...
def _utc(moment: datetime | None) -> datetime | None:
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=UTC)


async def seconds_to_wait(factory, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    async with factory() as session:
        last_ok = _utc(await session.scalar(select(BackupRun.finished_at).where(BackupRun.ok.is_(True)).order_by(BackupRun.id.desc()).limit(1)))
        last_try = (await session.execute(select(BackupRun.ok, BackupRun.finished_at).order_by(BackupRun.id.desc()).limit(1))).first()
    due = next_slot(last_ok)
    if last_try and last_try.ok is False and last_try.finished_at:
        due = max(due, _utc(last_try.finished_at) + RETRY_AFTER)
    return max(0.0, (due - now).total_seconds())


async def loop(factory) -> None:
    while True:
        wait = await seconds_to_wait(factory)
        if wait:
            await asyncio.sleep(min(wait, 3600))          # re-read the schedule at least hourly
            continue
        await run_once(factory)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    if db.SessionLocal is None or not configured():
        log.error('the backup is not set up: DATABASE_URL, BACKUP_S3_* and BACKUP_KEY are needed')
        sys.exit(1)
    if '--once' in sys.argv:
        sys.exit(0 if asyncio.run(run_once(db.SessionLocal)) else 1)
    asyncio.run(loop(db.SessionLocal))


if __name__ == '__main__':
    main()
