"""Management commands.

    python -m app.cli create-admin you@gmail.com        # an admin; they sign in with that Google account
    python -m app.cli make-root you@example.com         # the only way to make a root (the pages never do)
    python -m app.cli activate you@example.com          # switch an account back on (recovery of the only root)
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
        print('DATABASE_URL is not set.', file=sys.stderr)
        return 1
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        print(f'Not a valid e-mail address: {email}', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        user = await session.scalar(select(AdminUser).where(AdminUser.email == email))
        if user is not None:
            print(f'The admin {email} already exists.', file=sys.stderr)
            return 1
        user = AdminUser(email=email, password_hash=NO_PASSWORD, role='admin', session_version=0)
        session.add(user)
        await session.flush()
        full = await session.scalar(select(PermissionGroup.id).where(PermissionGroup.name == SYSTEM_GROUP))
        if full is not None:                     # an admin without a group could open nothing
            session.add(AdminUserGroup(user_id=user.id, group_id=full))
        await session.commit()
    await db.get_engine().dispose()
    print(f'Done: {email}')
    return 0


async def _change(email: str, root: bool) -> int:
    if db.SessionLocal is None:
        print('DATABASE_URL is not set.', file=sys.stderr)
        return 1
    async with db.new_session() as session:
        user = await session.scalar(select(AdminUser).where(AdminUser.email == normalize_email(email)))
        if user is None:
            print(f'No user {email}.', file=sys.stderr)
            return 1
        held = [g for (g,) in (await session.execute(select(PermissionGroup.name).join(AdminUserGroup, AdminUserGroup.group_id == PermissionGroup.id)
                                                      .where(AdminUserGroup.user_id == user.id))).all()]
        print(f'Was: role {user.role}, active {bool(user.is_active)}, groups: {", ".join(held) or "-"}')
        if root:
            user.role = ROOT
            await session.execute(delete(AdminUserGroup).where(AdminUserGroup.user_id == user.id))
        user.is_active = True
        user.session_version = (user.session_version or 0) + 1
        await session.commit()
    await db.get_engine().dispose()
    print(f'Done: {email}' + (' is now root' if root else ' is active'))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog='python -m app.cli')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('create-admin', help='create an admin (they sign in with Google)').add_argument('email')
    for name, help_text in (('make-root', 'make a user root'), ('activate', 'switch an account on')):
        commands.add_parser(name, help=help_text).add_argument('email')
    args = parser.parse_args()
    if args.command in ('make-root', 'activate'):
        return asyncio.run(_change(args.email, root=args.command == 'make-root'))
    return asyncio.run(_create(args.email))


if __name__ == '__main__':
    sys.exit(main())
