"""BenchmarkEngine: synthesizes a qmod once and runs it across N SimBackends,
tracked together under one benchmark id.

benchmark() synthesizes the given qmod exactly once, then fans the
resulting QASM out to every requested backend via that backend's own
submit_job(). Synthesis and fan-out run on background worker threads (a
pool of `num_workers` for synthesis) so benchmark() never blocks. Status
and results are read directly from the same Store[Job] every SimBackend
writes to (see backend/dal) - no polling, no cached status field
on BackendRun, nothing to refresh.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from typing import Dict, List, Optional

from backend.errors import BenchmarkNotFoundError, BenchmarkNotReadyError, UnknownBackendError
from backend.models import BackendRun, Benchmark, BenchmarkStatus, Job, JobStatus
from backend.dal import InMemoryQueue, InMemoryStore, Queue, Store
from backend.sim_backend import SimBackend
from backend.synthesizers import Synthesizer

__all__ = [
    "BenchmarkNotFoundError",
    "BenchmarkNotReadyError",
    "UnknownBackendError",
    "BenchmarkEngine",
]


def _with_backend_run(benchmark: Benchmark, name: str, **updates: object) -> Benchmark:
    """Return a copy of `benchmark` with one BackendRun's fields updated."""
    new_runs = dict(benchmark.backend_runs)
    new_runs[name] = replace(new_runs[name], **updates)
    return replace(benchmark, backend_runs=new_runs)


