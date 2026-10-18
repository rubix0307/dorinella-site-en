"""Admin accounts: the rules for making, changing and removing them, in one place (the admin pages use them).

Who may touch whom (actor A, target T). The pages hide what A may not do, but the rules are enforced here, again,
under row locks, with A's role and permissions read inside the same transaction:
  - nobody edits themselves through these pages;
  - a root is untouchable (roots are made by the migration / `app.cli make-root`);
  - an admin is edited, switched off, demoted or deleted by a root only;
  - a plain user is edited, switched off and deleted by whoever has `users.manage`;
  - a user becomes an admin through `users.promote`, with groups whose permissions are all A's own (a root: any group).
Also: the site always keeps an active admin or root, and the last one cannot be switched off, demoted or deleted.
"""
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import permissions
from .auth import EMAIL_RE, NO_PASSWORD, normalize_email
from . import models
from .models import AdminUser, AdminUserGroup, PermissionGroup, ShortLink
from .permissions import ADMIN, ROOT, USER

log = logging.getLogger(__name__)

EMAIL_MAX = 254


class UserError(ValueError):
    """A refusal with a message for the admin; `field` names the input it belongs to ('' = the whole form)."""

    def __init__(self, message: str, field: str = '', code: str = ''):
        super().__init__(message)
        self.field, self.code = field, code


FORBIDDEN = 'Недостатньо прав для цієї дії.'


def check_email(raw: str) -> str:
    email = normalize_email(raw)
    if not email or len(email) > EMAIL_MAX or not EMAIL_RE.match(email) or any(ord(c) < 32 for c in email):
        raise UserError('Вкажіть коректну пошту.', 'email')
    return email


# ---------------------------------------------------------------- who may do what (pure; the pages use them too)

def may_edit(actor: AdminUser, target: AdminUser | dict) -> bool:
    """Email, switching on/off, deleting."""
    target_id, role = (target['id'], target['role']) if isinstance(target, dict) else (target.id, target.role)
    if actor.id == target_id or role == ROOT:
        return False
    if role == ADMIN:
        return actor.role == ROOT
    return actor.can('users.manage')


def may_set_access(actor: AdminUser, target: AdminUser | dict) -> bool:
    """Role and groups: promoting a user, and (root only) changing or removing an admin's access."""
    target_id, role = (target['id'], target['role']) if isinstance(target, dict) else (target.id, target.role)
    if actor.id == target_id or role == ROOT:
        return False
    if role == ADMIN:
        return actor.role == ROOT
    return actor.can('users.promote')


def may_grant(actor: AdminUser, group_perms: frozenset[str], system: bool = False) -> bool:
    """Only your own permissions or fewer (a root may grant any group). The system group also covers the permissions
    added later, so it is handed out only by a root."""
    return actor.role == ROOT or (not system and group_perms <= actor.perms)


async def grantable_groups(session: AsyncSession, actor: AdminUser) -> list[tuple[PermissionGroup, frozenset[str], bool]]:
    groups = list((await session.scalars(select(PermissionGroup).order_by(PermissionGroup.is_system.desc(), PermissionGroup.name))).all())
    effective = await permissions.group_permissions(session, groups)
    return [(group, effective[group.id], may_grant(actor, effective[group.id], group.is_system)) for group in groups]


# ---------------------------------------------------------------- locking

def bump(user: AdminUser) -> None:
    """Ends every session of the account (the version is part of the cookie signature)."""
    user.session_version = (user.session_version or 0) + 1


async def _lock(session: AsyncSession, actor: AdminUser, target: AdminUser | None = None) -> None:
    """Row locks in ascending id order (so two admins acting on each other cannot deadlock), then the actor's
    role, state and permissions are read again: a stale check must not authorise a write."""
    ids = sorted({actor.id, target.id} if target is not None else {actor.id})
    await session.scalars(select(AdminUser).where(AdminUser.id.in_(ids)).order_by(AdminUser.id).with_for_update()
                          .execution_options(populate_existing=True))
    if not actor.is_active:
        raise UserError(FORBIDDEN, code='forbidden')
    actor.perms = await permissions.load(session, actor)


async def _guard_last(session: AsyncSession, target: AdminUser, verb: str) -> None:
    """The target stops being an active admin (switched off, demoted or deleted): the site keeps at least one other."""
    if target.is_active and target.role != USER:
        others = await session.scalar(select(func.count()).select_from(AdminUser).where(
            AdminUser.is_active.is_(True), AdminUser.role != USER, AdminUser.id != target.id))
        if not others:
            raise UserError(f'Це останній активний адміністратор: його не можна {verb}.', code='last')


def _refuse_self_or_root(actor: AdminUser, target: AdminUser) -> None:
    if actor.id == target.id:
        raise UserError('Свій обліковий запис змінюють у профілі.', code='self')
    if target.role == ROOT:
        raise UserError('Root змінити не можна.', code='root')


# ---------------------------------------------------------------- groups of a promoted account

async def _groups_for(session: AsyncSession, actor: AdminUser, ids: list[int]) -> list[PermissionGroup]:
    wanted = set(ids)
    groups = list((await session.scalars(select(PermissionGroup).where(PermissionGroup.id.in_(wanted)))).all()) if wanted else []
    if len(groups) != len(wanted):
        raise UserError('Групу не знайдено.', 'groups', code='nogroup')
    effective = await permissions.group_permissions(session, groups)
    for group in groups:
        if not may_grant(actor, effective[group.id], group.is_system):
            raise UserError(f'Група «{group.name}» дає права, яких немає у вас.', 'groups', code='grant')
    return groups


