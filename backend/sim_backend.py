"""SimBackend: an async, queued job system in front of a SimulatorRunner -
mimics one cloud hardware provider's job API.

submit_job() queues work; a background worker thread drains the queue and
executes jobs one at a time; get_status()/get_result() let a caller poll
for progress and pull counts once a job is done. Execution itself is
delegated to a SimulatorRunner. Job records and queueing go through an
injected Store[Job]/Queue[str] (see backend/dal), so a caller
sharing the same Store can read a Job's status directly instead of
polling this class - which is what BenchmarkEngine does.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
from typing import Dict, Optional

from backend.dal import InMemoryQueue, InMemoryStore, Queue, Store
from backend.errors import JobNotFoundError, JobNotReadyError
from backend.models import Job, JobStatus
from backend.runners import SimulatorRunner

__all__ = [
    "JobNotFoundError",
    "JobNotReadyError",
    "SimBackend",
]


class SimBackend:
    """One simulated backend: jobs submitted here queue behind whatever
    else is running, processed one at a time by a single worker thread,
    independent of any other SimBackend instance.
    """

    def __init__(
        self,
        name: str,
        runner: SimulatorRunner,
        job_store: Optional[Store[Job]] = None,
        job_queue: Optional[Queue[str]] = None,
    ):
        self.name = name
        self._runner = runner
        # Defaults to private in-memory instances; pass a store shared
        # with other SimBackends/BenchmarkEngine to read Jobs across them.
        self._store: Store[Job] = job_store or InMemoryStore()
        self._queue: Queue[str] = job_queue or InMemoryQueue()
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
        self._queue.close()
        self._worker.join(timeout=timeout)
        self._worker = None

    # -- public API: submit_job / get_status / get_result --------------

    def submit_job(self, qasm: str, num_shots: int) -> str:
        """Queue a job and return its id immediately (does not block on execution)."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, qasm=qasm, num_shots=num_shots, backend_name=self.name)
        self._store.put(job_id, job)
        self._queue.publish(job_id)
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
        job = self._store.get(job_id)
        if job is None:
            raise JobNotFoundError(f"unknown job id: {job_id}")
        return job

    def _worker_loop(self) -> None:
        while (job_id := self._queue.consume()) is not None:  # blocks - no busy polling
            self._run_job(job_id)

    def _run_job(self, job_id: str) -> None:
        self._store.update(
            job_id, lambda job: replace(job, status=JobStatus.RUNNING, started_at=time.time())
        )
        job = self._get_job(job_id)

        try:
            counts = self._runner.run(job.qasm, job.num_shots)
        except Exception as exc:  # noqa: BLE001 - a bad job must not kill the worker
            error_message = str(exc)
            self._store.update(
                job_id,
                lambda j: replace(
                    j, status=JobStatus.ERROR, error=error_message, finished_at=time.time()
                ),
            )
            return

        self._store.update(
            job_id,
            lambda j: replace(
                j, status=JobStatus.DONE, counts=counts, finished_at=time.time()
            ),
        )
