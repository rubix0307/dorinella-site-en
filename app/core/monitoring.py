"""Error tracking, tracing and logs in Sentry — on when SENTRY_DSN is set (production), off otherwise."""
import logging
import os

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

log = logging.getLogger(__name__)

# Never sent, even with send_default_pii: the admin session cookie is as good as a password,
# the visitor id is a tracking identifier.
_PRIVATE_COOKIES = ['dori_admin', 'dori_vid', '__Host-dori_vid']
# Docker probes /health every 15 s and static files are not interesting: keep them out of tracing.
_NOT_TRACED = ('/health', '/static/', '/favicon')


def _traces_sampler(context: dict) -> float:
    path = (context.get('asgi_scope') or {}).get('path', '')
    if path.startswith(_NOT_TRACED):
        return 0.0
    return float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '1.0'))


def init_sentry() -> bool:
    dsn = os.getenv('SENTRY_DSN', '')
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv('SENTRY_ENVIRONMENT', 'production'),
        release=os.getenv('SENTRY_RELEASE') or None,   # the git commit, set by deploy.sh
        # request headers and the visitor's IP (cookies listed above are scrubbed)
        send_default_pii=True,
        event_scrubber=EventScrubber(denylist=DEFAULT_DENYLIST + _PRIVATE_COOKIES, recursive=True),
        # logs: the app's own INFO and up (errors also become issues); one access-log line per
        # request would only repeat the traces and eat the quota
        enable_logs=True,
        integrations=[LoggingIntegration(sentry_logs_level=logging.INFO, level=logging.INFO, event_level=logging.ERROR)],
        traces_sampler=_traces_sampler,   # traces: every request (and its SQL) except /health and static
        enable_metrics=True,
    )
    ignore_logger('uvicorn.access')
    return True


def metric(name: str, value: float = 1, *, kind: str = 'count', **attributes) -> None:
    """A Sentry metric (count / gauge / distribution); a no-op when Sentry is off, never raises.
    Keep attributes low-cardinality (a placement area like 'footer', never a URL or a slug)."""
    try:
        getattr(sentry_sdk.metrics, kind)(name, value, attributes=attributes or None)
    except Exception:
        log.debug('metric %s not sent', name, exc_info=True)
