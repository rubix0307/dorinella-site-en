"""Permission groups: made, changed and removed by a root only (so a group can never be widened by someone who
holds less than it gives). Admins are only handed existing groups, within their own permissions: see app/users.py."""
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import permissions
from .models import AdminUser, AdminUserGroup, GroupPermission, PermissionGroup
from .permissions import ROOT
from .users import FORBIDDEN, UserError

log = logging.getLogger(__name__)


async def _root(session: AsyncSession, actor: AdminUser) -> None:
    """Role read again under a lock: a root demoted a moment ago must not finish the write."""
    await session.scalars(select(AdminUser).where(AdminUser.id == actor.id).with_for_update().execution_options(populate_existing=True))
    if actor.role != ROOT or not actor.is_active:
        raise UserError(FORBIDDEN, code='forbidden')


def _clean(name: str, description: str, codes: list[str]) -> tuple[str, str, frozenset[str]]:
    name, description = ' '.join(name.split()), description.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise UserError('Вкажіть назву групи (до 80 символів).', 'name')
    if len(description) > 200 or any(ord(c) < 32 and c not in '\n\t' for c in description):
        raise UserError('Опис задовгий (до 200 символів).', 'description')
    unknown = set(codes) - permissions.ASSIGNABLE
    if unknown:
        raise UserError('Невідоме право у запиті.', 'codes')
    return name, description, permissions.closure(codes)


async def _taken(session: AsyncSession, name: str, group_id: int | None) -> bool:
    stmt = select(PermissionGroup.id).where(PermissionGroup.name == name)
    if group_id is not None:
        stmt = stmt.where(PermissionGroup.id != group_id)
    return await session.scalar(stmt) is not None


async def save(session: AsyncSession, actor: AdminUser, group: PermissionGroup | None, name: str, description: str,
               codes: list[str]) -> PermissionGroup:
    name, description, effective = _clean(name, description, codes)
    await _root(session, actor)
    if group is not None and group.is_system:
        raise UserError('Системну групу змінити не можна.', code='system')
    if await _taken(session, name, group.id if group else None):
        raise UserError('Група з такою назвою вже є.', 'name')
    if group is None:
        group = PermissionGroup(name=name, description=description, is_system=False)
        session.add(group)
        await session.flush()
    else:
        group.name, group.description = name, description
        await session.execute(delete(GroupPermission).where(GroupPermission.group_id == group.id))
    for code in sorted(effective):
        session.add(GroupPermission(group_id=group.id, code=code))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise UserError('Група з такою назвою вже є.', 'name') from None
    log.info('permission group saved: %s %s by %s', group.name, sorted(effective), actor.email)
    return group


async def remove(session: AsyncSession, actor: AdminUser, group: PermissionGroup) -> None:
    await _root(session, actor)
    if group.is_system:
        raise UserError('Системну групу видалити не можна.', code='system')
    await session.execute(delete(AdminUserGroup).where(AdminUserGroup.group_id == group.id))
    await session.execute(delete(GroupPermission).where(GroupPermission.group_id == group.id))
    name = group.name
    await session.delete(group)
    await session.commit()
    log.info('permission group deleted: %s by %s', name, actor.email)
