"""Open a sealed database backup (app/backup.py) and, if asked, load it into a database.

    BACKUP_KEY=<base64> python scripts/backup_restore.py dorinella-20261020-030000.dump.enc dorinella.dump
    BACKUP_KEY=<base64> python scripts/backup_restore.py dorinella-....dump.enc dorinella.dump --restore postgresql://user:pass@host/newdb

Download the file from the bucket first (with your own key: the server's key can only write). The first form only
decrypts to a pg_dump file (custom format); --restore then runs `pg_restore --clean --if-exists` into the named database,
which should be an empty one made for the purpose (pg_restore needs PostgreSQL 18 client tools).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backup import open_sealed, read_key


def main(args: list[str]) -> int:
    if len(args) < 2:
        print(__doc__)
        return 2
    source, target = Path(args[0]), Path(args[1])
    open_sealed(source, target, read_key(os.environ.get('BACKUP_KEY', '')))
    print(f'opened: {target} ({target.stat().st_size} bytes)')
    if '--restore' in args:
        url = args[args.index('--restore') + 1]
        return subprocess.run(['pg_restore', '--clean', '--if-exists', '--no-owner', '--dbname', url, str(target)], check=False).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
