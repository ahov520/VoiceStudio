"""In-memory pub/sub event bus for real-time UI updates.

Any backend code that mutates sidebar-visible data (projects, profiles,
history) calls ``emit(kind, payload)`` and the WebSocket endpoint fans it
out to all connected frontends.  This replaces the 45 s polling band-aid
with instant push.

Events are fire-and-forget, no persistence needed — the frontend uses
the event as a "hey, refetch this" signal rather than carrying the full
data payload.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger("omnivoice.events")

# All connected WebSocket listener queues
_listeners: list[asyncio.Queue] = []
_lock = asyncio.Lock()
_event_loop: asyncio.AbstractEventLoop | None = None


async def subscribe() -> asyncio.Queue:
    """Register a new listener. Returns a Queue that receives event dicts."""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    global _event_loop
    async with _lock:
        _listeners.append(q)
        _event_loop = asyncio.get_running_loop()
    return q


async def unsubscribe(q: asyncio.Queue) -> None:
    """Remove a listener."""
    global _event_loop
    async with _lock:
        try:
            _listeners.remove(q)
        except ValueError:
            pass
        if not _listeners:
            _event_loop = None


def emit(kind: str, payload: dict[str, Any] | None = None) -> None:
    """Broadcast an event to all connected frontends.

    Safe to call from sync or async context — uses fire-and-forget
    scheduling into the running event loop.

    ``kind`` is one of: projects, profiles, dub_history, export_history,
    generation_history, model_status, glossary.
    """
    # Runtime callers are not type-checked. Keep malformed payloads from
    # turning an optional notification into a failed mutation.
    metadata_dropped = payload is not None and not isinstance(payload, dict)
    event = {
        "kind": kind,
        "ts": time.time(),
        **(payload if isinstance(payload, dict) else {}),
    }
    # Event delivery is a best-effort side channel.  A caller should not lose
    # a successful mutation because an optional diagnostic value (for example
    # a Path or an exception) is not JSON-native.
    if metadata_dropped:
        logger.warning("Event payload must be a mapping; metadata dropped: %s", kind)
        event = {"kind": kind, "ts": event["ts"], "metadata_dropped": True}
    try:
        event_str = json.dumps(event, default=str)
    except (TypeError, ValueError):
        logger.warning("Event payload could not be serialized; metadata dropped: %s", kind)
        event_str = json.dumps({"kind": kind, "ts": event["ts"], "metadata_dropped": True})
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync producers may run in a worker thread. Route delivery back to
        # the loop captured by subscribe() instead of silently dropping it.
        loop = _event_loop
        if loop is None or loop.is_closed():
            logger.debug("No event loop — event dropped: %s", kind)
            return
        try:
            # Create the coroutine inside the callback so a rejected
            # scheduling call does not leave an unawaited coroutine behind.
            loop.call_soon_threadsafe(lambda: loop.create_task(_broadcast(event_str)))
        except RuntimeError:
            # The server can shut its loop down between the closed check and
            # scheduling. Event delivery is best-effort and must not fail the
            # worker operation that produced the event.
            logger.debug("Event loop closed while delivering event: %s", kind)
    else:
        loop.create_task(_broadcast(event_str))


async def _broadcast(event_str: str) -> None:
    """Push event to all listener queues. Drop if full (slow consumer)."""
    async with _lock:
        dead: list[asyncio.Queue] = []
        for q in _listeners:
            try:
                q.put_nowait(event_str)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest, then push. Not a race (#1163):
                # every queue op runs on the single event loop, and there is
                # no await between the QueueFull and this get_nowait/put_nowait
                # pair — no consumer can interleave, so get_nowait cannot raise
                # QueueEmpty here. emit() from a foreign thread drops the event
                # before ever touching a queue (see the RuntimeError branch).
                try:
                    q.get_nowait()
                    q.put_nowait(event_str)
                except Exception:
                    dead.append(q)
        for q in dead:
            try:
                _listeners.remove(q)
            except ValueError:
                pass
