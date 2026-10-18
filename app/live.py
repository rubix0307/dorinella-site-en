"""Live admin updates: "new data" events pushed to open admin pages over a WebSocket.

The site runs several uvicorn workers, so an event can't just be an in-memory callback: a click
handled by worker A must reach an admin page whose socket lives in worker B. With PostgreSQL each
worker coalesces its events into one NOTIFY per ~0.3 s (outside the writers' transactions), and each
worker LISTENs on one connection and fans the event out to its local sockets. Without PostgreSQL (tests, local SQLite) events stay in-process.
"""
import asyncio
import contextlib
import logging

from sqlalchemy import text

from . import db

log = logging.getLogger(__name__)

CHANNEL = 'dori_live'
KINDS = frozenset({'click', 'view', 'link', 'recipes', 'attempt', 'course', 'reviews', 'tgposts'})
SUPPORT_PREFIX = 'support:'      # `support:<ticket id>`: a message or a change in a ticket; read by the sockets of the chat pages only
                                 # `support:<ticket id>:<category>`: the ticket just LEFT that category; only the staff lists of it are told (so they drop the row)


def support_event(kind: str) -> tuple[int, str] | None:
    """(ticket id, the category it left or '') of a `support:` event, None for anything else or malformed."""
    if not kind.startswith(SUPPORT_PREFIX):
        return None
    number, _, left = kind[len(SUPPORT_PREFIX):].partition(':')
    if not (number.isascii() and number.isdigit()) or (left and not (left.isascii() and left.isalpha() and len(left) <= 16)):
        return None
    return int(number), left


def known(kind: str) -> bool:
    return kind in KINDS or support_event(kind) is not None

class Hub:
    """Fan-out of events to the sockets of this process. Slow consumers drop events, never block.

    Thread-safe: each queue is fed on the event loop that owns it.
    """

    def __init__(self) -> None:
        self._queues: dict[asyncio.Queue[str], asyncio.AbstractEventLoop] = {}

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._queues[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._queues.pop(queue, None)

    def publish(self, kind: str) -> None:
        for callback in _callbacks.get(kind, ()):     # in-process reactions, e.g. reload the recipes
            try:
                callback(kind)
            except Exception:
                log.exception('live: %s callback failed', kind)
        for queue, loop in list(self._queues.items()):
            loop.call_soon_threadsafe(self._put, queue, kind)

    @staticmethod
    def _put(queue: asyncio.Queue[str], kind: str) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(kind)

    @property
    def listeners(self) -> int:
        return len(self._queues)
_callbacks: dict[str, list] = {}


def on(kind: str, callback) -> None:
    """Run `callback(kind)` in every worker when an event of this kind arrives (from any worker)."""
    _callbacks.setdefault(kind, []).append(callback)


hub = Hub()
_listener: asyncio.Task | None = None
def _is_postgres() -> bool:
    return db.engine is not None and db.engine.dialect.name == 'postgresql'
FLUSH_DELAY = 0.3   # seconds: events of a burst leave this worker as one NOTIFY per kind
_pending: set[str] = set()
_flush: asyncio.Task | None = None
def published(kind: str) -> None:
    """Call after a successful commit. NOT sent inside the writer's transaction: a NOTIFY takes a
    database-wide lock at commit, which would serialize every click (measured: ~200 req/s ceiling).
    Instead each worker coalesces its events and notifies at most once per FLUSH_DELAY."""
    global _flush
    if not _is_postgres():
        hub.publish(kind)
        return
    _pending.add(kind)
    if _flush is None or _flush.done():
        _flush = asyncio.get_running_loop().create_task(_send_pending(), name='live-flush')
async def notify(kind: str) -> None:
    """For another process (the Telegram sender): tells the web workers right away, in its own short transaction (no coalescing: such events are rare). Never raises."""
    if not _is_postgres():
        hub.publish(kind)
        return
    try:
        async with db.get_engine().begin() as conn:
            await conn.execute(text('SELECT pg_notify(:c, :k)'), {'c': CHANNEL, 'k': kind})
    except Exception:
        log.warning('live: could not send %s', kind, exc_info=True)
async def _send_pending() -> None:
    await asyncio.sleep(FLUSH_DELAY)
    kinds = sorted(_pending)
    _pending.clear()
    try:
        async with db.get_engine().begin() as conn:
            for kind in kinds:
                await conn.execute(text('SELECT pg_notify(:c, :k)'), {'c': CHANNEL, 'k': kind})
    except Exception:
        log.warning('live: could not send %s', kinds, exc_info=True)
async def _listen_forever() -> None:
    """One dedicated LISTEN connection per worker (outside the request pool); reconnects with backoff."""
    import asyncpg

    dsn = db.get_engine().url.set(drivername='postgresql').render_as_string(hide_password=False)
    delay = 1
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            lost = asyncio.Event()
            await conn.add_listener(CHANNEL, lambda *args: hub.publish(args[3]) if known(args[3]) else None)
            conn.add_termination_listener(lambda *_, lost=lost: lost.set())
            delay = 1
            while not lost.is_set():              # a periodic query notices a silently dead socket
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(lost.wait(), 30)
                if not lost.is_set():
                    await conn.execute('SELECT 1')
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('live: LISTEN connection lost, retrying in %ss', delay, exc_info=True)
        finally:
            if conn is not None and not conn.is_closed():
                with contextlib.suppress(Exception):
                    await asyncio.shield(conn.close(timeout=2))
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
def start() -> None:
    global _listener
    if _is_postgres() and _listener is None:
        _listener = asyncio.create_task(_listen_forever(), name='live-listener')
async def stop() -> None:
    global _listener
    if _listener is not None:
        _listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _listener
        _listener = None
