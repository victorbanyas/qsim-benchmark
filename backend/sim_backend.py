"""Generic async job machinery for a single simulated quantum hardware backend.

A `SimBackend` mimics one cloud hardware provider's job API: `submit_job`
puts work on a queue, a background worker thread drains the queue and
executes jobs one at a time, and `get_status` / `get_result` let a caller
poll for progress and pull the final counts once a job is done.

This class knows nothing about *how* a circuit is actually executed - that
is the job of the `SimulatorRunner` passed into the constructor. Three
`SimBackend` instances, each wrapping a different `SimulatorRunner`
(state vector, noisy state vector, noisy density matrix), give you the
three backends required by Part 1 without duplicating any of the
queue / thread / status bookkeeping: that machinery lives here, once.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Protocol
from queue import Queue


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"


class JobNotFoundError(KeyError):
    """Raised when a job id was never submitted to this backend."""


class JobNotReadyError(RuntimeError):
    """Raised by get_result() when the job hasn't finished (or failed) yet."""


class SimulatorRunner(Protocol):
    """Strategy interface: knows how to execute one circuit and return counts.

    Each of the three Part 1 configurations (plain state vector, noisy
    state vector, noisy density matrix) is a separate implementation of
    this protocol, e.g. wrapping a differently-configured AerSimulator.
    SimBackend is deliberately ignorant of Qiskit/Aer specifics - it only
    ever calls `run(qasm, num_shots)` and expects a counts dict back.
    """

    def run(self, qasm: str, num_shots: int) -> Dict[str, int]:
        """Execute `qasm` for `num_shots` shots.

        Returns a histogram over measured bitstrings, e.g.
        {"00": 512, "11": 488}.
        """
        ...


@dataclass
class Job:
    id: str
    qasm: str
    num_shots: int
    status: JobStatus = JobStatus.QUEUED
    counts: Optional[Dict[str, int]] = None
    error: Optional[str] = None
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


# Sentinel pushed onto the queue to tell the worker thread to stop cleanly,
# without needing a poison-pill string that could collide with a real job id.
_SHUTDOWN = "SHUTDOWN"


class SimBackend:
    """One simulated backend: an async, queued job system in front of a SimulatorRunner.

    Mirrors how a real cloud provider exposes one specific QPU: you submit
    a job to *this* backend, it queues behind whatever else is running
    here, and a single worker thread processes jobs one at a time -
    independent of any other SimBackend instance. That independence is
    what lets one backend fail or lag in Part 2's benchmark without
    affecting the others.
    """

    def __init__(self, name: str, runner: SimulatorRunner):
        self.name = name
        self._runner = runner
        self._queue: Queue[str] = Queue()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread. Idempotent - safe to call once."""
        if self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._worker_loop, name=f"SimBackend-{self.name}", daemon=True
        )
        self._worker.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        """Ask the worker to finish its current job and stop, then join it."""
        if self._worker is None:
            return
        self._queue.put(_SHUTDOWN)
        self._worker.join(timeout=timeout)
        self._worker = None

    # -- public API: submit_job / get_status / get_result --------------

    def submit_job(self, qasm: str, num_shots: int) -> str:
        """Queue a job and return its id immediately (does not block on execution)."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, qasm=qasm, num_shots=num_shots)
        with self._lock:
            self._jobs[job_id] = job
        self._queue.put(job_id)
        return job_id

    def get_status(self, job_id: str) -> JobStatus:
        return self._get_job(job_id).status

    def get_result(self, job_id: str) -> Dict[str, int]:
        """Return the counts for a finished job.

        Raises JobNotReadyError if the job is still queued/running, and
        RuntimeError (wrapping the stored error) if the job failed. Callers
        are expected to poll get_status() until it's DONE or ERROR first.
        """
        job = self._get_job(job_id)
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise JobNotReadyError(
                f"job {job_id} is still {job.status.value}; poll get_status() first"
            )
        if job.status == JobStatus.ERROR:
            raise RuntimeError(f"job {job_id} failed: {job.error}")
        assert job.counts is not None  # DONE jobs always have counts
        return job.counts

    # -- internals -------------------------------------------------------

    def _get_job(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"unknown job id: {job_id}")
        return job

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()  # blocks until work arrives - no busy polling
            if item is _SHUTDOWN:
                return
            self._run_job(item)

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.RUNNING
            job.started_at = time.time()

        try:
            counts = self._runner.run(job.qasm, job.num_shots)
        except Exception as exc:  # noqa: BLE001 - a bad job must not kill the worker
            with self._lock:
                job.status = JobStatus.ERROR
                job.error = str(exc)
                job.finished_at = time.time()
            return

        with self._lock:
            job.status = JobStatus.DONE
            job.counts = counts
            job.finished_at = time.time()
