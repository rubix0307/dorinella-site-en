"""Admin tables — the one way to build a table in the admin.

Rules: every admin table is sortable by every column, and sorting, searching and filtering
happen on the server. tests/test_admin_tables.py fails for any admin <th> that isn't a sort link,
so a new table has to go through this module:

    SPEC = TableSpec('links', default='-clicks', columns=(
        Column('link', 'Link', expr=Click.target_url, searchable=True),   # ?links_q=…
        Column('placement', 'Placement', expr=Click.placement, filterable=True),  # ?links_placement=…
        Column('clicks', 'Clicks', expr=clicks_label, numeric=True),     # SQL: ORDER BY expr
        Column('day', 'Day', attr='day'),                                 # in-memory: sort by attr
    ))
    view = table_view(request, SPEC)          # reads ?links_sort=-clicks etc. (whitelisted)
    stmt = view.order(view.where(stmt))       # SQL tables; rows = view.sort(rows) for Python lists

Templates use `sort_th(view, key)` from admin/_table.html for each header cell.

Long tables are cursor-paginated (keyset, never OFFSET): `page = await fetch_page(session, stmt, view)`.
The cursor is the last row's unique key (`TableSpec.row_key`); the database itself looks up that row's
sort values, so paging is exact for any sort column — aggregates and NULLs included — and rows added
meanwhile never shift a page. Every Column of a paginated table names its select label in `field`.
"""
import base64
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Request
from sqlalchemy import and_, false, func, nulls_last, or_, select

SEARCH_MAX = 100
SEARCHES_MAX = 20             # search inputs one table accepts at once
TRIGRAM_THRESHOLD = 0.35  # pg_trgm word_similarity: 1 = exact word; 0.35 still finds 'yuotube' → youtube
# URL noise shared by every link: dropped from search words so it can't make unrelated links "similar"
_URL_NOISE = re.compile(r'^(https?:)?/*(www\.)?|/+$', re.IGNORECASE)
# a pasted short link (example.com/l/q83we, /l/q83we) is searched by its code
_SHORT_PREFIX = re.compile(r'^(?:.*?/)?l/(?=[a-z0-9])', re.IGNORECASE)
CHOICES_MAX = 50
PAGE_SIZE = 30
TAKE_MAX = 300            # a live refresh re-reads at most this many rows from the top


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    expr: Any = None              # SQL expression to ORDER BY / filter on (SQL-backed tables)
    attr: str | None = None       # attribute of a row object (in-memory tables)
    numeric: bool = False         # right-aligned, and the first click sorts largest first
    desc_first: bool | None = None  # override the first-click direction (dates: newest first)
    searchable: bool = False      # free-text search (ILIKE) over this column
    filterable: bool = False      # pick-from-list filter (IN) on this column
    field: str | None = None      # label of this column in the paginated select (keyset paging)
    sortable: bool = True         # False: search-only column that isn't shown (never a ?…_sort= key)
    default: tuple[str, ...] = () # filter values applied when the request picks none (e.g. status: active)

    @property
    def first_desc(self) -> bool:
        return self.numeric if self.desc_first is None else self.desc_first


@dataclass(frozen=True)
class TableSpec:
    id: str                       # also the query-param prefix: ?<id>_sort=-column
    columns: tuple[Column, ...]
    default: str                  # e.g. '-clicks' (desc) or 'placement' (asc)
    tiebreak: tuple[str, ...] = field(default=())   # extra keys for a stable order
    row_key: tuple[str, ...] = ()  # select labels that identify one row: the pagination cursor
    first_take: int = 0            # rows on the first page when it is smaller than the following ones (0 = same)

    @property
    def param(self) -> str:
        return f'{self.id}_sort'

    @property
    def search_param(self) -> str:
        return f'{self.id}_q'

    def filter_param(self, key: str) -> str:
        return f'{self.id}_{key}'

    def column(self, key: str) -> Column | None:
        return next((c for c in self.columns if c.key == key), None)

    def required(self, key: str) -> Column:
        """A column that must exist (the key came from the spec itself, not from the request)."""
        found = self.column(key)
        if found is None:
            raise KeyError(key)
        return found

    @property
    def after_param(self) -> str:
        return f'{self.id}_after'

    @property
    def take_param(self) -> str:
        return f'{self.id}_take'

    def own_params(self) -> set[str]:
        return {self.param, self.search_param, *(self.filter_param(c.key) for c in self.columns if c.filterable)}


