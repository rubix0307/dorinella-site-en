"""Management commands.

    python -m app.cli create-admin you@gmail.com        # an admin; they sign in with that Google account
    python -m app.cli make-root you@example.com         # the only way to make a root (the pages never do)
    python -m app.cli activate you@example.com          # switch an account back on (recovery of the only root)
    python -m app.cli import-articles <dir> [--dry-run]  # markdown files -> draft articles (app/article_import.py)
    python -m app.cli set-promo-id <key> [--dry-run]     # write the id of an advert block into the adverts of the articles that name none (app/promo.py)
    python -m app.cli embed-recipes                     # vectors for new / edited recipes (deploy.sh)
"""
import argparse
import asyncio
import sys

from sqlalchemy import delete, select

from . import db
from .auth import EMAIL_RE, NO_PASSWORD, normalize_email
from .models import AdminUser, AdminUserGroup, PermissionGroup
from .permissions import ROOT, SYSTEM_GROUP


async def _create(email: str) -> int:
    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        print(f'Некоректна пошта: {email}', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        user = await session.scalar(select(AdminUser).where(AdminUser.email == email))
        if user is not None:
            print(f'Адміністратор {email} вже існує.', file=sys.stderr)
            return 1
        user = AdminUser(email=email, password_hash=NO_PASSWORD, role='admin', session_version=0, onboarding_state='pending')
        session.add(user)
        await session.flush()
        full = await session.scalar(select(PermissionGroup.id).where(PermissionGroup.name == SYSTEM_GROUP))
        if full is not None:                     # an admin without a group could open nothing
            session.add(AdminUserGroup(user_id=user.id, group_id=full))
        await session.commit()
    await db.get_engine().dispose()
    print(f'Готово: {email}')
    return 0


async def _change(email: str, root: bool) -> int:
    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        user = await session.scalar(select(AdminUser).where(AdminUser.email == normalize_email(email)))
        if user is None:
            print(f'Користувача {email} не знайдено.', file=sys.stderr)
            return 1
        held = [g for (g,) in (await session.execute(select(PermissionGroup.name).join(AdminUserGroup, AdminUserGroup.group_id == PermissionGroup.id)
                                                      .where(AdminUserGroup.user_id == user.id))).all()]
        print(f'Було: роль {user.role}, активний {bool(user.is_active)}, групи: {", ".join(held) or "—"}')
        if root:
            user.role = ROOT
            await session.execute(delete(AdminUserGroup).where(AdminUserGroup.user_id == user.id))
        user.is_active = True
        user.session_version = (user.session_version or 0) + 1
        await session.commit()
    await db.get_engine().dispose()
    print(f'Готово: {email}' + (' тепер root' if root else ' активний'))
    return 0


async def _embed_recipes() -> int:
    from . import recommendations  # the model itself only ever loads in a child process
    from .recipes import store as recipe_store

    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        await recipe_store.load(session)          # the published recipes, from the database
        result = await recommendations.sync(session)
    await db.get_engine().dispose()
    print('Рецепти: нових або змінених {embedded}, видалених {removed}, без змін {unchanged}'.format(**result))
    return 0


async def _embed_articles() -> int:
    from . import articles

    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        result = await articles.sync_embeddings(session)
    await db.get_engine().dispose()
    print('Статті: ' + ', '.join(f'{k} {v}' for k, v in result.items()))
    return 0


async def _seed_articles() -> int:
    from . import article_seed

    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        result = await article_seed.seed(session)
    await db.get_engine().dispose()
    if result.get('no_owner'):
        print('Немає жодного root-акаунта: спершу python -m app.cli make-root <пошта>.', file=sys.stderr)
        return 1
    print('Статті-чернетки: створено {created}, вже були {skipped}'.format(**result))
    return 0


async def _import_articles(directory: str, dry_run: bool) -> int:
    from pathlib import Path

    from . import article_import

    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        report = await article_import.run(session, Path(directory), dry_run=dry_run)
    await db.get_engine().dispose()
    if report.no_owner:
        print(article_import.NO_OWNER, file=sys.stderr)
        return 1
    for item in report.items:
        print(f'{item.file}: {item.title} -> {item.slug} | слів {item.words} | посилань переписано {item.links} | {item.status}{" (" + item.message + ")" if item.message else ""}')
    for warning in report.warnings:
        print(f'УВАГА: {warning}', file=sys.stderr)
    for error in report.errors:
        print(f'ПОМИЛКА: {error}', file=sys.stderr)
    print(f'{"Пробний запуск, нічого не записано. " if dry_run else ""}Створено {report.count("created")}, вже були {report.count("already")}, помилок {report.count("error")}')
    return 1 if report.errors else 0


async def _set_promo_id(key: str, dry_run: bool) -> int:
    from . import promo

    if db.SessionLocal is None:
        print('DATABASE_URL не задано.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        done, problem = await promo.pin_ids(session, key, dry_run=dry_run)
    await db.get_engine().dispose()
    if problem:
        print(problem, file=sys.stderr)
        return 1
    for slug, count in done:
        print(f'{slug}: реклам {count}')
    print(f'{"Пробний запуск, нічого не записано. " if dry_run else ""}Статей змінено: {len(done)}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog='python -m app.cli')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('create-admin', help='створити адміністратора (входить через Google)').add_argument('email')
    for name, help_text in (('make-root', 'зробити користувача root'), ('activate', 'увімкнути обліковий запис')):
        commands.add_parser(name, help=help_text).add_argument('email')
    commands.add_parser('embed-recipes', help='оновити вектори рецептів (рекомендації)')
    commands.add_parser('seed-articles', help='додати дві перші статті чернетками (нічого не публікує)')
    imp = commands.add_parser('import-articles', help='статті з markdown-файлів теки чернетками (нічого не публікує)')
    imp.add_argument('directory')
    imp.add_argument('--dry-run', action='store_true')
    pin = commands.add_parser('set-promo-id', help='вписати id рекламного блоку в реклами статей, де його немає')
    pin.add_argument('key')
    pin.add_argument('--dry-run', action='store_true')
    commands.add_parser('embed-articles', help='оновити вектори статей (схожі статті, стрічка)')
    args = parser.parse_args()
    if args.command == 'seed-articles':
        return asyncio.run(_seed_articles())
    if args.command == 'import-articles':
        return asyncio.run(_import_articles(args.directory, args.dry_run))
    if args.command == 'set-promo-id':
        return asyncio.run(_set_promo_id(args.key, args.dry_run))
    if args.command == 'embed-articles':
        return asyncio.run(_embed_articles())
    if args.command == 'embed-recipes':
        return asyncio.run(_embed_recipes())
    if args.command in ('make-root', 'activate'):
        return asyncio.run(_change(args.email, root=args.command == 'make-root'))
    return asyncio.run(_create(args.email))


if __name__ == '__main__':
    sys.exit(main())
