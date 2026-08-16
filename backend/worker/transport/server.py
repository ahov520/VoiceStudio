"""Control-plane gRPC service.

Translates the wire into scheduler calls and back. The rules it enforces here
are the ones that must hold at the *boundary*, before anything reaches the
domain:

  * authentication — an enrollment token once, then proof of key possession
  * fencing — one active session per worker, newest epoch wins, stale epochs
    dropped rather than merged
  * ordering — persist a result before acknowledging it
  * integrity — an artifact is verified against its declared digest before it
    is renamed into place, and only an explicit last chunk commits one

Everything else is delegated. If this file starts making scheduling decisions,
something has been put in the wrong place.

The control stream runs as two independent loops rather than a single
request/response generator. That is not stylistic: a worker uploading its
status while the server is trying to push an assignment would otherwise
deadlock behind its own reader, and the heartbeats that prove the worker is
alive are exactly what must never queue behind anything else.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Callable, Optional

import grpc

from core.path_security import UnsafePath, resolve_within, safe_filename
from worker import identity, registry, task_store
from worker.errors import ErrorClass, WorkerError
from worker.lifecycle import Attempt, Task
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.scheduler import Scheduler
from worker.transport import codec

logger = logging.getLogger("omnivoice.worker")

PROTOCOL_VERSION = 1
# How far back a peer may be and still be served. Beta ships continuously, so
# skew is the normal case rather than the exception.
MIN_SUPPORTED_VERSION = 1

# Semantic changes that remained additive on the protobuf wire but are not
# safe to ignore. In particular, accepting a clone without task inputs can
# return plausible wrong audio as SUCCESS, so absence is a registration error
# rather than an execution-time fallback.
REQUIRED_FEATURES = frozenset({
    "task_progress_v1",
    "task_inputs_v1",
    "remote_model_download_v1",
    # A generic backend.generate() call accepts the same wire shape but drops
    # profile conditioning controls. Require the canonical worker render path
    # so an older peer cannot successfully return a different voice.
    "remote_tts_render_v1",
})


class ControlPlaneBindError(RuntimeError):
    """The configured control-plane address is already owned."""

# Metadata key carrying the session token when a worker opens its stream.
SESSION_METADATA_KEY = "x-omnivoice-session"

# Bytes above which a result must be uploaded rather than inlined on the
# control stream. Kept well under gRPC's 4 MB default message cap: a large
# payload here head-of-line blocks the heartbeats that prove the worker alive.
INLINE_RESULT_THRESHOLD = 256 * 1024

# Ceilings on what a remote peer may stream into our filesystem. They are not
# derived from anything the worker says: ``ArtifactRef.size_bytes`` narrows the
# cap when it is declared, but can never widen it. A gibibyte is roughly six
# hours of the 24 kHz PCM16 WAV the executor writes — past any single render,
# far short of a disk.
MAX_ARTIFACT_BYTES = 1024**3
# And a budget across every artifact one task delivers, so retries and
# redeliveries cannot walk past the per-artifact cap one upload at a time.
MAX_TASK_ARTIFACT_BYTES = 2 * 1024**3

_HEARTBEAT_INTERVAL_SECONDS = 20
# How often the control plane times a round trip to each worker. Frequent
# enough that the latency shown in the UI is current, rare enough to be free.
_PING_INTERVAL_SECONDS = 5.0
# Read size when serving an input. Two orders of magnitude under the 8 MiB
# message cap, so a large input is many small frames rather than one that the
# receiver refuses outright.
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_REHASH_BLOCK_BYTES = 1024 * 1024


def _upload_refused(
    code: str,
    message: str,
    *,
    bytes_received: int = 0,
    error_class: int = pb.ERROR_CLASS_PROTOCOL,
) -> pb.ResultAck:
    """A terminal ack that commits nothing and says why.

    Refusals are answered rather than aborted: the ack is the only frame this
    RPC ever sends back, so aborting the call would leave the worker knowing
    the upload failed and nothing about whether to retry, resume, or re-render.
    """
    return pb.ResultAck(
        bytes_received=bytes_received,
        committed=False,
        error=pb.Error(error_class=error_class, code=code, message=message),
    )


class _Upload:
    """One in-progress result transfer.

    Bytes land in an attempt-scoped ``.part`` file and are renamed into place
    only once the declared digest matches what actually arrived, so a
    truncated, reordered, or corrupted transfer can never be mistaken for a
    finished result. Every rule here exists because the sender is remote: the
    offset is checked against what we hold rather than trusted as a hint, the
    total is capped whether or not a size was declared, and an iterator that
    simply stops commits nothing.
    """

    def __init__(
        self,
        *,
        attempt: Attempt,
        artifact_id: str,
        final: str,
        limit: int,
        declared_size: int,
        declared_sha256: str,
        on_commit: Callable[[Attempt, int], None],
    ) -> None:
        self.attempt = attempt
        self.artifact_id = artifact_id
        self.final = final
        self.part = f"{final}.part"
        self.limit = limit
        self.declared_size = declared_size
        self.declared_sha256 = declared_sha256.strip().lower()
        self._on_commit = on_commit
        self._digest = hashlib.sha256()
        self._handle = None
        self.received = 0

    def held_bytes(self) -> int:
        try:
            return os.path.getsize(self.part)
        except OSError:
            return 0

    async def start(self, offset: int) -> Optional[pb.ResultAck]:
        """Open the part file at ``offset``, or refuse with what we hold."""
        held = self.held_bytes()
        if offset == 0:
            self._handle = open(self.part, "wb")
            return None
        if offset == held and 0 < held <= self.limit:
            # The digest has to cover the bytes already on disk, or the
            # verification at commit would attest only to the resumed tail —
            # which is exactly the case a resume exists to protect.
            await asyncio.to_thread(self._rehash_held)
            self.received = held
            self._handle = open(self.part, "ab")
            return None
        return _upload_refused(
            "OFFSET_MISMATCH",
            "Resume from the byte count in this ack.",
            bytes_received=held,
            error_class=pb.ERROR_CLASS_TRANSIENT,
        )

    def _rehash_held(self) -> None:
        with open(self.part, "rb") as fh:
            for block in iter(lambda: fh.read(_REHASH_BLOCK_BYTES), b""):
                self._digest.update(block)

    def write(self, chunk) -> Optional[pb.ResultAck]:
        """Append one chunk. Non-None means the transfer is over."""
        if int(chunk.offset) != self.received:
            # Not a resume point: a gap or an overlap inside a live stream is
            # a sender that has lost track of what it sent, and appending it
            # would produce a file that hashes to nothing anybody expected.
            return _upload_refused(
                "OFFSET_MISMATCH",
                "Resume from the byte count in this ack.",
                bytes_received=self.received,
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        data = bytes(chunk.data)
        if self.received + len(data) > self.limit:
            self.discard()
            return _upload_refused(
                "ARTIFACT_TOO_LARGE",
                "This result is larger than the control plane accepts.",
            )
        self._handle.write(data)
        self._digest.update(data)
        self.received += len(data)
        return None

    def commit(self) -> pb.ResultAck:
        """Verify, then rename. Never the other way round."""
        self.close()
        if self.declared_size and self.received != self.declared_size:
            self.discard()
            return _upload_refused(
                "SIZE_MISMATCH",
                "The transfer did not deliver the number of bytes it declared.",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        if self._digest.hexdigest() != self.declared_sha256:
            # Keeping the part file would let the next resume append onto
            # bytes already known to be wrong.
            self.discard()
            return _upload_refused(
                "DIGEST_MISMATCH",
                "The uploaded result does not match its declared sha256.",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        os.replace(self.part, self.final)
        self._on_commit(self.attempt, self.received)
        return pb.ResultAck(
            artifact_id=self.artifact_id, bytes_received=self.received, committed=True
        )

    def incomplete(self) -> pb.ResultAck:
        """The stream ended with no terminal chunk.

        The part file survives for a resume and nothing is renamed. This used
        to return ``committed=True`` over whatever bytes happened to arrive.
        """
        self.close()
        return _upload_refused(
            "UPLOAD_INCOMPLETE",
            "The upload ended before its last chunk; resume from the byte count in this ack.",
            bytes_received=self.received,
            error_class=pb.ERROR_CLASS_TRANSIENT,
        )

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def discard(self) -> None:
        self.close()
        try:
            os.remove(self.part)
        except OSError:
            pass


class _Session:
    """Server-side view of one connected worker's stream."""

    def __init__(self, worker_id: str, epoch: int, session: identity.Session) -> None:
        self.worker_id = worker_id
        self.epoch = epoch
        self.session = session
        self.outbox: asyncio.Queue[pb.ServerMessage] = asyncio.Queue()
        self.stream_open = False
        # Set only in inbound mode, where artifacts move over RPCs this side
        # initiates. None means outbound, where the worker calls UploadResult
        # and DownloadArtifact itself and there is nothing to hold here.
        self.connection = None
        # nonce → monotonic send time, for the outstanding ping.
        self.pending_pings: dict[int, float] = {}
        self.next_nonce = 1

    async def send(self, message: pb.ServerMessage) -> None:
        await self.outbox.put(message)


