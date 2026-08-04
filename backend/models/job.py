from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from backend.models.enums import JobStatus


@dataclass
class Job:
    """One Part 1 job, as stored in a Store[Job] shared by every SimBackend
    and read directly by BenchmarkEngine - this record IS "the DB row";
    SimBackend's worker flushes every status transition here directly.
    """

    id: str
    qasm: str
    num_shots: int
    backend_name: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    counts: Optional[Dict[str, int]] = None
    error: Optional[str] = None
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
