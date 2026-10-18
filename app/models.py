"""Database models."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, Uuid, false, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# BigInteger on Postgres, plain INTEGER on SQLite (tests) so autoincrement works there too
BigId = BigInteger().with_variant(Integer, 'sqlite')


class AdminUser(Base):
    __tablename__ = 'admin_users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)        # stored lower-case
    name: Mapped[str | None] = mapped_column(String(100))              # the name of the Google account, filled at sign-in
    password_hash: Mapped[str] = mapped_column(String(255))            # always auth.NO_PASSWORD (sign-in is Google only); kept in the session signature
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str] = mapped_column(String(10), server_default='admin')         # root / admin / user, see app/permissions.py
    session_version: Mapped[int] = mapped_column(Integer, server_default='0')    # in the cookie signature: bumping it ends the sessions
    timezone: Mapped[str] = mapped_column(String(64), server_default='UTC')    # an IANA name, or 'browser' (the zone of the browser), see app/timezones.py

    perms: frozenset[str] = frozenset()      # filled per request by auth.authorize (plain attribute: it survives a rollback)

    def can(self, code: str | tuple[str, ...]) -> bool:
        """One permission, or any of several (a tuple)."""
        return code in self.perms if isinstance(code, str) else any(c in self.perms for c in code)


class PermissionGroup(Base):
    """A named list of permissions that admins are given together (the system one means "everything assignable")."""

    __tablename__ = 'permission_groups'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(200), server_default='')
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupPermission(Base):
    __tablename__ = 'group_permissions'

    group_id: Mapped[int] = mapped_column(ForeignKey('permission_groups.id', ondelete='CASCADE'), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)


class AdminUserGroup(Base):
    __tablename__ = 'admin_user_groups'

    user_id: Mapped[int] = mapped_column(ForeignKey('admin_users.id', ondelete='CASCADE'), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('permission_groups.id', ondelete='CASCADE'), primary_key=True, index=True)


class LoginFailure(Base):
    """A failed admin login, for throttling shared by every worker (see app/auth.py)."""

    __tablename__ = 'login_failures'

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)


class PageView(Base):
    """One view of a public HTML page (bots, admin, API and static files are not recorded)."""

    __tablename__ = 'page_views'

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    referrer_host: Mapped[str | None] = mapped_column(String(255))     # external sites only
    utm_source: Mapped[str | None] = mapped_column(String(128))
    utm_medium: Mapped[str | None] = mapped_column(String(128))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))
    # the daily IP + user-agent hash: who it was when the browser keeps no cookie (and for rows before visitor ids)
    visitor_hash: Mapped[str] = mapped_column(String(32), index=True)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)    # app/visitors.py
    device: Mapped[str] = mapped_column(String(16))


class ShortLink(Base):
    """Admin-made short link: /l/<slug> redirects to target_url and every hit is counted."""

    __tablename__ = 'short_links'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    target_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200), server_default='')
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_favorite: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey('admin_users.id', ondelete='SET NULL'))


class FavoriteLink(Base):
    """A starred row of the clicks table: one destination in one placement."""

    __tablename__ = 'favorite_links'
    __table_args__ = (UniqueConstraint('target_url', 'placement'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_url: Mapped[str] = mapped_column(Text)
    placement: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundClick(Base):
    """One click on an external link, recorded by the /go/ redirect."""

    __tablename__ = 'outbound_clicks'

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    target_url: Mapped[str] = mapped_column(Text)
    target_host: Mapped[str] = mapped_column(String(255), index=True)
    placement: Mapped[str] = mapped_column(String(64), index=True)   # where on the site, e.g. 'plan:immersion'

    page_path: Mapped[str | None] = mapped_column(String(512), index=True)   # page the click came from
    utm_source: Mapped[str | None] = mapped_column(String(128))
    utm_medium: Mapped[str | None] = mapped_column(String(128))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))

    # hash of IP + user agent with a daily rotating salt (no raw IPs stored); the visitor id when there is one
    visitor_hash: Mapped[str] = mapped_column(String(32), index=True)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    device: Mapped[str] = mapped_column(String(16))           # mobile / tablet / desktop / bot
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # set for clicks on admin short links (/l/<slug>); NULL = a link on the site itself
    short_link_id: Mapped[int | None] = mapped_column(ForeignKey('short_links.id', ondelete='SET NULL'), index=True)
    referrer_host: Mapped[str | None] = mapped_column(String(255))   # e.g. l.instagram.com


class BioButton(Base):
    """A button of the /links/ page. It can only point at an admin short link, so every click is in analytics."""

    __tablename__ = 'bio_buttons'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    # none: a group (no link of its own, only sub-links under it)
    short_link_id: Mapped[int | None] = mapped_column(ForeignKey('short_links.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(String(120), server_default='')
    image: Mapped[str] = mapped_column(String(32))        # a logo key, a Lucide icon name, or 'photo'
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_visible: Mapped[bool] = mapped_column(Boolean, server_default=true())
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)   # small image, made in the admin
    photo_type: Mapped[str] = mapped_column(String(16), server_default='')       # image/webp, image/png, image/jpeg
    photo_hash: Mapped[str] = mapped_column(String(16), server_default='')       # in the URL: a new photo, a new URL
    short_link: Mapped[ShortLink | None] = relationship(lazy='joined')
    sublinks: Mapped[list['BioSublink']] = relationship(lazy='selectin', order_by='BioSublink.position',
                                                        cascade='all, delete-orphan')


class BioSublink(Base):
    """A sub-link (sitelink) under a button of the /links/ page: a small second-level link. Like the button,
    it can only point at an admin short link, so its clicks are in analytics too."""

    __tablename__ = 'bio_sublinks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    button_id: Mapped[int] = mapped_column(ForeignKey('bio_buttons.id', ondelete='CASCADE'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    short_link_id: Mapped[int] = mapped_column(ForeignKey('short_links.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(60))
    image: Mapped[str] = mapped_column(String(32), server_default='')    # a logo key, a Lucide icon name, 'photo'; '' = none
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    photo_type: Mapped[str] = mapped_column(String(16), server_default='')
    photo_hash: Mapped[str] = mapped_column(String(16), server_default='')
    short_link: Mapped[ShortLink] = relationship(lazy='joined')


class BioPage(Base):
    """The one row of /links/ settings: the header (photo, name, line under it) and the ads card
    (no short link = no card)."""

    __tablename__ = 'bio_page'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), server_default='Dori')
    tagline: Mapped[str] = mapped_column(String(160), server_default='')
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)   # none: the site's own portrait
    photo_type: Mapped[str] = mapped_column(String(16), server_default='')
    photo_hash: Mapped[str] = mapped_column(String(16), server_default='')
    ads_title: Mapped[str] = mapped_column(String(80), server_default='')
    ads_text: Mapped[str] = mapped_column(Text, server_default='')
    ads_button: Mapped[str] = mapped_column(String(60), server_default='')
    ads_short_link_id: Mapped[int | None] = mapped_column(ForeignKey('short_links.id', ondelete='SET NULL'))
    ads_short_link: Mapped[ShortLink | None] = relationship(lazy='joined')


# ---------------------------------------------------------------- admin video calls (app/meet.py)
# One call = one LiveKit room = one row. A closed call is never opened again. Every published track is recorded in
# chunks (MeetingSegment); each chunk is encrypted with the key of the call (Meeting.dek, itself wrapped with the master
# key) before it leaves this computer, and lives in the private bucket. Only this site can read it back.


# ---------------------------------------------------------------- visitors and test attempts

class Visitor(Base):
    """A visitor whose id cookie has come back at least once (app/visitors.py) — bots and cookie-less
    clients never get a row. When they first came is in the id itself (a UUIDv7)."""

    __tablename__ = 'visitors'

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # the old dori_vid cookie this id replaced (its hash, as on old recipe_views rows)
    legacy_hash: Mapped[str | None] = mapped_column(String(32), index=True)


class VisitorAccount(Base):
    """A visitor id seen signed in to an account. Many to many: one person has a phone and a laptop,
    one family laptop may see two accounts. The visitor id stays after sign-in and sign-out."""

    __tablename__ = 'visitor_accounts'

    visitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('visitors.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)   # site accounts to come
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_signed_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestVersion(Base):
    """A test exactly as it was answered: its questions, options with points and scoring. `version` is a hash
    of that content (app/learn.py), so an edited question makes a new version and old attempts stay readable."""

    __tablename__ = 'test_versions'
    __test__ = False    # not a pytest class

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestAttempt(Base):
    """One completed test (app/test_attempts.py): every answer, and the result the person saw."""

    __tablename__ = 'test_attempts'
    __test__ = False

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)    # a UUIDv7, made in Python
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('visitors.id'), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)    # set when the visitor signs in
    test_slug: Mapped[str] = mapped_column(String(64), index=True)
    test_version: Mapped[str] = mapped_column(String(16))               # → test_versions
    answers: Mapped[list] = mapped_column(JSON)          # the chosen option of every question, 0-based
    score: Mapped[int | None] = mapped_column(Integer)   # single-score tests
    band_key: Mapped[str | None] = mapped_column(String(32), index=True)
    scales: Mapped[dict | None] = mapped_column(JSON)    # multi-scale tests (DEBQ): {key: score}
    source: Mapped[str] = mapped_column(String(8))       # form (no JS) / js / api
    device: Mapped[str] = mapped_column(String(16))


class CourseAnswer(Base):
    """What a person has answered / passed in a course (app/course_progress.py): one row per key, the latest value."""

    __tablename__ = 'course_answers'
    __table_args__ = (UniqueConstraint('user_id', 'course', 'key'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('admin_users.id', ondelete='CASCADE'), index=True)
    course: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(64))             # safety / goal / list.<id> / done.<page>
    value: Mapped[dict | list | str | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseAnswerLog(Base):
    """Every change of an answer, with its time (the history behind CourseAnswer)."""

    __tablename__ = 'course_answer_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('admin_users.id', ondelete='CASCADE'), index=True)
    course: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict | list | str | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AuditLog(Base):
    """What changed in any tracked table (app/audit.py): written by a database trigger, in the transaction of the change. Nothing here is edited by the app."""

    __tablename__ = 'audit_log'
    __table_args__ = (Index('ix_audit_log_row', 'table_name', 'row_id', 'id'),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    txid: Mapped[int | None] = mapped_column(BigInteger)                                   # the PostgreSQL transaction (rows of one transaction share it)
    table_name: Mapped[str] = mapped_column(String(64))
    row_id: Mapped[str] = mapped_column(String(64))                                        # the id of the changed row
    op: Mapped[str] = mapped_column(String(6))                                             # INSERT / UPDATE / DELETE
    old_data: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), 'postgresql'))   # before (the whole row; none on INSERT)
    new_data: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), 'postgresql'))   # after (the whole row; none on DELETE)
    actor: Mapped[str | None] = mapped_column(String(64))                                  # 'user:<id>' / 'system:<job>'; a script or a manual query: 'db:<database user>'
    actor_id: Mapped[int | None] = mapped_column(Integer, index=True)                      # the id of the person when the actor is one
    db_user: Mapped[str | None] = mapped_column(String(64))                                # the database user that ran the statement


class BackupRun(Base):
    """One run of the daily database backup (app/backup.py): written when it starts, finished with the outcome. Shown nowhere."""

    __tablename__ = 'backup_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)                 # None while it runs
    object_key: Mapped[str | None] = mapped_column(String(200))
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(String(300))


from . import audit  # noqa: E402,F401 — registers the hook that hands the actor to the database (app/audit.py)
