"""Admin: the accounts — the list (search, filter, sort), a new account, editing, access (role and groups), switching off, deleting.

The rules live in app/users.py (who may touch whom); the table follows app/admin_tables.py like every admin table.
Every route needs a permission, checked before the target is looked up.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, permissions, users
from ..admin_tables import Column, TableSpec, fetch_page, table_view
from ..auth import authorize
from ..models import AdminUser, ShortLink
from ..permissions import ADMIN, ROLE_LABELS, ROOT, USER
from .admin import NO_INDEX, _page, _to_login

router = APIRouter(prefix='/admin/users', default_response_class=HTMLResponse, include_in_schema=False)

STATUS = case((AdminUser.is_active.is_(False), 'off'), (AdminUser.last_login_at.is_(None), 'never'), else_='active')
STATUS_LABELS = {'active': 'Активний', 'never': 'Ще не входив', 'off': 'Вимкнений'}
USERS_TABLE = TableSpec('users', default='-created', tiebreak=('email',), row_key=('id',), columns=(
    Column('email', 'Пошта', expr=AdminUser.email, searchable=True, field='email'),
    Column('role', 'Роль', expr=AdminUser.role, filterable=True, field='role'),
    Column('status', 'Статус', expr=STATUS, filterable=True, field='status'),
    Column('created', 'Створено', expr=AdminUser.created_at, desc_first=True, field='created_at'),
    Column('last', 'Останній вхід', expr=AdminUser.last_login_at, desc_first=True, field='last_login_at'),
))
NOTICES = {'created': 'Користувача створено.', 'saved': 'Зміни збережено.', 'deleted': 'Користувача видалено.',
           'enabled': 'Користувача увімкнено.', 'disabled': 'Користувача вимкнено: він більше не може увійти.',
           'access': 'Доступ змінено.'}
ERRORS = {'self': 'Свій обліковий запис змінюють у профілі.',
          'last': 'Це останній активний адміністратор: його не можна вимкнути, понизити чи видалити.',
          'root': 'Root змінити не можна.', 'forbidden': 'Недостатньо прав для цієї дії.',
          'orders': 'За цим акаунтом були оплати: записи про них зберігаються за обліком, тому видалити акаунт не можна. Вимкніть його.',
          'nogroup': 'Групу не знайдено.', 'grant': 'Не можна видати групу, що дає права, яких немає у вас.'}


def _back(request: Request, notice: str = '', error: str = '') -> RedirectResponse:
    query = f'?notice={notice}' if notice else (f'?error={error}' if error else '')
    return RedirectResponse(request.app.url_path_for('admin_users') + query, status_code=303, headers=NO_INDEX)


def _to_profile(request: Request) -> RedirectResponse:
    return RedirectResponse(request.app.url_path_for('admin_profile'), status_code=303, headers=NO_INDEX)


def _stmt():
    return select(AdminUser.id.label('id'), AdminUser.email.label('email'), AdminUser.role.label('role'), STATUS.label('status'),
                  AdminUser.created_at.label('created_at'), AdminUser.last_login_at.label('last_login_at'))


async def _counts(session: AsyncSession) -> tuple[int, int, dict[str, int], dict[str, int]]:
    by_status: dict[str, int] = dict((await session.execute(select(STATUS, func.count()).group_by(STATUS))).all())
    by_role: dict[str, int] = dict((await session.execute(select(AdminUser.role, func.count()).group_by(AdminUser.role))).all())
    return sum(by_status.values()), by_status.get('off', 0), by_status, by_role


def _list_context(view, user: AdminUser, page, total: int, off: int, by_status: dict, by_role: dict) -> dict:
    return {'user': user, 'view': view, 'page': page, 'total': total, 'off': off, 'status_labels': STATUS_LABELS, 'role_labels': ROLE_LABELS,
                'statuses': [(s, by_status.get(s, 0)) for s in STATUS_LABELS],
                'roles': [(r, by_role.get(r, 0)) for r in (ROOT, ADMIN, USER)]}


@router.get('/', name='admin_users')
async def users_list(request: Request, notice: str = '', error: str = '', session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.view')
    if user is None:
        return _to_login(request)
    view = table_view(request, USERS_TABLE)
    page = await fetch_page(session, _stmt(), view, dict)
    for row in page.rows:       # what the buttons of each row may do (the server checks again on every POST)
        row['me'] = row['id'] == user.id
        row['can_edit'] = users.may_edit(user, row)
    total, off, by_status, by_role = await _counts(session)
    return _page(request, 'admin/users.html', notice=NOTICES.get(notice, ''), error=ERRORS.get(error, ''),
                 **_list_context(view, user, page, total, off, by_status, by_role))


async def _missing(request: Request, session: AsyncSession, user: AdminUser):
    total, off, by_status, by_role = await _counts(session)
    return _page(request, 'admin/users.html', status_code=404, notice='', error='Користувача не знайдено.',
                 **_list_context(table_view(request, USERS_TABLE), user, None, total, off, by_status, by_role))


def _form(request: Request, user: AdminUser, target: AdminUser | None, values: dict, error: str = '', field: str = '',
          status_code: int = 200, extra: dict | None = None):
    return _page(request, 'admin/user_form.html', status_code=status_code, user=user, target=target, values=values,
                 error=error, error_field=field, role_labels=ROLE_LABELS,
                 can_edit=target is None or users.may_edit(user, target),
                 can_access=target is None or users.may_set_access(user, target), **(extra or {}))


async def _groups_context(session: AsyncSession, user: AdminUser, member_ids: set[int]) -> dict:
    return {'groups': await users.grantable_groups(session, user), 'member_ids': member_ids,
            'labels': {code: label for code, (_, label) in permissions.PERMISSIONS.items()}}


async def _edit_context(session: AsyncSession, user: AdminUser, target: AdminUser) -> dict:
    links = await session.scalar(select(func.count()).select_from(ShortLink).where(ShortLink.created_by == target.id)) or 0
    member_ids = set(await users.target_groups(session, target.id))
    return {'links': links,
            **await _groups_context(session, user, member_ids)}


def _new_values(email: str = '', active: bool = True, role: str = USER, group_ids=()) -> dict:
    return {'email': email.strip(), 'active': active, 'role': role, 'group_ids': set(group_ids)}


@router.get('/new', name='admin_user_new')
async def user_new(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.manage')
    if user is None:
        return _to_login(request)
    return _form(request, user, None, _new_values(), extra=await _groups_context(session, user, set()))


@router.post('/new', name='admin_user_create')
async def user_create(request: Request, email: str = Form('', max_length=300), active: str = Form(''), role: str = Form(USER, max_length=20),
                      group: list[int] = Form([]), session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.manage', post=True)
    if user is None:
        return _to_login(request)
    values = _new_values(email, bool(active), role, group)
    try:
        await users.create(session, user, email, bool(active), role, group)
    except users.UserError as error:
        await session.rollback()
        await session.refresh(user)
        return _form(request, user, None, values, str(error), error.field or ('groups' if error.code == 'grant' else ''),
                     status_code=422, extra=await _groups_context(session, user, set(group)))
    return _back(request, 'created')


async def _target(session: AsyncSession, user_id: int) -> AdminUser | None:
    return await session.get(AdminUser, user_id)


@router.get('/{user_id:int}', name='admin_user_edit')
async def user_edit(user_id: int, request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.view')
    if user is None:
        return _to_login(request)
    if user_id == user.id:
        return _to_profile(request)
    target = await _target(session, user_id)
    if target is None:
        return await _missing(request, session, user)
    values = {'email': target.email, 'active': target.is_active, 'role': target.role}
    return _form(request, user, target, values, extra=await _edit_context(session, user, target))


async def _edit_failed(request, session, user, target, values, error, group_ids):
    await session.rollback()
    await session.refresh(user)
    await session.refresh(target)
    if error.code in ERRORS and error.code != 'grant':
        return _back(request, error=error.code)
    extra = await _edit_context(session, user, target)
    if error.code == 'grant':
        extra['member_ids'] = set(group_ids)
    return _form(request, user, target, values, str(error), error.field or ('groups' if error.code == 'grant' else ''),
                 status_code=422, extra=extra)


@router.post('/{user_id:int}', name='admin_user_save')
async def user_save(user_id: int, request: Request, email: str = Form('', max_length=300), active: str = Form(''),
                    session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.manage', post=True)
    if user is None:
        return _to_login(request)
    target = await _target(session, user_id)
    if target is None:
        return await _missing(request, session, user)
    values = {'email': email.strip(), 'active': bool(active), 'role': target.role}
    try:
        await users.update_user(session, user, target, email, bool(active))
    except users.UserError as error:
        return await _edit_failed(request, session, user, target, values, error, [])
    return _back(request, 'saved')


@router.post('/{user_id:int}/access', name='admin_user_access')
async def user_access(user_id: int, request: Request, role: str = Form('', max_length=20), group: list[int] = Form([]),
                      session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.promote', post=True)
    if user is None:
        return _to_login(request)
    target = await _target(session, user_id)
    if target is None:
        return await _missing(request, session, user)
    values = {'email': target.email, 'active': target.is_active, 'role': role}
    try:
        await users.set_access(session, user, target, role, group)
    except users.UserError as error:
        return await _edit_failed(request, session, user, target, values, error, group)
    return _back(request, 'access')


@router.post('/{user_id:int}/toggle', name='admin_user_toggle')
async def user_toggle(user_id: int, request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.manage', post=True)
    if user is None:
        return _to_login(request)
    target = await _target(session, user_id)
    if target is None:
        return await _missing(request, session, user)
    try:
        await users.set_active(session, user, target, not target.is_active)
    except users.UserError as error:
        await session.rollback()
        return _back(request, error=error.code or 'forbidden')
    return _back(request, 'enabled' if target.is_active else 'disabled')


@router.post('/{user_id:int}/delete', name='admin_user_delete')
async def user_delete(user_id: int, request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session, 'users.manage', post=True)
    if user is None:
        return _to_login(request)
    target = await _target(session, user_id)
    if target is None:
        return await _missing(request, session, user)
    try:
        await users.remove(session, user, target)
    except users.UserError as error:
        await session.rollback()
        return _back(request, error=error.code or 'forbidden')
    return _back(request, 'deleted')
