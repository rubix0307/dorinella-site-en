"""Admin: the account's own profile — role and rights."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, permissions, timezones
from ..auth import authorize
from ..permissions import ROLE_LABELS
from .admin import NO_INDEX, _page, _to_login

router = APIRouter(prefix='/admin/profile', default_response_class=HTMLResponse, include_in_schema=False)


def _sections(perms) -> list[tuple[str, list[tuple[str, bool]]]]:
    result: dict[str, list[tuple[str, bool]]] = {}
    for code, (section, label) in permissions.PERMISSIONS.items():
        result.setdefault(section, []).append((label, code in perms))
    return list(result.items())


def _profile(request: Request, user, notice: str = '', error: str = '', status_code: int = 200):
    zone = timezones.resolve(user.timezone, request.cookies.get(timezones.COOKIE))
    return _page(request, 'admin/profile.html', status_code=status_code, user=user, role_label=ROLE_LABELS[user.role], sections=_sections(user.perms),
                 notice=notice, error=error, zones=timezones.ZONES, browser_zone=timezones.BROWSER, zone_label=timezones.label(zone))


@router.get('/', name='admin_profile')
async def profile_page(request: Request, session: AsyncSession = Depends(db.get_session)):
    user = await authorize(request, session)
    if user is None:
        return _to_login(request)
    return _profile(request, user, 'Часовий пояс збережено.' if request.query_params.get('saved') else '')


@router.post('/timezone', name='admin_profile_timezone')
async def save_timezone(request: Request, timezone: str = Form('', max_length=64), session: AsyncSession = Depends(db.get_session)):
    """The time zone the admin works in: what they type for a schedule is read in it, and it is written next to every time."""
    user = await authorize(request, session, post=True)
    if user is None:
        return _to_login(request)
    if not timezones.is_valid_setting(timezone):
        return _profile(request, user, error='Такого часового поясу немає в списку.', status_code=422)
    user.timezone = timezone
    await session.commit()
    return RedirectResponse(request.app.url_path_for('admin_profile') + '?saved=1', status_code=303, headers=NO_INDEX)
