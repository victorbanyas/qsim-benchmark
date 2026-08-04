from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.models.enums import JobStatus


@dataclass
class BackendRun:
    """One backend's participation in a benchmark: just enough to find the
    Job that carries the real status/result in the shared job store.

    Deliberately doesn't cache status/counts/score - those are always read
    fresh from the Job the backend already flushed to the shared store, so
    there's no cached copy that can go stale or need an explicit refresh.

    No backend_name field either - it's already the dict key in
    Benchmark.backend_runs, so storing it again here would just be a copy
    that could drift from the key it's supposed to match.
    """

    job_id: Optional[str] = None
    submit_error: Optional[str] = None  # set if submit_job() itself raised


@dataclass
class Benchmark:
    """One benchmark run: a qmod synthesized once, executed on N backends."""

    id: str
    qmod: str
    expected_result: str  # the expected measurement bitstring, e.g. "100010111"
    num_shots: int
    backend_names: List[str]
    synthesis_status: JobStatus = JobStatus.QUEUED
    qasm: Optional[str] = None
    synthesis_error: Optional[str] = None
    backend_runs: Dict[str, BackendRun] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class BenchmarkStatus:
    """What get_benchmark_status() returns - enough to show real progress."""

    synthesis_status: JobStatus
    backend_statuses: Dict[str, JobStatus]

    @property
    def completed(self) -> int:
        return sum(
            1
            for status in self.backend_statuses.values()
            if status in (JobStatus.DONE, JobStatus.ERROR)
        )

    @property
    def total(self) -> int:
        return len(self.backend_statuses)

    @property
    def is_finished(self) -> bool:
        """True once nothing further will happen without a retry_backend() call."""
        if self.synthesis_status == JobStatus.ERROR:
            return True
        if self.synthesis_status != JobStatus.DONE:
            return False
        return self.completed == self.total
