"""Roles, permissions and groups of the admin.

Three roles: `root` (every permission, the only one who manages admins and groups), `admin` (the permissions of the
groups attached to it) and `user` (an account without access: no groups, no permissions). Roots are never made through
the pages: the migration and `python -m app.cli make-root` do it.

A permission is a code from PERMISSIONS. Groups are lists of codes; the system group "Full access" holds no rows and
means "every assignable permission", so a new permission is covered the day it is added.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminUser, AdminUserGroup, GroupPermission, PermissionGroup

ROOT, ADMIN, USER = 'root', 'admin', 'user'
ROLE_LABELS = {ROOT: 'Root', ADMIN: 'Admin', USER: 'User'}

SYSTEM_GROUP = 'Full access'

# code -> (section, label): English source strings, translated by app/i18n.py when the admin shows them
PERMISSIONS: dict[str, tuple[str, str]] = {
    'analytics.view': ('Analytics', 'View analytics and pages'),
    'links.manage': ('Analytics', 'Manage short links'),
    'tests.view': ('Analytics', 'View test answers'),
    'course.view': ('Course', 'View course statistics'),
    'links_page.manage': ('Links page', 'Edit the links page'),
    'orders.view': ('Orders', 'View orders and payment statistics'),
    'orders.refund': ('Orders', 'Refund orders'),
    'users.view': ('Users', 'View users'),
    'users.manage': ('Users', 'Create, change, disable and delete users'),
    'users.promote': ('Users', 'Make a user an admin (within your own rights)'),
}
ASSIGNABLE = frozenset(PERMISSIONS)
ROOT_ONLY = frozenset({'groups.manage', 'admins.manage'})          # only the root role has them; never in a group
ALL = ASSIGNABLE | ROOT_ONLY

# having the key without its implied permission would leave a link that leads to a 403
IMPLIES = {
    'links.manage': 'analytics.view',
    'orders.refund': 'orders.view',
    'users.manage': 'users.view',
    'users.promote': 'users.view',
}


def closure(codes) -> frozenset[str]:
    """The codes plus what they imply; unknown codes are dropped."""
    result = {code for code in codes if code in ASSIGNABLE}
    while True:                                          # what is implied may imply more (a permission may imply one that implies another)
        more = {IMPLIES[code] for code in result if code in IMPLIES} - result
        if not more:
            return frozenset(result)
        result |= more


def group_codes(group: PermissionGroup, stored: set[str] | frozenset[str]) -> frozenset[str]:
    return ASSIGNABLE if group.is_system else closure(stored)


async def load(session: AsyncSession, user: AdminUser) -> frozenset[str]:
    """The permissions of an account, read from the database (one query for an admin, none for root and user)."""
    if user.role == ROOT:
        return ALL
    if user.role != ADMIN:
        return frozenset()
    rows = (await session.execute(
        select(PermissionGroup.is_system, GroupPermission.code)
        .select_from(AdminUserGroup)
        .join(PermissionGroup, PermissionGroup.id == AdminUserGroup.group_id)
        .outerjoin(GroupPermission, GroupPermission.group_id == PermissionGroup.id)
        .where(AdminUserGroup.user_id == user.id))).all()
    if any(is_system for is_system, _ in rows):
        return ASSIGNABLE
    return closure(code for _, code in rows if code)


async def group_permissions(session: AsyncSession, groups: list[PermissionGroup]) -> dict[int, frozenset[str]]:
    """The effective codes of each group."""
    ids = [group.id for group in groups]
    stored: dict[int, set[str]] = {i: set() for i in ids}
    if ids:
        for group_id, code in (await session.execute(
                select(GroupPermission.group_id, GroupPermission.code).where(GroupPermission.group_id.in_(ids)))).all():
            stored[group_id].add(code)
    return {group.id: group_codes(group, stored[group.id]) for group in groups}


def first_section(perms) -> str | None:
    """Route name of the first admin page a set of permissions can open (the dashboard sends people there)."""
    for code, route in (('analytics.view', 'admin_dashboard'), ('tests.view', 'admin_tests'), ('course.view', 'admin_course'), ('links_page.manage', 'admin_links_page'),
                        ('orders.view', 'admin_orders'), ('users.view', 'admin_users'), ('groups.manage', 'admin_groups')):
        if code in perms:
            return route
    return None