def _parse(token: str) -> tuple[str, bool]:
    return (token[1:], True) if token.startswith('-') else (token, False)


def _search_words(text: str) -> list[tuple[str, bool]]:
    """(word, exact) pairs of a search. A pasted tracking link (…/go/?to=<url>&from=<placement>&sig=…)
    becomes its destination + placement matched EXACTLY, so it finds just the row it was copied from;
    everything else is a fuzzy word."""
    words = []
    for raw in text.split():
        parts = urlsplit(raw if '://' in raw else f'//{raw}')
        query = parse_qs(parts.query)
        if parts.path.rstrip('/').endswith('/go') and 'to' in query:
            words.append((query['to'][0], True))
            words.extend((placement, True) for placement in query.get('from', [])[:1])
        else:
            words.append((raw, False))
    return words


def _like_pattern(text: str) -> str:
    escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


@dataclass(frozen=True)
class TableView:
    spec: TableSpec
    key: str
    desc: bool
    query: tuple[tuple[str, str], ...]   # the request's query params, kept in every link
    searches: tuple[str, ...] = ()   # ?<id>_q=…&<id>_q=… — each its own input; a row matching ANY is shown
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()   # (column key, selected values)
    after: tuple | None = None     # decoded cursor: row_key values of the last row already shown
    take: int = PAGE_SIZE

    @property
    def search(self) -> str:
        """The first search (tables with a single search box)."""
        return self.searches[0] if self.searches else ''

    @property
    def token(self) -> str:
        return f'{"-" if self.desc else ""}{self.key}'

    @property
    def is_default(self) -> bool:
        return self.token == self.spec.default

    def selected(self, key: str) -> tuple[str, ...]:
        return dict(self.choices).get(key, ())

    @property
    def is_filtered(self) -> bool:
        return bool(self.searches or any(values and values != self.spec.required(key).default
                                         for key, values in self.choices))

    # ---- links & forms
    def aria_sort(self, key: str) -> str:
        if key != self.key:
            return 'none'
        return 'descending' if self.desc else 'ascending'

    def href(self, key: str) -> str:
        """Link that sorts by `key`: toggles direction on the active column, keeps everything else."""
        column = self.spec.required(key)
        desc = (not self.desc) if key == self.key else column.first_desc
        token = f'{"-" if desc else ""}{key}'
        params = [(k, v) for k, v in self.query if k != self.spec.param]
        if token != self.spec.default:
            params.append((self.spec.param, token))
        return '?' + urlencode(params) if params else '?'

    def href_dir(self, key: str, desc: bool) -> str:
        """Link that sorts by `key` in the given direction (the phone sort menu)."""
        token = f'{"-" if desc else ""}{key}'
        params = [(k, v) for k, v in self.query if k != self.spec.param]
        if token != self.spec.default:
            params.append((self.spec.param, token))
        return '?' + urlencode(params) if params else '?'

    def is_sorted(self, key: str, desc: bool) -> bool:
        return self.key == key and self.desc == desc

    def toggle_href(self, key: str, value: str) -> str:
        """Same view with `value` switched on/off in the `key` filter (e.g. the "★ Favorites" chip)."""
        param = self.spec.filter_param(key)
        on = value in self.selected(key)
        params = [(k, v) for k, v in self.query if not (k == param and v == value)]
        if not on:
            params.append((param, value))
        return '?' + urlencode(params) if params else '?'

    def next_href(self, cursor: str) -> str:
        """The next page of this table (same sort, search and filters)."""
        return '?' + urlencode([*self.query, (self.spec.after_param, cursor)])

    def reset_href(self) -> str:
        """Same page without this table's search and filters (sorting kept)."""
        own = self.spec.own_params() - {self.spec.param}
        params = [(k, v) for k, v in self.query if k not in own]
        return '?' + urlencode(params) if params else '?'

    def state(self) -> list[tuple[str, str]]:
        """This table's non-default state as query pairs (for other forms on the page)."""
        pairs = [] if self.is_default else [(self.spec.param, self.token)]
        pairs.extend((self.spec.search_param, s) for s in self.searches)
        for key, values in self.choices:
            if values != self.spec.required(key).default:      # the default needs no parameter
                pairs.extend((self.spec.filter_param(key), v) for v in values)
        return pairs

    def outside_fields(self) -> list[tuple[str, str]]:
        """Hidden fields for this table's own filter form: everything on the page except its search/filters."""
        own = self.spec.own_params() - {self.spec.param}
        return [(k, v) for k, v in self.query if k not in own]

    # ---- server-side filtering & sorting
    def where(self, stmt, dialect: str = 'postgresql'):
        """Fuzzy search: within one search every word must match a searchable column, either as a
        substring or — on PostgreSQL — by trigram (n-gram) similarity, so typos and partial input
        still find rows (pg_trgm, GIN-indexed); other databases fall back to substrings.
        Several searches (one per input) are alternatives: a row matching any of them is shown."""
        if self.searches:
            columns = [c for c in self.spec.columns if c.searchable]
            alternatives = []
            for search in self.searches:
                conditions = []
                for raw, exact in _search_words(search):
                    if exact:
                        conditions.append(or_(*(c.expr == raw for c in columns)))
                        continue
                    word = _SHORT_PREFIX.sub('', _URL_NOISE.sub('', raw)) or raw
                    matches = [c.expr.ilike(_like_pattern(word), escape='\\') for c in columns]
                    if dialect == 'postgresql':
                        matches += [func.word_similarity(word.lower(), func.lower(c.expr)) >= TRIGRAM_THRESHOLD
                                    for c in columns]
                    conditions.append(or_(*matches))
                alternatives.append(and_(*conditions))
            stmt = stmt.where(or_(*alternatives))
        for key, values in self.choices:
            if values:
                stmt = stmt.where(self.spec.required(key).expr.in_(values))
        return stmt

    def _order_keys(self) -> list[tuple[Column, bool]]:
        keys = [(self.spec.required(self.key), self.desc)]
        for token in self.spec.tiebreak:
            name, desc = _parse(token)
            if name != self.key:
                keys.append((self.spec.required(name), desc))
        return keys

    def order(self, stmt):
        """Apply ORDER BY for SQL-backed tables (NULLs always last)."""
        return stmt.order_by(*(nulls_last(c.expr.desc() if d else c.expr.asc()) for c, d in self._order_keys()))

    def sort(self, rows: Iterable[Any]) -> list[Any]:
        """Sort in-memory rows (NULLs always last), stable across the tiebreak keys."""
        result = list(rows)
        for column, desc in reversed(self._order_keys()):
            attr = column.attr
            if attr is None:
                raise ValueError(f'column {column.key} has no row attribute to sort by')
            present = [r for r in result if getattr(r, attr) is not None]
            missing = [r for r in result if getattr(r, attr) is None]
            result = sorted(present, key=partial(_attr_value, attr), reverse=desc) + missing
        return result