class WorkerServicer(pb_grpc.WorkerServiceServicer):
    """Implements ``WorkerService`` on top of the scheduler and registry."""

    def __init__(
        self,
        scheduler: Scheduler,
        pool: WorkerPool,
        *,
        artifact_dir: str,
        cert_fingerprint: str = "",
    ) -> None:
        self.scheduler = scheduler
        self.pool = pool
        self.artifact_dir = artifact_dir
        self.cert_fingerprint = cert_fingerprint
        self._sessions: dict[str, _Session] = {}
        self._by_token: dict[str, _Session] = {}
        # task_id → attempt_id → committed artifact bytes. Per attempt rather
        # than a running total, so a redelivered upload of the same attempt
        # replaces its own entry instead of spending the task's budget twice.
        self._artifact_bytes: dict[str, dict[str, int]] = {}
        os.makedirs(artifact_dir, exist_ok=True)

    # ── Registration ──────────────────────────────────────────────────────

    async def Register(self, request: pb.RegisterRequest, context) -> pb.RegisterResponse:
        if request.protocol_version_max < MIN_SUPPORTED_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker speaks an older protocol than the control plane supports. "
                "Update OmniVoice on the worker machine, then reconnect.",
            )
        if request.protocol_version_min > PROTOCOL_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker is newer than the control plane. Update OmniVoice on this "
                "machine, then reconnect.",
            )
        missing_features = sorted(REQUIRED_FEATURES.difference(request.features))
        if missing_features:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker is missing required protocol features "
                f"({', '.join(missing_features)}). Update VoiceStudio on the worker "
                "machine, then reconnect; no task was run.",
            )

        worker = self._authenticate(request)
        if worker is None:
            # Deliberately one message for every failure mode: unknown key,
            # revoked worker, bad signature, spent token. Distinguishing them
            # tells an attacker which half of the guess was right.
            return self._refuse(
                "AUTH_FAILED",
                "This worker could not be authenticated. Generate a new enrollment "
                "token in Settings → System → Remote workers and add the worker again.",
            )

        # The address the worker actually reached us from — what the UI shows
        # as ip:port. Self-reported endpoints would be guesses; this is fact.
        return self.establish_session(worker, request, address=_peer_address(context))

    def establish_session(
        self, worker: registry.RemoteWorker, request: pb.RegisterRequest, *, address: str
    ) -> pb.RegisterResponse:
        """Everything registration does once the worker is known to be genuine.

        Split out because inbound mode (NodeService.Attach) reaches this point
        by a different road — the panel dialled, and admission was an API key
        rather than an enrollment token — but must arrive in exactly the same
        state. A second copy of session issue, capability application and
        in-flight reconciliation is a second thing to keep in step forever, and
        the half that gets forgotten is always the reconciliation.
        """
        epoch = registry.begin_session(worker.id)
        session = identity.issue_session(worker_id=worker.id, key_id=worker.key_id, epoch=epoch)
        capabilities = [codec.capability_from_pb(c) for c in request.capabilities]
        host = codec.host_from_pb(request.host)
        registry.update_capabilities(
            worker.id,
            capabilities=capabilities,
            host=host,
            max_concurrent_tasks=request.max_concurrent_tasks or 1,
        )
        worker = registry.get(worker.id) or worker

        backend = host["gpus"][0].get("backend", "") if host.get("gpus") else ""
        claimed = {ref.attempt_id for ref in request.in_flight}
        # A finished result the worker never had acknowledged is work it is
        # still holding the only copy of. Reconciliation writes off anything
        # the worker does not claim (lifecycle.reconcile), so leaving these out
        # marks a completed render LOST moments before it is redelivered.
        unacked = {ref.attempt_id for ref in request.completed_unacked}

        # Replace any previous session for this worker. Two live sessions is
        # the race that delivers two accepts for one assignment.
        previous = self._sessions.pop(worker.id, None)
        if previous is not None:
            self._by_token.pop(previous.session.token, None)

        self.pool.connect(
            worker,
            session=session,
            epoch=epoch,
            max_concurrent_tasks=request.max_concurrent_tasks or 1,
            backend=backend,
            in_flight=claimed,
            address=address,
        )
        self.pool.apply_capabilities(worker.id, capabilities)
        live = _Session(worker.id, epoch, session)
        self._sessions[worker.id] = live
        self._by_token[session.token] = live

        # Reconcile before any new work is dispatched: the worker may be
        # holding tasks this control plane forgot across a restart. Unacked
        # results count as held here but not as occupied slots above — the work
        # is done, only its delivery is outstanding.
        self.scheduler.on_reconnected(worker.id, in_flight=claimed | unacked)

        logger.info("Worker %s registered on epoch %d", worker.name, epoch)
        return pb.RegisterResponse(
            worker_id=worker.id,
            session_token=session.token,
            session_epoch=epoch,
            protocol_version=PROTOCOL_VERSION,
            session_expires_at_unix=int(session.expires_at),
            heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
            authoritative_in_flight=self._authoritative_refs(worker.id),
        )

    def _authenticate(self, request: pb.RegisterRequest) -> Optional[registry.RemoteWorker]:
        public_key = bytes(request.public_key)
        if len(public_key) != 32:
            return None
        key_id = identity.key_id_for(public_key)

        if request.enrollment_token:
            # First contact: spend the join token, then bind this key to it.
            try:
                token = identity.EnrollmentToken.decode(request.enrollment_token)
            except ValueError:
                return None
            if token.expired():
                return None
            if registry.is_revoked(key_id):
                return None
            existing = registry.get_by_key_id(key_id)
            if not registry.redeem_enrollment(token, worker_id=existing.id if existing else key_id):
                return None
            return registry.enroll_worker(
                name=request.host.hostname or key_id,
                public_key=public_key,
                consent_granted=True,
            )

        if registry.is_revoked(key_id):
            return None
        return registry.authenticate(
            key_id=key_id,
            public_key=public_key,
            challenge=bytes(request.challenge),
            signature=bytes(request.challenge_signature),
            nonce=bytes(request.nonce),
            session_epoch=request.envelope.sequence,
        )

    def _authoritative_refs(self, worker_id: str) -> list[pb.TaskRef]:
        """What this control plane believes the worker is running.

        Anything the worker holds that is not in this list is a zombie it must
        stop, which is the other half of reconciliation.
        """
        refs = []
        for task in self.scheduler.tasks_for_worker(worker_id):
            attempt = task.active_attempt
            if attempt is not None:
                refs.append(codec.ref_for(attempt))
        return refs

    @staticmethod
    def _refuse(code: str, message: str) -> pb.RegisterResponse:
        return pb.RegisterResponse(
            error=pb.Error(error_class=pb.ERROR_CLASS_PROTOCOL, code=code, message=message)
        )

    # ── Control stream ────────────────────────────────────────────────────

    async def Control(self, request_iterator, context) -> None:
        """Bidirectional control stream.

        A coroutine (not an async generator) so that reads and writes can run
        as independent tasks: outbound assignments must not wait on an inbound
        message, and heartbeats must not queue behind an outbound one.
        """
        session = self._session_from_metadata(context)
        if session is None:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Register before opening a control stream.",
            )
            return
        if session.stream_open:
            await context.abort(
                grpc.StatusCode.ALREADY_EXISTS, "This session already has an open stream."
            )
            return

        from worker.executor import INLINE_LIMIT_BYTES  # noqa: PLC0415

        worker = self.pool.get(session.worker_id)
        if worker is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "This worker is no longer connected; register again.",
            )
            return
        reader = writer = pinger = None
        session.stream_open = True
        try:
            await session.send(
                pb.ServerMessage(
                    config=pb.ConfigUpdate(
                        heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                        max_concurrent_tasks=max(
                            1, worker.capacity.max_concurrent_tasks
                        ),
                        inline_result_threshold_bytes=INLINE_LIMIT_BYTES,
                    )
                )
            )
            writer = asyncio.create_task(self._write_loop(session, context))
            reader = asyncio.create_task(self._read_loop(session, request_iterator))
            pinger = asyncio.create_task(self._ping_loop(session))
            done, pending = await asyncio.wait(
                {reader, writer, pinger}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    logger.debug("Control stream ended for %s: %s", session.worker_id, exc)
        finally:
            session.stream_open = False
            for task in (reader, writer, pinger):
                if task is None:
                    continue
                task.cancel()
            # A dropped stream starts grace windows; it fails nothing. The
            # worker may be seconds away from delivering a finished result.
            self.scheduler.on_disconnected(session.worker_id)
            self._sessions.pop(session.worker_id, None)
            self._by_token.pop(session.session.token, None)
            logger.info("Worker %s disconnected", session.worker_id)

    # ── Inbound mode ──────────────────────────────────────────────────────
    #
    # The panel dialled the node instead of the other way round. Admission was
    # an API key rather than an enrollment token, and the frames arrive on a
    # client stream rather than a servicer context — but this is still the
    # control plane, so everything between those two edges is the same code.

    def session_for(self, worker_id: str) -> Optional[_Session]:
        return self._sessions.get(worker_id)

    def register_inbound(
        self, worker: registry.RemoteWorker, request: pb.RegisterRequest, *, address: str
    ) -> pb.RegisterResponse:
        """Register a node this panel dialled.

        The version and feature gates run here too. Skipping them for inbound
        would let an out-of-date node register cleanly and then ignore task
        inputs — the failure that returned a clone with no reference audio,
        reported as success.
        """
        if request.protocol_version_max < MIN_SUPPORTED_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine speaks an older protocol than this app supports. "
                "Update VoiceStudio there, then reconnect.",
            )
        if request.protocol_version_min > PROTOCOL_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine is newer than this app. Update VoiceStudio here, "
                "then reconnect.",
            )
        missing_features = sorted(REQUIRED_FEATURES.difference(request.features))
        if missing_features:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine is missing required protocol features "
                f"({', '.join(missing_features)}). Update VoiceStudio there, then "
                "reconnect; no task was run.",
            )
        return self.establish_session(worker, request, address=address)

    async def run_inbound_stream(self, session: _Session, frames, connection) -> None:
        """Drive one dialled session until it ends.

        Mirrors ``Control``'s task set minus the writer: outbound writes to a
        servicer context, while here the connector drains the same outbox onto
        its request generator. The teardown is deliberately identical — a
        dropped stream starts grace windows and fails nothing, because the node
        may be seconds away from delivering a finished result.
        """
        worker = self.pool.get(session.worker_id)
        if worker is None:
            return
        from worker.executor import INLINE_LIMIT_BYTES  # noqa: PLC0415

        reader = pinger = None
        try:
            session.stream_open = True
            session.connection = connection
            await session.send(
                pb.ServerMessage(
                    config=pb.ConfigUpdate(
                        heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                        max_concurrent_tasks=max(
                            1, worker.capacity.max_concurrent_tasks
                        ),
                        inline_result_threshold_bytes=INLINE_LIMIT_BYTES,
                    )
                )
            )
            reader = asyncio.create_task(self._read_loop(session, frames))
            pinger = asyncio.create_task(self._ping_loop(session))
            done, pending = await asyncio.wait(
                {reader, pinger}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    logger.debug("Inbound stream ended for %s: %s", session.worker_id, exc)
        finally:
            session.stream_open = False
            session.connection = None
            for task in (reader, pinger):
                if task is not None:
                    task.cancel()
            self.scheduler.on_disconnected(session.worker_id)
            self._sessions.pop(session.worker_id, None)
            self._by_token.pop(session.session.token, None)
            logger.info("GPU machine %s disconnected", session.worker_id)

    def _session_from_metadata(self, context) -> Optional[_Session]:
        for key, value in context.invocation_metadata() or ():
            if key.lower() == SESSION_METADATA_KEY:
                session = self._by_token.get(value)
                if session is not None and not session.session.expired():
                    return session
                return None
        return None

    async def _read_loop(self, session: _Session, request_iterator) -> None:
        async for message in request_iterator:
            try:
                await self._handle(session, message)
            except Exception:
                # One unusable frame is not a broken session. A late or
                # out-of-order message raises LifecycleError from the domain,
                # and letting that end the reader would win the asyncio.wait in
                # Control() and disconnect a worker that is mid-render.
                logger.warning(
                    "Dropping unusable %s frame from worker %s",
                    message.WhichOneof("payload"),
                    session.worker_id,
                    exc_info=True,
                )

    async def _ping_loop(self, session: _Session) -> None:
        """Time a round trip periodically so the UI can show real latency."""
        while True:
            await asyncio.sleep(_PING_INTERVAL_SECONDS)
            nonce = session.next_nonce
            session.next_nonce += 1
            # Monotonic: a wall-clock jump (NTP, sleep/wake) must not turn into
            # a nonsense latency reading.
            session.pending_pings[nonce] = time.monotonic()
            # Never let unanswered pings accumulate on a wedged worker.
            if len(session.pending_pings) > 20:
                for stale in sorted(session.pending_pings)[:-5]:
                    session.pending_pings.pop(stale, None)
            await session.send(pb.ServerMessage(ping=pb.Ping(nonce=nonce)))

    async def _write_loop(self, session: _Session, context) -> None:
        while True:
            message = await session.outbox.get()
            await context.write(message)

    async def _handle(self, session: _Session, message: pb.WorkerMessage) -> None:
        kind = message.WhichOneof("payload")
        if kind is None:
            return

        if kind == "heartbeat":
            beat = message.heartbeat
            self.pool.heartbeat(
                session.worker_id,
                active_tasks=beat.active_tasks,
                available_slots=beat.available_slots,
                resident_models=set(beat.resident_models),
                free_memory_bytes=beat.free_memory_bytes,
            )
            registry.touch(session.worker_id)
            return

        if kind == "capabilities":
            caps = [codec.capability_from_pb(c) for c in message.capabilities.capabilities]
            registry.update_capabilities(session.worker_id, capabilities=caps)
            self.pool.apply_capabilities(session.worker_id, caps)
            return

        if kind == "download_progress":
            try:
                event = json.loads(message.download_progress.event_json)
                if not isinstance(event, dict):
                    raise ValueError("progress event is not an object")
                # The authenticated session, never the worker payload, is the
                # authoritative target identity.
                event["target"] = session.worker_id
                from utils import hf_progress  # noqa: PLC0415

                hf_progress.emit(event)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Worker %s sent malformed download progress", session.worker_id)
            return

        if kind == "goodbye":
            # A clean shutdown is a drain, not a failure.
            worker = self.pool.get(session.worker_id)
            if worker is not None:
                worker.draining = True
            return

        if kind == "pong":
            sent_at = session.pending_pings.pop(message.pong.nonce, None)
            if sent_at is not None:
                self.pool.record_latency(
                    session.worker_id, (time.monotonic() - sent_at) * 1000.0
                )
            return

        if kind == "cancel_ack":
            ref = message.cancel_ack.ref
            if self._owns(session, ref):
                self.scheduler.on_cancel_ack(
                    ref.task_id, ref.attempt_id, epoch=ref.session_epoch
                )
            return

        if kind == "result":
            # Deliberately ahead of the epoch fence. A result is a statement
            # about a *past* epoch by construction — the work was assigned in
            # the session the reconnect just replaced — so fencing it on the
            # live epoch drops finished renders. Ownership is checked against
            # the attempt's recorded epoch instead, inside _on_result.
            await self._on_result(session, message.result)
            return

        ref = getattr(message, kind).ref
        if not self._owns(session, ref):
            return

        if kind == "accepted":
            self.scheduler.on_accepted(ref.task_id, ref.attempt_id, epoch=ref.session_epoch)
        elif kind == "rejected":
            error = codec.error_from_pb(message.rejected.error) or WorkerError(
                error_class=ErrorClass.CAPACITY,
                code="WORKER_AT_CAPACITY",
                message="The worker declined the task.",
            )
            self.scheduler.on_failed(ref.task_id, ref.attempt_id, error, epoch=ref.session_epoch)
        elif kind == "model_loading":
            self.scheduler.on_model_loading(
                ref.task_id,
                ref.attempt_id,
                progress=message.model_loading.progress,
                detail=message.model_loading.detail,
                epoch=ref.session_epoch,
            )
        elif kind == "started":
            self.scheduler.on_started(ref.task_id, ref.attempt_id, epoch=ref.session_epoch)
        elif kind == "progress":
            # The lease arithmetic lives in the scheduler, which owns the
            # phase budgets; the transport only reports what arrived. A
            # keepalive frame renews without claiming any work was done.
            self.scheduler.on_progress(
                ref.task_id,
                ref.attempt_id,
                progress=message.progress.progress,
                stage=message.progress.stage,
                keepalive=message.progress.keepalive,
                epoch=ref.session_epoch,
            )
        elif kind == "failed":
            error = codec.error_from_pb(message.failed.error) or WorkerError(
                error_class=ErrorClass.TRANSIENT,
                code="WORKER_FAILED",
                message="The worker reported a failure with no detail.",
            )
            self.scheduler.on_failed(ref.task_id, ref.attempt_id, error, epoch=ref.session_epoch)

    def _owns(self, session: _Session, ref: pb.TaskRef) -> bool:
        """May this session speak for the attempt the frame names?

        Ownership, deliberately not an epoch comparison. ``ref.session_epoch``
        is stamped once at dispatch and echoed verbatim by the worker for the
        life of the task, while ``registry.begin_session`` bumps the session
        epoch on every reconnect. Fencing task frames against the *live* epoch
        therefore discarded every liveness frame from a worker that dropped and
        resumed — so the control plane expired a task whose GPU was still
        rendering it, and swallowed the failure report when it went wrong.

        Staleness is still fenced, one layer down and per attempt:
        ``Scheduler._fenced`` compares the frame's epoch against the epoch the
        *attempt* was assigned under, which is the question that actually
        matters. What only this layer can check is that the session on the
        stream is the worker the attempt was handed to.
        """
        attempt, foreign = self._attempt_and_owner(session, ref)
        if foreign:
            # Not a routine race: unguessable ids and no listing RPC mean a
            # worker should never see another's attempt id.
            logger.warning(
                "Worker %s sent a frame for an attempt owned by another worker; dropping",
                session.worker_id,
            )
            return False
        if attempt is None:
            logger.debug("Dropping frame for unknown attempt on task %s", ref.task_id)
            return False
        return True

    async def _on_result(self, session: _Session, result: pb.TaskResult) -> None:
        """Commit, then acknowledge — never the other way round.

        The acknowledgement is the worker's licence to forget a finished
        render, so it is sent only once this control plane holds a durable
        verdict. Acking a frame we could not place — an attempt we have no
        record of, a task still being restored — silently destroys the only
        copy of work that succeeded.
        """
        ref = result.ref
        attempt, foreign = self._attempt_and_owner(session, ref)
        if foreign:
            # Committing here would mark the task done with no artifact, and
            # the owning worker's real delivery would then arrive as a
            # duplicate and be discarded — losing the render this whole
            # redelivery path exists to protect. No ack either: nothing was
            # placed, so nothing has earned the licence to forget.
            logger.warning(
                "Worker %s reported a result for an attempt owned by another worker; dropping",
                session.worker_id,
            )
            return
        payload = None
        if result.result_json:
            try:
                payload = json.loads(result.result_json)
            except ValueError:
                payload = {"raw": result.result_json}

        artifact = None
        if result.artifacts:
            if session.connection is not None:
                # Inbound: the node cannot call us, so a result it "delivered"
                # is only staged on its own disk until we pull it. Without this
                # the task commits with an artifact path that was never
                # written, and the job fails with "finished the job but its
                # audio did not arrive" — which is exactly what it did on
                # hardware before this existed.
                artifact = await self._fetch_inbound_artifact(
                    session, attempt, result.artifacts[0]
                )
            else:
                artifact = self._contained_artifact(result.artifacts[0].artifact_id)
        # No attempt record, no place to put it: the payload of a task we
        # cannot identify has nothing to be attached to, and the worker keeps
        # its copy because nothing below will acknowledge it.
        if result.inline_payload and attempt is not None:
            artifact = self._store_inline(attempt, bytes(result.inline_payload))

        # Returns only after the commit is durable, which is what makes the
        # acknowledgement below safe to send. The epoch on the wire is the one
        # the attempt was assigned under, and that is what the scheduler
        # compares against — not whichever session happens to be live now.
        committed, task = self.scheduler.on_result(
            ref.task_id,
            ref.attempt_id,
            result_ref=artifact,
            result=payload,
            epoch=ref.session_epoch,
        )
        if self._settled(committed, task, ref.task_id):
            await session.send(pb.ServerMessage(result_ack=pb.ResultAckMessage(ref=ref)))

    def _settled(self, committed: bool, task: Optional[Task], task_id: str) -> bool:
        """May the worker drop its copy of this result?

        Only against a durable verdict: this commit, an earlier one that won
        the race, or — after a restart that never reloaded the task — the fact
        of completion on disk. Anything else is redelivered, which costs one
        frame per reconnect and is the only thing standing between a dropped
        message and a lost render.
        """
        if committed:
            return True
        if task is not None:
            return task.state.terminal
        try:
            return task_store.is_committed(task_id)
        except Exception:
            logger.debug("Could not check the committed state of %s", task_id, exc_info=True)
            return False

    def _attempt_for(self, session: _Session, ref) -> Optional[Attempt]:
        """This control plane's own record of the attempt a frame names.

        Every artifact path is minted from what this returns rather than from
        the frame, because the ids on the wire are remote input: ``os.path.join``
        silently discards its prefix the moment one of them is absolute.
        """
        attempt, _foreign = self._attempt_and_owner(session, ref)
        return attempt

    def _attempt_and_owner(self, session: _Session, ref) -> tuple[Optional[Attempt], bool]:
        """``(attempt, foreign)`` — the attempt, and whether another worker owns it.

        The two None cases must not be collapsed. "No record" is ordinary and
        recoverable: a task not yet restored after a restart still has a
        durable verdict on disk, so a result naming it is redelivered rather
        than lost. "Another worker's attempt" is neither — accepting it lets a
        frame from the wrong worker commit the task, after which the owning
        worker's real delivery arrives as a duplicate and its audio is
        discarded. Returning one None for both is how that got through.
        """
        task = self.scheduler.get(ref.task_id)
        if task is None:
            return None, False
        attempt = task.get_attempt(ref.attempt_id)
        if attempt is None:
            return None, False
        if attempt.worker_id != session.worker_id:
            return None, True
        return attempt, False

    def _artifact_path(self, task_id: str, attempt_id: str) -> Optional[str]:
        """Attempt-scoped storage for one result.

        Attempt-scoped, not task-scoped: two attempts of one task must never
        share a path, or a superseded straggler overwrites the result that won.
        """
        try:
            relative = os.path.join(safe_filename(task_id), f"{safe_filename(attempt_id)}.bin")
            path = resolve_within(self.artifact_dir, relative)
        except UnsafePath:
            logger.warning("Refusing to store a result outside the artifact directory")
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def _fetch_inbound_artifact(
        self, session: _Session, attempt: Optional[Attempt], ref: pb.ArtifactRef
    ) -> Optional[str]:
        """Pull a staged result down from a node this control plane dialled.

        Returns the local path, or None — and None is not a silent loss: the
        commit below records no artifact, the task fails with a message naming
        the machine, and the node keeps its copy because nothing acknowledges
        a result we could not fetch.
        """
        if attempt is None:
            return None
        path = self._artifact_path(attempt.task_id, attempt.attempt_id)
        if path is None:
            return None
        declared = int(ref.size_bytes)
        prior = self._artifact_bytes.get(attempt.task_id, {}).get(
            attempt.attempt_id, 0
        )
        spent = max(0, self._artifact_bytes_spent(attempt.task_id) - prior)
        remaining = MAX_TASK_ARTIFACT_BYTES - spent
        if declared > MAX_ARTIFACT_BYTES or declared > remaining or remaining <= 0:
            logger.warning(
                "Refusing an oversized result for task %s from %s",
                attempt.task_id,
                session.worker_id,
            )
            return None
        limit = min(MAX_ARTIFACT_BYTES, remaining)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await session.connection.fetch_result(ref, path, max_bytes=limit)
            received = os.path.getsize(path)
            if declared and received != declared:
                raise RuntimeError(
                    f"the result contained {received} bytes, expected {declared}"
                )
        except Exception as exc:
            try:
                os.remove(path)
            except OSError:
                pass
            logger.warning(
                "Could not fetch the result for task %s from %s: %s",
                attempt.task_id,
                session.worker_id,
                exc,
            )
            return None
        self._record_artifact_bytes(attempt, received)
        return path

    def _contained_artifact(self, artifact_id: str) -> Optional[str]:
        """An artifact the worker names is only ever a reference into our own
        store, and is resolved as one."""
        if not artifact_id:
            return None
        try:
            return str(resolve_within(self.artifact_dir, artifact_id))
        except UnsafePath:
            logger.warning("Refusing an artifact reference outside the artifact directory")
            return None

    def _store_inline(self, attempt: Attempt, payload: bytes) -> Optional[str]:
        """Write a small inline result to attempt-scoped storage."""
        path = self._artifact_path(attempt.task_id, attempt.attempt_id)
        if path is None:
            return None
        with open(path, "wb") as fh:
            fh.write(payload)
        return path

    # ── Dispatch out ──────────────────────────────────────────────────────

    async def dispatch(self, assignment) -> bool:
        """Send an assignment to its worker. False if the stream is gone."""
        session = self._sessions.get(assignment.worker.worker_id)
        if session is None:
            return False
        message = codec.assignment_to_pb(
            assignment.task,
            assignment.attempt,
            assignment.deadlines,
            artifact_root=self.artifact_dir,
        )
        if session.connection is not None and message.inputs:
            # Inbound: the node cannot pull, so its inputs have to be here
            # BEFORE the assignment is. The executor asks for them as soon as
            # it starts, and an assignment that overtakes its own reference
            # audio fails on a file that is merely late.
            try:
                await self._push_inbound_inputs(session, message)
            except Exception as exc:
                logger.warning(
                    "Could not send task inputs to %s: %s", session.worker_id, exc
                )
                return False
        await session.send(pb.ServerMessage(assignment=message))
        return True

    async def _push_inbound_inputs(self, session: _Session, message) -> None:
        """Upload every declared input, replacing each ref with what landed."""
        pushed = []
        for ref in message.inputs:
            local = self._contained_artifact(ref.artifact_id)
            if local is None:
                raise ValueError("task input is not inside the artifact directory")
            pushed.append(await session.connection.push_input(ref, local))
        del message.inputs[:]
        message.inputs.extend(pushed)

    async def cancel(self, worker_id: str, task_id: str, attempt_id: str, epoch: int) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        await session.send(
            pb.ServerMessage(cancel=pb.TaskCancel(ref=codec.task_ref(task_id, attempt_id, epoch)))
        )
        return True

    async def drain(self, worker_id: str, *, deadline_seconds: int = 300) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        worker = self.pool.get(worker_id)
        if worker is not None:
            worker.draining = True
        await session.send(
            pb.ServerMessage(drain=pb.Drain(deadline_seconds=deadline_seconds))
        )
        return True

    async def prewarm(
        self, worker_id: str, *, engine: str, model_id: str = "", download_if_missing: bool = False
    ) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        await session.send(pb.ServerMessage(prewarm=pb.PrewarmRequest(
            engine=engine, model_id=model_id, download_if_missing=download_if_missing,
        )))
        return True

    # ── Artifact transfer ─────────────────────────────────────────────────

    async def UploadResult(self, request_iterator, context) -> pb.ResultAck:
        """Receive a result artifact in chunks, resumably.

        Nothing the sender says is taken on trust. Every chunk must name the
        exact offset this control plane already holds, the total is bounded per
        artifact and per task, the digest declared in ``ArtifactRef.sha256``
        must match the bytes that arrived, and only an explicit ``last`` chunk
        renames the ``.part`` file into place. An iterator that simply stops
        leaves the partial file for a resume and commits nothing — it used to
        commit, which is how a truncated transfer became a finished render.

        Resume is real, and this is where it is reported. The call is
        client-streaming with a single terminal ack, so there is no mid-stream
        channel for "bytes already held": a chunk whose offset disagrees with
        what we hold is answered with ``committed=False`` and
        ``bytes_received`` set to the authoritative held count, and the worker
        restarts from there. That ack is the bytes-held probe the proto
        promised and no RPC provided.
        """
        upload: Optional[_Upload] = None
        try:
            async for chunk in request_iterator:
                if upload is None:
                    upload, refusal = await self._open_upload(context, chunk)
                    if upload is None:
                        if refusal is None:
                            await context.abort(
                                grpc.StatusCode.UNAUTHENTICATED,
                                "Unknown or expired session.",
                            )
                            return _upload_refused(
                                "UNAUTHENTICATED", "Unknown or expired session."
                            )
                        return refusal
                refusal = upload.write(chunk)
                if refusal is not None:
                    return refusal
                self._renew_upload_lease(upload.attempt)
                if chunk.last:
                    return upload.commit()
        finally:
            if upload is not None:
                upload.close()
        if upload is None:
            return _upload_refused("EMPTY_UPLOAD", "The upload carried no chunks.")
        return upload.incomplete()

    async def _open_upload(self, context, chunk) -> tuple[Optional[_Upload], Optional[pb.ResultAck]]:
        """Authorise the first chunk and open its destination.

        ``(None, None)`` means unauthenticated — the one failure answered with
        a gRPC abort rather than an ack, because a caller we cannot identify
        has no business being told anything about the task it named.
        """
        ref = chunk.ref
        session = self._session_for(context, ref) or self._session_for(context, chunk)
        if session is None:
            return None, None
        # Same rule as an inline result: the destination is minted from our own
        # attempt record, never assembled from the ids in the request.
        attempt = self._attempt_for(session, ref)
        final = (
            self._artifact_path(attempt.task_id, attempt.attempt_id)
            if attempt is not None
            else None
        )
        if attempt is None or final is None:
            return None, _upload_refused(
                "UNKNOWN_ATTEMPT", "No such attempt is running for this worker."
            )
        if not ref.sha256:
            # Refused before a single byte is accepted. An upload with no
            # declared digest cannot be verified, and committing it would make
            # the whole verification path decorative.
            return None, _upload_refused(
                "DIGEST_REQUIRED", "Declare ArtifactRef.sha256 before uploading a result."
            )
        declared = int(ref.size_bytes)
        if declared > MAX_ARTIFACT_BYTES:
            return None, _upload_refused(
                "ARTIFACT_TOO_LARGE", "This result is larger than the control plane accepts."
            )
        # A declared size narrows the cap; an undeclared one gets the ceiling.
        limit = declared or MAX_ARTIFACT_BYTES
        if self._artifact_bytes_spent(attempt.task_id) + limit > MAX_TASK_ARTIFACT_BYTES:
            return None, _upload_refused(
                "TASK_BUDGET_EXCEEDED",
                "This task has delivered as many artifact bytes as it is allowed.",
            )
        # Last, and only once the request is known to be one we would accept:
        # a refusal must not leave a task parked in RESULT_UPLOADING with no
        # transfer under way. Cancelled, timed out, or already committed by
        # another attempt all fail here, because accepting these bytes would
        # overwrite the artifact of whichever attempt actually won.
        if not self._begin_uploading(attempt):
            return None, _upload_refused(
                "ATTEMPT_NOT_LIVE", "This attempt is no longer accepting a result."
            )
        upload = _Upload(
            attempt=attempt,
            artifact_id=self._artifact_id_for(final),
            final=final,
            limit=limit,
            declared_size=declared,
            declared_sha256=ref.sha256,
            on_commit=self._record_artifact_bytes,
        )
        refusal = await upload.start(int(chunk.offset))
        if refusal is not None:
            upload.close()
            return None, refusal
        return upload, None

    def _begin_uploading(self, attempt: Attempt) -> bool:
        """Put the task into RESULT_UPLOADING for the length of the transfer.

        Without this transition ``Task.uploading`` has no callers, so
        RESULT_UPLOADING is unreachable and the entire delivery of a large
        result runs under the 120 s progress lease while the 900 s
        ``result_delivery_seconds`` budget sits unused because nothing ever
        entered the state it applies to.
        """
        task = self.scheduler.get(attempt.task_id)
        if task is None or task.state.terminal or attempt.state.terminal:
            return False
        try:
            task.uploading(attempt.attempt_id, session_epoch=attempt.session_epoch)
        except Exception:
            logger.debug(
                "Refusing an upload for attempt %s: not in a state that can deliver",
                attempt.attempt_id,
                exc_info=True,
            )
            return False
        self._renew_upload_lease(attempt)
        return True

    def _renew_upload_lease(self, attempt: Attempt) -> None:
        """Renew the lease from upload progress, under the delivery budget.

        Routed through the scheduler rather than computed here: it owns the
        phase budgets, and ``on_progress(keepalive=True)`` already caps a
        renewal at the current phase's ceiling — which, now that the task is in
        RESULT_UPLOADING, is ``result_delivery_seconds``. A keepalive and not a
        progress frame: bytes on the wire prove the worker is alive, not that
        the render advanced, and overwriting a finished 100% with a transfer's
        zero is a UI that goes backwards.
        """
        self.scheduler.on_progress(
            attempt.task_id,
            attempt.attempt_id,
            progress=0.0,
            keepalive=True,
            epoch=attempt.session_epoch,
        )

    def _artifact_bytes_spent(self, task_id: str) -> int:
        """How much of this task's artifact budget is already committed."""
        for known in list(self._artifact_bytes):
            task = self.scheduler.get(known)
            if task is None or task.state.terminal:
                self._artifact_bytes.pop(known, None)
        return sum(self._artifact_bytes.get(task_id, {}).values())

    def _record_artifact_bytes(self, attempt: Attempt, count: int) -> None:
        self._artifact_bytes.setdefault(attempt.task_id, {})[attempt.attempt_id] = count

    def _artifact_id_for(self, path: str) -> str:
        """The store-relative id a worker may name this artifact by.

        Relative, not the absolute path it lives at: the id travels back on the
        control stream as ``TaskResult.artifacts[0].artifact_id`` and is
        re-resolved against the artifact directory, and handing a remote peer
        our filesystem layout buys nothing that resolution does not already do.
        """
        try:
            return os.path.relpath(path, self.artifact_dir)
        except ValueError:  # different drive on Windows; cannot happen, but
            return path

    async def DownloadArtifact(self, request: pb.ArtifactRef, context):
        """Stream a task input (reference audio, source video) to a worker.

        Bound to the attempt that needs the input, not merely to a live
        session. Until artifacts started flowing inwards, ``inputs`` carried
        nothing and "any authenticated worker may read any staged file" was a
        distinction without a difference; from here those files are the user's
        own reference audio, staged from their voice library.
        """
        session = self._session_for(context, request)
        if session is None:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown or expired session.")
            return
        if not self._may_read_input(session, request):
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "This input belongs to a task that is not running on this worker.",
            )
            return
        path = self._resolve_input(request.artifact_id)
        if path is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Artifact not found.")
            return
        # A ref minted here rather than the caller's echoed back: the request
        # carries the worker's session token, and nothing goes back out that
        # did not have to go out.
        served = pb.ArtifactRef(
            artifact_id=request.artifact_id,
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            filename=os.path.basename(path),
            content_type=request.content_type,
            size_bytes=os.path.getsize(path),
        )
        offset = 0
        with open(path, "rb") as fh:
            while True:
                data = fh.read(_DOWNLOAD_CHUNK_BYTES)
                if not data:
                    break
                yield pb.ArtifactChunk(ref=served, offset=offset, data=data, last=False)
                offset += len(data)
        yield pb.ArtifactChunk(ref=served, offset=offset, data=b"", last=True)

    def _may_read_input(self, session: _Session, ref) -> bool:
        """Does this session hold a live attempt of the task naming the input?

        Per-task rather than per-artifact on purpose: staged inputs are
        content-hashed so repeated clones of one voice share a single copy, and
        an id that is deliberately shared cannot itself carry the
        authorisation. The attempt does.
        """
        task = self.scheduler.get(ref.task_id) if ref.task_id else None
        if task is None:
            return False
        if ref.attempt_id:
            attempt, foreign = self._attempt_and_owner(session, ref)
            return attempt is not None and not foreign and attempt.state.live
        return any(
            attempt.worker_id == session.worker_id and attempt.state.live
            for attempt in task.attempts
        )

    def _session_for(self, context, ref) -> Optional[_Session]:
        """The live session a transfer belongs to, by ref token or by metadata."""
        token = getattr(ref, "session_token", "") or ""
        if token and token in self._by_token:
            session = self._by_token[token]
            return None if session.session.expired() else session
        return self._session_from_metadata(context)

    def _resolve_input(self, artifact_id: str) -> Optional[str]:
        """Resolve an input reference to a path inside the artifact directory.

        Containment is enforced rather than assumed: a worker is a remote peer,
        and an artifact id is attacker-controlled input, so ``../`` must not be
        able to read arbitrary files off the control plane. One containment
        implementation for the whole file — a second, hand-rolled one is how
        the two directions came to disagree in the first place.
        """
        path = self._contained_artifact(artifact_id)
        return path if path and os.path.isfile(path) else None


