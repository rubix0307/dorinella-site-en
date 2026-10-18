"""Writes app/lastmod.json: the day of the last commit of every file under app/ (the Docker image has no git, so the sitemap reads this). Standard library only."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app'


def main() -> None:
    out = subprocess.run(['git', '-C', str(ROOT), 'log', '--format=@%cs', '--name-only', '--', 'app'], capture_output=True, text=True, check=True).stdout
    days: dict[str, str] = {}
    day = ''
    for line in out.splitlines():
        if line.startswith('@'):
            day = line[1:]
        elif line.strip().startswith('app/'):
            days.setdefault(line.strip().removeprefix('app/'), day)      # newest commit comes first
    (APP / 'lastmod.json').write_text(json.dumps(days, sort_keys=True), encoding='utf-8')


if __name__ == '__main__':
    main()