# ---------------------------------------------------------------- cursor pagination

@dataclass(frozen=True)
class Page:
    rows: list
    total: int
    next: str | None              # cursor of the next page, None on the last one


def encode_cursor(values: Sequence[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(values), separators=(',', ':')).encode()).decode().rstrip('=')


def decode_cursor(raw: str, size: int) -> tuple | None:
    try:
        values = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return None
    ok = isinstance(values, list) and len(values) == size and all(isinstance(v, (str, int)) for v in values)
    return tuple(values) if ok else None


def _after(keys: list[tuple[Any, bool]], cursor: list[Any]):
    """Rows strictly after the cursor row in ORDER BY keys (NULLs last), i.e. the classic
    (k1 > v1) OR (k1 = v1 AND k2 > v2) OR … with per-key direction and NULL handling."""
    clauses = []
    for i, (column, desc) in enumerate(keys):
        value = cursor[i]
        beyond = and_(value.isnot(None), or_(column < value if desc else column > value, column.is_(None)))
        same = [or_(column == v, and_(column.is_(None), v.is_(None))) for (column, _), v in zip(keys[:i], cursor[:i], strict=False)]
        clauses.append(and_(*same, beyond))
    return or_(*clauses) if clauses else false()


async def fetch_page(session, stmt, view: TableView, row, params: dict | None = None) -> Page:
    """One page of a grouped/joined select, filtered and sorted by `view`, `row(mapping)` builds each item."""
    spec = view.spec
    sub = view.where(stmt, session.get_bind().dialect.name).subquery()
    keys = [(sub.c[c.field], d) for c, d in view._order_keys()]
    keys += [(sub.c[name], False) for name in spec.row_key if name not in {k.name for k, _ in keys}]
    query = select(sub)
    if view.after is not None:
        match = and_(*(sub.c[name] == value for name, value in zip(spec.row_key, view.after, strict=False)))
        anchor = [select(column).where(match).scalar_subquery() for column, _ in keys]
        query = query.where(_after(keys, anchor))
    query = query.order_by(*(nulls_last(c.desc() if d else c.asc()) for c, d in keys)).limit(view.take + 1)
    found = (await session.execute(query, params or {})).mappings().all()
    total = await session.scalar(select(func.count()).select_from(sub), params or {})
    more = len(found) > view.take
    found = found[:view.take]
    cursor = encode_cursor([found[-1][name] for name in spec.row_key]) if more and found else None
    return Page([row(m) for m in found], total or 0, cursor)


