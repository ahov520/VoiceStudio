import asyncio
import json

import pytest


def _stub_job_store(monkeypatch):
    calls = []
    monkeypatch.setattr("core.tasks.job_store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.tasks.job_store.mark_running", lambda task_id: calls.append(("running", task_id)))
    monkeypatch.setattr("core.tasks.job_store.mark_cancelled", lambda task_id: calls.append(("cancelled", task_id)))
    monkeypatch.setattr("core.tasks.job_store.mark_done", lambda task_id: calls.append(("done", task_id)))
    monkeypatch.setattr("core.tasks.job_store.append_event", lambda *args: 1)
    return calls


@pytest.mark.asyncio
async def test_cancelled_pending_task_never_starts(monkeypatch):
    from core.tasks import TaskManager

    calls = _stub_job_store(monkeypatch)
    started = False

    async def work():
        nonlocal started
        started = True

    manager = TaskManager()
    await manager.add_task("pending", "test", work)
    assert manager.cancel_task("pending") is True

    worker = asyncio.create_task(manager.worker())
    await asyncio.wait_for(manager.queue.join(), timeout=1)
    worker.cancel()

    assert started is False
    assert manager.active_tasks["pending"]["status"] == "cancelled"
    assert ("cancelled", "pending") in calls
    assert ("done", "pending") not in calls


@pytest.mark.asyncio
async def test_cancel_running_coroutine_without_stopping_worker(monkeypatch):
    from core.tasks import TaskManager

    calls = _stub_job_store(monkeypatch)
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    manager = TaskManager()
    await manager.add_task("running", "test", work)
    worker = asyncio.create_task(manager.worker())
    await asyncio.wait_for(started.wait(), timeout=1)

    assert manager.cancel_task("running") is True
    await asyncio.wait_for(manager.queue.join(), timeout=1)

    assert cleaned_up.is_set()
    assert worker.done() is False
    assert manager.active_tasks["running"]["status"] == "cancelled"
    assert ("cancelled", "running") in calls
    assert ("done", "running") not in calls
    payloads = manager.active_tasks["running"]["history"]
    assert json.loads(payloads[-1].split("data:", 1)[1])["type"] == "cancelled"
    worker.cancel()


@pytest.mark.asyncio
async def test_terminal_task_cannot_be_cancelled(monkeypatch):
    from core.tasks import TaskManager

    _stub_job_store(monkeypatch)
    manager = TaskManager()
    await manager.add_task("done", "test", lambda: None)
    manager.active_tasks["done"]["status"] = "done"

    assert manager.cancel_task("done") is False


@pytest.mark.asyncio
async def test_full_listener_does_not_block_task(monkeypatch):
    from core.tasks import TaskManager

    _stub_job_store(monkeypatch)
    manager = TaskManager()
    await manager.add_task("busy", "test", lambda: None)
    listener = asyncio.Queue(maxsize=1)
    listener.put_nowait("stale")
    await manager.add_listener("busy", listener)

    await manager._push_event("busy", "data: {\"type\": \"progress\"}\n\n")
    assert listener.get_nowait().startswith("data:")


@pytest.mark.asyncio
async def test_listener_registration_is_idempotent(monkeypatch):
    _stub_job_store(monkeypatch)
    manager = TaskManager()
    await manager.add_task("dedupe", "test", lambda: None)
    listener = asyncio.Queue()

    assert await manager.add_listener("dedupe", listener) is True
    assert await manager.add_listener("dedupe", listener) is True

    await manager._push_event("dedupe", "data: {\"type\": \"progress\"}\n\n")
    assert listener.qsize() == 1
