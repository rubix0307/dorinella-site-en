"""Admin: permission groups (root only)."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, groups, permissions
from ..auth import authorize
from ..models import AdminUserGroup, GroupPermission, PermissionGroup
from ..users import UserError
from .admin import NO_INDEX, _page, _to_login

router = APIRouter(prefix='/admin/groups', default_response_class=HTMLResponse, include_in_schema=False)

PERM = 'groups.manage'
NOTICES = {'created': 'Групу створено.', 'saved': 'Групу збережено.', 'deleted': 'Групу видалено.'}
ERRORS = {'missing': 'Групу не знайдено.', 'system': 'Системну групу не можна змінити чи видалити.', 'forbidden': 'Недостатньо прав для цієї дії.'}


def _back(request: Request, notice: str = '', error: str = '') -> RedirectResponse:
    query = f'?notice={notice}' if notice else (f'?error={error}' if error else '')
    return RedirectResponse(request.app.url_path_for('admin_groups') + query, status_code=303, headers=NO_INDEX)


def _sections() -> list[tuple[str, list[tuple[str, str]]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for code, (section, label) in permissions.PERMISSIONS.items():
        result.setdefault(section, []).append((code, label))
    return list(result.items())


@router.get('/', name='admin_groups')
async def groups_list(request: Request, notice: str = '', error: str = '', session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    rows = list((await session.scalars(select(PermissionGroup).order_by(PermissionGroup.is_system.desc(), PermissionGroup.name))).all())
    effective = await permissions.group_permissions(session, rows)
    members = dict((await session.execute(select(AdminUserGroup.group_id, func.count()).group_by(AdminUserGroup.group_id))).all())
    return _page(request, 'admin/groups.html', user=user, groups=[
        {'group': g, 'perms': effective[g.id], 'members': members.get(g.id, 0)} for g in rows],
        labels={code: label for code, (_, label) in permissions.PERMISSIONS.items()},
        notice=NOTICES.get(notice, ''), error=ERRORS.get(error, ''))


def _form(request: Request, user, group: PermissionGroup | None, values: dict, error: str = '', field: str = '',
          status_code: int = 200, members: int = 0):
    return _page(request, 'admin/group_form.html', status_code=status_code, user=user, group=group, values=values,
                 error=error, error_field=field, sections=_sections(), implies=permissions.IMPLIES, members=members)


@router.get('/new', name='admin_group_new')
async def group_new(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    return _form(request, user, None, {'name': '', 'description': '', 'codes': set()})


@router.post('/new', name='admin_group_create')
async def group_create(request: Request, name: str = Form('', max_length=300), description: str = Form('', max_length=1000),
                       code: list[str] = Form([]), session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM, post=True)
    if user is None:
        return _to_login(request)
    values = {'name': name, 'description': description, 'codes': permissions.closure(code)}
    try:
        await groups.save(session, user, None, name, description, code)
    except UserError as error:
        await session.rollback()
        await session.refresh(user)
        return _form(request, user, None, values, str(error), error.field, status_code=422)
    return _back(request, 'created')


async def _group(session: AsyncSession, group_id: int) -> tuple[PermissionGroup | None, set[str], int]:
    group = await session.get(PermissionGroup, group_id)
    if group is None:
        return None, set(), 0
    codes = permissions.group_codes(group, set((await session.scalars(
        select(GroupPermission.code).where(GroupPermission.group_id == group_id))).all()))
    members = await session.scalar(select(func.count()).select_from(AdminUserGroup).where(AdminUserGroup.group_id == group_id)) or 0
    return group, set(codes), members



@router.get('/{group_id:int}', name='admin_group_edit')
async def group_edit(group_id: int, request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM)
    if user is None:
        return _to_login(request)
    group, codes, members = await _group(session, group_id)
    if group is None:
        return _back(request, error='missing')
    return _form(request, user, group, {'name': group.name, 'description': group.description, 'codes': codes}, members=members)


@router.post('/{group_id:int}', name='admin_group_save')
async def group_save(group_id: int, request: Request, name: str = Form('', max_length=300), description: str = Form('', max_length=1000),
                     code: list[str] = Form([]), session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM, post=True)
    if user is None:
        return _to_login(request)
    group, _, members = await _group(session, group_id)
    if group is None:
        return _back(request, error='missing')
    values = {'name': name, 'description': description, 'codes': permissions.closure(code)}
    try:
        await groups.save(session, user, group, name, description, code)
    except UserError as error:
        await session.rollback()
        await session.refresh(user)
        await session.refresh(group)
        if error.code:
            return _back(request, error=error.code)
        return _form(request, user, group, values, str(error), error.field, status_code=422, members=members)
    return _back(request, 'saved')


@router.post('/{group_id:int}/delete', name='admin_group_delete')
async def group_delete(group_id: int, request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, PERM, post=True)
    if user is None:
        return _to_login(request)
    group = await session.get(PermissionGroup, group_id)
    if group is None:
        return _back(request, error='missing')
    try:
        await groups.remove(session, user, group)
    except UserError as error:
        await session.rollback()
        return _back(request, error=error.code or 'forbidden')
    return _back(request, 'deleted')