async def serve(
    servicer: WorkerServicer,
    *,
    host: str = "0.0.0.0",
    port: int = 7443,
    certificate_pem: bytes,
    private_key_pem: bytes,
) -> grpc.aio.Server:
    """Start the control-plane server. TLS is not optional."""
    server = grpc.aio.server(
        options=[
            # gRPC enables SO_REUSEPORT by default where the platform supports
            # it. That is useful for replicated stateless services, but two
            # VoiceStudio control planes have independent worker registries
            # and schedulers: sharing this port sends each connection to an
            # arbitrary app instance.
            ("grpc.so_reuseport", 0),
            ("grpc.max_receive_message_length", 8 * 1024 * 1024),
            ("grpc.max_send_message_length", 8 * 1024 * 1024),
            # Consumer NAT/CGNAT mappings expire silently after 30–120s, and a
            # dead mapping looks exactly like a healthy idle connection until
            # something asks. Keepalives make the difference observable.
            ("grpc.keepalive_time_ms", 25_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            # The client above sends an HTTP/2 ping every 25 seconds while its
            # long-lived Control RPC is idle. gRPC's server default permits
            # only two idle pings and then sends ENHANCE_YOUR_CALM
            # ("too_many_pings"), evicting every healthy worker. Accept the
            # interval this protocol itself configures; zero means no count
            # ceiling, while the minimum interval still rate-limits peers.
            ("grpc.http2.min_ping_interval_without_data_ms", 20_000),
            ("grpc.http2.max_pings_without_data", 0),
        ]
    )
    pb_grpc.add_WorkerServiceServicer_to_server(servicer, server)
    credentials = grpc.ssl_server_credentials([(private_key_pem, certificate_pem)])
    try:
        bound_port = server.add_secure_port(f"{host}:{port}", credentials)
    except RuntimeError as exc:
        raise ControlPlaneBindError(
            f"Another VoiceStudio instance is already accepting remote workers "
            f"on port {port}. Close the other instance, or set "
            "OMNIVOICE_WORKER_PORT to a different port and restart VoiceStudio."
        ) from exc
    # add_secure_port() reports bind failure as 0; awaiting start() is not the
    # documented place to discover it and historically let this pass unseen.
    if bound_port == 0:
        raise ControlPlaneBindError(
            f"Another VoiceStudio instance is already accepting remote workers "
            f"on port {port}. Close the other instance, or set "
            "OMNIVOICE_WORKER_PORT to a different port and restart VoiceStudio."
        )
    await server.start()
    logger.info("Worker control plane listening on %s:%d (TLS)", host, port)
    return server


def _peer_address(context) -> str:
    """Turn gRPC's peer string into a plain ip:port.

    gRPC reports "ipv4:192.168.0.5:54321" or "ipv6:[::1]:54321"; neither is
    something to show a user.
    """
    try:
        peer = context.peer() or ""
    except Exception:
        return ""
    if peer.startswith("ipv4:"):
        return peer[5:]
    if peer.startswith("ipv6:"):
        return peer[5:]
    return peer


__all__ = [
    "ControlPlaneBindError",
    "INLINE_RESULT_THRESHOLD",
    "MAX_ARTIFACT_BYTES",
    "MAX_TASK_ARTIFACT_BYTES",
    "MIN_SUPPORTED_VERSION",
    "PROTOCOL_VERSION",
    "SESSION_METADATA_KEY",
    "WorkerServicer",
    "serve",
]