def _sort_value(value: Any) -> Any:
    """Text sorts naturally (gut_health_2 before gut_health_10), case-blind; anything else as it is."""
    if not isinstance(value, str):
        return value
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r'(\d+)', value.casefold()) if part)


# one-off params (a notice after an action) are never carried into sort/filter links
TRANSIENT_PARAMS = frozenset({'notice', 'slug'})


def _attr_value(attr: str, row: Any) -> Any:
    return _sort_value(getattr(row, attr))


def table_view(request: Request, spec: TableSpec) -> TableView:
    """The table's state from the query string; unknown columns fall back to the default sort."""
    params = request.query_params
    key, desc = _parse(params.get(spec.param, '') or spec.default)
    chosen = spec.column(key)
    if chosen is None or not chosen.sortable:
        key, desc = _parse(spec.default)
    searches = tuple(dict.fromkeys(
        ' '.join(raw.split())[:SEARCH_MAX] for raw in params.getlist(spec.search_param) if raw.strip()
    ))[:SEARCHES_MAX] if any(c.searchable for c in spec.columns) else ()
    choices = tuple(
        (c.key, tuple(dict.fromkeys(v[:200] for v in params.getlist(spec.filter_param(c.key)) if v))[:CHOICES_MAX]
         or c.default)
        for c in spec.columns if c.filterable
    )
    # position params (cursor, rows to re-read) never travel into sort / filter / next-page links
    query = tuple((k, v) for k, v in params.multi_items()
                  if k not in TRANSIENT_PARAMS and not k.endswith(('_after', '_take')))
    after = decode_cursor(params.get(spec.after_param, ''), len(spec.row_key)) if spec.row_key else None
    try:
        take = max(1, min(TAKE_MAX, int(params.get(spec.take_param, PAGE_SIZE))))
    except ValueError:
        take = PAGE_SIZE
    if spec.first_take and after is None and spec.take_param not in params:
        take = spec.first_take
    return TableView(spec, key, desc, query, searches, choices, after, take)


def page_state(views: Sequence[TableView]) -> list[tuple[str, str]]:
    """Hidden fields for page-level forms (e.g. the date filter) so they keep every table's state."""
    return [pair for view in views for pair in view.state()]
