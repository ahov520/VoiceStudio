"""PostHog analytics client singleton for OmniVoice Studio.

Provides a stable installation-scoped distinct_id (no user auth in this
app), a lazy-initialized Posthog client, and thin helpers used by routers.
All calls are best-effort — a disabled or uninitialized client is a no-op.
"""
import atexit
import logging
import os
import uuid

logger = logging.getLogger("omnivoice.analytics")

_posthog_client = None


def get_posthog():
    """Return the active Posthog client, or None if disabled/uninitialized."""
    return _posthog_client


def setup_posthog() -> None:
    """Initialize the PostHog client. Called once during lifespan startup."""
    global _posthog_client

    if os.environ.get("POSTHOG_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        logger.info("PostHog analytics disabled (POSTHOG_DISABLED).")
        return

    token = os.environ.get("POSTHOG_PROJECT_TOKEN", "")
    host = os.environ.get("POSTHOG_HOST", "https://eu.i.posthog.com")
    if not token:
        logger.info("PostHog analytics disabled (POSTHOG_PROJECT_TOKEN not set).")
        return

    try:
        from posthog import Posthog
        _posthog_client = Posthog(
            token,
            host=host,
            enable_exception_autocapture=True,
        )
        atexit.register(_posthog_client.shutdown)
        logger.info("PostHog analytics initialized (host=%s).", host)
    except Exception as e:
        logger.warning("PostHog analytics failed to initialize: %s", e)


def teardown_posthog() -> None:
    """Flush all queued events before shutdown. Called in lifespan teardown."""
    global _posthog_client
    if _posthog_client is not None:
        try:
            _posthog_client.shutdown()
        except Exception as e:
            logger.debug("PostHog shutdown error (non-fatal): %s", e)
        _posthog_client = None


def get_installation_id() -> str:
    """Return a stable UUID for this installation.

    Generated once and persisted to prefs.json so every backend restart
    maps to the same PostHog person — even without user authentication.
    """
    from core.prefs import get, set_

    _KEY = "installation_id"
    iid = get(_KEY)
    if not iid:
        iid = str(uuid.uuid4())
        try:
            set_(_KEY, iid)
        except Exception:
            pass
    return iid


def capture(event: str, properties: dict | None = None) -> None:
    """Capture a single event associated with this installation.

    Uses the Posthog instance directly so the distinct_id is always explicit.
    Best-effort — never raises.
    """
    client = get_posthog()
    if client is None:
        return
    try:
        iid = get_installation_id()
        client.capture(event, distinct_id=iid, properties=properties or {})
    except Exception as e:
        logger.debug("PostHog capture error (%s): %s", event, e)