class BenchmarkEngine:
    """Synthesizes a qmod once and fans it out to N backends, tracked by id."""

    def __init__(
        self,
        backends: Dict[str, SimBackend],
        synthesizer: Synthesizer,
        job_store: Store[Job],
        benchmark_store: Optional[Store[Benchmark]] = None,
        benchmark_queue: Optional[Queue[str]] = None,
        num_workers: int = 1,
    ):
        self._backends = backends
        self._synthesizer = synthesizer
        # Must be the same Store[Job] every SimBackend in `backends` uses -
        # not defaulted, so a mismatch fails loudly instead of silently.
        self._job_store = job_store
        self._benchmark_store: Store[Benchmark] = benchmark_store or InMemoryStore()
        self._queue: Queue[str] = benchmark_queue or InMemoryQueue()
        # How many benchmarks can synthesize concurrently. Requires a
        # thread-safe Synthesizer if > 1.
        self._num_workers = num_workers
        self._workers: List[threading.Thread] = []

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            threading.Thread(
                target=self._worker_loop, name=f"BenchmarkEngine-{i}", daemon=True
            )
            for i in range(self._num_workers)
        ]
        for worker in self._workers:
            worker.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        if not self._workers:
            return
        self._queue.close()
        for worker in self._workers:
            worker.join(timeout=timeout)
        self._workers = []

    # -- public API: benchmark / get_benchmark_status / get_benchmark_result --

    def benchmark(
        self,
        qmod: str,
        expected_result: str,
        backends: List[str],
        num_shots: int,
    ) -> str:
        """Queue a new benchmark and return its id immediately.

        Validates backend names up front; synthesis happens on a worker.
        """
        unknown = [name for name in backends if name not in self._backends]
        if unknown:
            raise UnknownBackendError(f"unknown backend(s): {unknown}")

        benchmark_id = str(uuid.uuid4())
        record = Benchmark(
            id=benchmark_id,
            qmod=qmod,
            expected_result=expected_result,
            num_shots=num_shots,
            backend_names=list(backends),
        )
        self._benchmark_store.put(benchmark_id, record)
        self._queue.publish(benchmark_id)
        return benchmark_id

    def get_benchmark_status(self, benchmark_id: str) -> BenchmarkStatus:
        record = self._get_benchmark(benchmark_id)
        return BenchmarkStatus(
            synthesis_status=record.synthesis_status,
            backend_statuses=self._backend_statuses(record),
        )

    def get_benchmark_result(self, benchmark_id: str) -> Dict[str, float]:
        """Return {backend_name: score} for backends that have finished.

        Backends still QUEUED/RUNNING or that errored are simply absent -
        poll get_benchmark_status() to see why, and use retry_backend() for
        errors. Scores are computed fresh on every call, never cached.
        """
        record = self._get_benchmark(benchmark_id)
        if record.synthesis_status in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise BenchmarkNotReadyError(
                f"benchmark {benchmark_id} is still synthesizing; poll get_benchmark_status() first"
            )
        if record.synthesis_status == JobStatus.ERROR:
            raise RuntimeError(f"benchmark {benchmark_id} synthesis failed: {record.synthesis_error}")

        result: Dict[str, float] = {}
        for name, run in record.backend_runs.items():
            if run.job_id is None:
                continue  # submission itself failed - see run.submit_error
            job = self._job_store.get(run.job_id)
            if job is not None and job.status == JobStatus.DONE and job.counts is not None:
                result[name] = job.counts.get(record.expected_result, 0) / sum(job.counts.values())
        return result

    def retry_backend(self, benchmark_id: str, backend_name: str) -> None:
        """Re-submit one backend's job using the already-synthesized QASM.
        Only valid for a backend currently in ERROR.
        """
        record = self._get_benchmark(benchmark_id)
        if record.synthesis_status != JobStatus.DONE:
            raise BenchmarkNotReadyError(
                f"benchmark {benchmark_id} has no synthesized QASM to retry with"
            )
        if backend_name not in self._backends:
            raise UnknownBackendError(f"unknown backend: {backend_name}")

        run = record.backend_runs.get(backend_name)
        if run is None:
            raise UnknownBackendError(
                f"backend {backend_name!r} was not part of benchmark {benchmark_id}"
            )

        current_status = self._backend_status(run)
        if current_status != JobStatus.ERROR:
            raise RuntimeError(
                f"backend {backend_name!r} is {current_status.value}, not ERROR - nothing to retry"
            )

        assert record.qasm is not None
        job_id = self._backends[backend_name].submit_job(record.qasm, record.num_shots)
        self._benchmark_store.update(
            benchmark_id,
            lambda b: _with_backend_run(b, backend_name, job_id=job_id, submit_error=None),
        )

    # -- internals -------------------------------------------------------

    def _get_benchmark(self, benchmark_id: str) -> Benchmark:
        record = self._benchmark_store.get(benchmark_id)
        if record is None:
            raise BenchmarkNotFoundError(f"unknown benchmark id: {benchmark_id}")
        return record

    def _backend_status(self, run: BackendRun) -> JobStatus:
        """Current status of one backend run, read straight from the
        shared job store."""
        if run.job_id is None:
            return JobStatus.ERROR if run.submit_error else JobStatus.QUEUED
        job = self._job_store.get(run.job_id)
        if job is None:
            return JobStatus.ERROR  # shouldn't happen: job vanished from the store
        return job.status

    def _backend_statuses(self, record: Benchmark) -> Dict[str, JobStatus]:
        return {name: self._backend_status(run) for name, run in record.backend_runs.items()}

    def _worker_loop(self) -> None:
        while (benchmark_id := self._queue.consume()) is not None:
            self._process_new_benchmark(benchmark_id)

    def _process_new_benchmark(self, benchmark_id: str) -> None:
        self._benchmark_store.update(benchmark_id, lambda b: replace(b, synthesis_status=JobStatus.RUNNING))
        record = self._get_benchmark(benchmark_id)

        try:
            qasm = self._synthesizer.synthesize(record.qmod)
        except Exception as exc:  # noqa: BLE001 - a bad model must not kill the worker
            error_message = str(exc)
            self._benchmark_store.update(
                benchmark_id,
                lambda b: replace(b, synthesis_status=JobStatus.ERROR, synthesis_error=error_message),
            )
            return

        record = self._benchmark_store.update(
            benchmark_id,
            lambda b: replace(
                b,
                qasm=qasm,
                synthesis_status=JobStatus.DONE,
                backend_runs={name: BackendRun() for name in b.backend_names},
            ),
        )

        # Fan out: submit_job() is non-blocking, so this loop is fast.
        for name in record.backend_names:
            backend = self._backends[name]
            try:
                job_id = backend.submit_job(qasm, record.num_shots)
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self._benchmark_store.update(
                    benchmark_id, lambda b: _with_backend_run(b, name, submit_error=error_message)
                )
                continue
            self._benchmark_store.update(
                benchmark_id, lambda b: _with_backend_run(b, name, job_id=job_id)
            )