async def _set_groups(session: AsyncSession, target: AdminUser, groups: list[PermissionGroup]) -> None:
    await session.execute(delete(AdminUserGroup).where(AdminUserGroup.user_id == target.id))
    for group in groups:
        session.add(AdminUserGroup(user_id=target.id, group_id=group.id))


async def target_groups(session: AsyncSession, user_id: int) -> list[int]:
    return list((await session.scalars(select(AdminUserGroup.group_id).where(AdminUserGroup.user_id == user_id))).all())


# ---------------------------------------------------------------- actions

async def create(session: AsyncSession, actor: AdminUser, email: str, active: bool,
                 role: str = USER, group_ids: list[int] | None = None) -> AdminUser:
    if role not in (USER, ADMIN):
        raise UserError(FORBIDDEN, code='forbidden')
    email = check_email(email)
    await _lock(session, actor)
    if not actor.can('users.manage') or (role == ADMIN and not actor.can('users.promote')):
        raise UserError(FORBIDDEN, code='forbidden')
    groups = await _groups_for(session, actor, group_ids or []) if role == ADMIN else []
    if await session.scalar(select(AdminUser.id).where(AdminUser.email == email)):
        raise UserError('Користувач з такою поштою вже є.', 'email')
    user = AdminUser(email=email, password_hash=NO_PASSWORD, is_active=active, role=role, session_version=0, onboarding_state='pending')
    session.add(user)
    try:
        await session.flush()
        await _set_groups(session, user, groups)
        await session.commit()
    except IntegrityError:          # two forms with the same address at the same moment
        await session.rollback()
        raise UserError('Користувач з такою поштою вже є.', 'email') from None
    log.info('admin user created: %s (id %s, %s) by %s', user.email, user.id, role, actor.email)
    return user


async def update_user(session: AsyncSession, actor: AdminUser, user: AdminUser, email: str, active: bool) -> None:
    """Saves the form."""
    email = check_email(email)
    await _lock(session, actor, user)
    _refuse_self_or_root(actor, user)
    if not may_edit(actor, user):
        raise UserError(FORBIDDEN, code='forbidden')
    if email != user.email and await session.scalar(select(AdminUser.id).where(AdminUser.email == email, AdminUser.id != user.id)):
        raise UserError('Користувач з такою поштою вже є.', 'email')
    if user.is_active and not active:
        await _guard_last(session, user, 'вимкнути')
        bump(user)
    user.email, user.is_active = email, active
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise UserError('Користувач з такою поштою вже є.', 'email') from None
    log.info('admin user changed: %s (id %s) by %s', user.email, user.id, actor.email)


async def set_access(session: AsyncSession, actor: AdminUser, user: AdminUser, role: str, group_ids: list[int]) -> None:
    """Promote a user to admin with groups, or (root) change an admin's groups or demote them."""
    if role not in (USER, ADMIN):
        raise UserError(FORBIDDEN, code='forbidden')
    await _lock(session, actor, user)
    _refuse_self_or_root(actor, user)
    if not may_set_access(actor, user):
        raise UserError(FORBIDDEN, code='forbidden')
    groups = await _groups_for(session, actor, group_ids) if role == ADMIN else []
    if user.role == ADMIN and role == USER:
        await _guard_last(session, user, 'понизити')
    before = (user.role, sorted(await target_groups(session, user.id)))
    user.role = role
    await _set_groups(session, user, groups)
    if before != (role, sorted(group.id for group in groups)):
        bump(user)
    await session.commit()
    log.info('admin access changed: %s (id %s) -> %s %s by %s', user.email, user.id, role, [g.name for g in groups], actor.email)


async def set_active(session: AsyncSession, actor: AdminUser, user: AdminUser, active: bool) -> None:
    await _lock(session, actor, user)
    _refuse_self_or_root(actor, user)
    if not may_edit(actor, user):
        raise UserError(FORBIDDEN, code='forbidden')
    if user.is_active and not active:
        await _guard_last(session, user, 'вимкнути')
    if user.is_active != active:
        bump(user)
    user.is_active = active
    await session.commit()
    log.info('admin user %s: %s (id %s) by %s', 'enabled' if active else 'disabled', user.email, user.id, actor.email)


async def remove(session: AsyncSession, actor: AdminUser, user: AdminUser) -> None:
    await _lock(session, actor, user)
    _refuse_self_or_root(actor, user)
    if not may_edit(actor, user):
        raise UserError(FORBIDDEN, code='forbidden')
    await _guard_last(session, user, 'delete')
    order = getattr(models, 'Order', None)         # the table of the orders exists once payments are built
    if order is not None and await session.scalar(select(func.count()).select_from(order).where(order.user_id == user.id)):
        raise UserError('This account has orders: they are kept for the books, so the account cannot be deleted. Switch it off instead.', code='orders')
    # the database does this too (ON DELETE SET NULL / CASCADE); said out loud it holds on every database
    await session.execute(update(ShortLink).where(ShortLink.created_by == user.id).values(created_by=None))
    await session.execute(delete(AdminUserGroup).where(AdminUserGroup.user_id == user.id))
    email, user_id = user.email, user.id
    await session.delete(user)
    await session.commit()
    log.info('admin user deleted: %s (id %s) by %s', email, user_id, actor.email)
