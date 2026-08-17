import asyncio
import json
import threading
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_emit_serializes_non_json_payload_without_failing():
    from core import event_bus

    event_bus._listeners.clear()
    queue = await event_bus.subscribe()
    try:
        event_bus.emit("model_status", {"path": Path("weights.bin")})
        message = await asyncio.wait_for(queue.get(), timeout=1)
        assert json.loads(message)["path"] == "weights.bin"
    finally:
        await event_bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_emit_drops_unserializable_metadata_without_failing(caplog):
    from core import event_bus

    event_bus._listeners.clear()
    queue = await event_bus.subscribe()
    circular = {}
    circular["self"] = circular
    try:
        event_bus.emit("profiles", circular)
        message = await asyncio.wait_for(queue.get(), timeout=1)
        event = json.loads(message)
        assert event["kind"] == "profiles"
        assert event["metadata_dropped"] is True
        assert "metadata dropped" in caplog.text
    finally:
        await event_bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_emit_drops_non_mapping_payload_without_raising(caplog):
    from core import event_bus

    event_bus._listeners.clear()
    queue = await event_bus.subscribe()
    try:
        event_bus.emit("profiles", ["unexpected"])
        event = json.loads(await asyncio.wait_for(queue.get(), timeout=1))
        assert event["kind"] == "profiles"
        assert event["metadata_dropped"] is True
        assert "must be a mapping" in caplog.text
    finally:
        await event_bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_emit_from_worker_thread_reaches_subscriber():
    from core import event_bus

    event_bus._listeners.clear()
    event_bus._event_loop = None
    queue = await event_bus.subscribe()
    try:
        thread = threading.Thread(target=event_bus.emit, args=("model_status", {"ready": True}))
        thread.start()
        thread.join()
        message = await asyncio.wait_for(queue.get(), timeout=1)
        assert json.loads(message)["ready"] is True
    finally:
        await event_bus.unsubscribe(queue)


def test_emit_ignores_loop_shutdown_race():
    from core import event_bus

    class ClosingLoop:
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, callback):
            raise RuntimeError("Event loop is closed")

    event_bus._event_loop = ClosingLoop()
    event_bus.emit("model_status", {"ready": False})
    event_bus._event_loop = None
