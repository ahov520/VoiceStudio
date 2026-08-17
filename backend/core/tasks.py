import asyncio
import time
import json
import logging

from core import job_store
from core import failure
from core import run_sentinel

logger = logging.getLogger("omnivoice.tasks")


class TaskManager:
    """In-memory task dispatcher with SQLite-backed metadata.

    The dispatcher itself (queue + worker + listeners) stays in-memory for
    speed, but every state transition and every SSE event is mirrored to
    `jobs` / `job_events`. That means:

      - clients can reconnect via `/tasks/stream/{id}?after_seq=N` and catch up
      - restart recovers: orphaned `running` jobs are flipped to `failed`
      - `GET /jobs` works across restarts
    """

    def __init__(self):
        self.queue = None
        self.active_tasks = {}

    def _init_queue(self):
        if self.queue is None:
            self.queue = asyncio.Queue()

    async def add_task(self, task_id, task_type, func, *args, project_id=None, meta=None, **kwargs):
        self._init_queue()
        task_obj = {
            "status": "pending",
            "type": task_type,
            "created_at": time.time(),
            "history": [],
            "listeners": [],
            "listeners_lock": asyncio.Lock(),
            "error": None,
            "cancelled": False,
        }
        self.active_tasks[task_id] = task_obj
        try:
            job_store.create(task_id, type=task_type, project_id=project_id, meta=meta)
        except Exception:
            logger.exception("job_store.create failed (non-fatal); in-memory task still runs")
        await self.queue.put((task_id, func, args, kwargs))

    def cancel_task(self, task_id):
        task = self.active_tasks.get(task_id)
        if task:
            if task["status"] in {"done", "failed", "cancelled"}:
                return False
            task["cancelled"] = True
            execution_task = task.get("execution_task")
            if execution_task is not None and not execution_task.done():
                execution_task.cancel()
            return True
        return False

    def is_cancelled(self, task_id):
        t = self.active_tasks.get(task_id)
        return t["cancelled"] if t else False

    async def add_listener(self, task_id, q):
        t = self.active_tasks.get(task_id)
        if not t:
            return False
        async with t["listeners_lock"]:
            # A reconnect/race can attempt to attach the same queue twice.
            # Keep listener registration idempotent so every event is
            # delivered once per client.
            if q not in t["listeners"]:
                t["listeners"].append(q)
        return True

    async def remove_listener(self, task_id, q):
        t = self.active_tasks.get(task_id)
        if not t:
            return
        async with t["listeners_lock"]:
            if q in t["listeners"]:
                t["listeners"].remove(q)

    async def _push_event(self, task_id, event_str):
        t = self.active_tasks.get(task_id)
        if t is None:
            return
        if event_str is not None:
            t["history"].append(event_str)
            self._persist_progress_event(task_id, event_str)
            try:
                seq = job_store.append_event(task_id, event_str)
                # Stash the seq on the in-memory copy too, mainly for tests.
                t.setdefault("event_seqs", []).append(seq)
            except Exception:
                # Never let disk writes break the live stream.
                logger.exception("job_store.append_event failed; event delivered to listeners only")
        # Snapshot listeners under lock so concurrent add/remove can't mutate mid-iteration.
        async with t["listeners_lock"]:
            listeners = list(t["listeners"])
        for q in listeners:
            # A slow SSE consumer must not back-pressure the worker. Keep the
            # newest progress frame, dropping the oldest queued frame when a
            # bounded listener is full. (The default stream queue is unbounded.)
            try:
                q.put_nowait(event_str)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event_str)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.debug("Dropping task event for a saturated listener: %s", task_id)

    @staticmethod
    def _persist_progress_event(task_id, event_str):
        """Mirror standard SSE progress payloads into the jobs summary row."""
        try:
            payload = event_str.split("data:", 1)[1].strip()
            event = json.loads(payload)
            if event.get("type") != "progress":
                return
            if "percent" in event:
                fraction = float(event["percent"]) / 100.0
            elif "current" in event and float(event.get("total") or 0) > 0:
                fraction = float(event["current"]) / float(event["total"])
            else:
                fraction = event.get("progress", 0.0)
            stage = event.get("stage")
            if stage is None:
                stage = event.get("text")
            job_store.update_progress(task_id, fraction, stage)
        except (ValueError, TypeError, json.JSONDecodeError, IndexError, KeyError):
            return
        except Exception:
            logger.exception("job progress persistence failed for %s", task_id)

    async def worker(self):
        self._init_queue()
        while True:
            task_id, func, args, kwargs = await self.queue.get()
            t = self.active_tasks.get(task_id)
            if not t:
                self.queue.task_done()
                continue

            if t.get("cancelled"):
                try:
                    await self._finish_cancelled(task_id)
                finally:
                    await self._push_event(task_id, None)
                    self.queue.task_done()
                continue

            t["status"] = "running"
            try:
                job_store.mark_running(task_id)
            except Exception:
                logger.exception("job_store.mark_running failed (non-fatal)")
            # Crash forensics (#1164): note what kind of work just started so
            # an unclean process death (OOM kill mid-dub, …) can be attributed
            # by the next run. Task TYPE only — never user content. The touch
            # is throttled + exception-safe by contract (core.run_sentinel).
            run_sentinel.touch_activity("task", t.get("type"))
            try:
                import inspect
                async def execute():
                    res = func(*args, **kwargs)
                    if inspect.isasyncgen(res):
                        async for update in res:
                            await self._push_event(task_id, update)
                    elif inspect.isawaitable(res):
                        await res

                execution_task = asyncio.create_task(execute())
                t["execution_task"] = execution_task
                await execution_task
                if t.get("cancelled"):
                    await self._finish_cancelled(task_id)
                else:
                    t["status"] = "done"
                    try: job_store.mark_done(task_id)
                    except Exception: logger.exception("job_store.mark_done failed")
            except asyncio.CancelledError:
                if not t.get("cancelled"):
                    raise
                await self._finish_cancelled(task_id)
            except Exception as e:
                logger.exception("Task %s failed", task_id)
                t["status"] = "failed"
                # plan-04 (#131): structured, non-empty failure event instead of
                # a bare str(e) (which is empty/cryptic for many exception types).
                evt = failure.build_failure_event(e, stage="task", context={"task_id": task_id})
                t["error"] = evt["reason"]
                try:
                    job_store.mark_failed(task_id, evt["reason"])
                except Exception:
                    logger.exception("job_store.mark_failed failed")
                try:
                    await self._push_event(task_id, f"data: {json.dumps(evt)}\n\n")
                except Exception as push_err:
                    logger.warning("Failed to push error event for %s: %s", task_id, push_err)
            finally:
                t.pop("execution_task", None)
                await self._push_event(task_id, None)  # EOF
                self.queue.task_done()

    async def _finish_cancelled(self, task_id):
        t = self.active_tasks.get(task_id)
        if t is None or t.get("cancel_notified"):
            return
        t["cancelled"] = True
        t["cancel_notified"] = True
        t["status"] = "cancelled"
        try:
            job_store.mark_cancelled(task_id)
        except Exception:
            logger.exception("job_store.mark_cancelled failed")
        await self._push_event(
            task_id,
            f"data: {json.dumps({'type': 'cancelled'})}\n\n",
        )

task_manager = TaskManager()
