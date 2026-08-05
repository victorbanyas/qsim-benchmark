"""Shared polling helpers: block until a job or benchmark reaches a
terminal state, or raise TimeoutError. Used by tests and by the
classiq_model/ example scripts, wherever something needs to wait on a
SimBackend job or a BenchmarkEngine benchmark synchronously.
"""

from __future__ import annotations

import time

from backend.benchmark_engine import BenchmarkEngine
from backend.models import BenchmarkStatus, JobStatus
from backend.sim_backend import SimBackend

__all__ = ["wait_for_job", "wait_for_benchmark"]


def wait_for_job(
    backend: SimBackend, job_id: str, timeout: float, poll_interval: float = 0.01
) -> JobStatus:
    """Poll `backend` until `job_id` reaches DONE or ERROR."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = backend.get_status(job_id)
        if status in (JobStatus.DONE, JobStatus.ERROR):
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def wait_for_benchmark(
    engine: BenchmarkEngine, benchmark_id: str, timeout: float, poll_interval: float = 0.01
) -> BenchmarkStatus:
    """Poll `engine` until `benchmark_id` is finished (synthesis + all backends)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = engine.get_benchmark_status(benchmark_id)
        if status.is_finished:
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"benchmark {benchmark_id} did not finish within {timeout}s")
