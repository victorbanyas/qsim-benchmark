import time
from typing import Optional

import pytest

from backend.sim_backend import (
    JobNotFoundError,
    JobNotReadyError,
    JobStatus,
    SimBackend,
)


class FakeRunner:
    """Test double standing in for a real simulator-backed SimulatorRunner."""

    def __init__(self, counts=None, delay: float = 0.0, exc: Optional[Exception] = None):
        self.counts = counts if counts is not None else {"00": 100}
        self.delay = delay
        self.exc = exc
        self.calls = []

    def run(self, qasm, num_shots):
        self.calls.append((qasm, num_shots))
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.counts


def _wait_until_finished(backend, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = backend.get_status(job_id)
        if status in (JobStatus.DONE, JobStatus.ERROR):
            return status
        time.sleep(0.01)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


@pytest.fixture
def running_backend():
    """A started SimBackend that is always stopped, even if the test fails."""

    def _make(runner):
        backend = SimBackend("test", runner)
        backend.start()
        return backend

    created = []

    def factory(runner):
        backend = _make(runner)
        created.append(backend)
        return backend

    yield factory
    for backend in created:
        backend.stop(timeout=2.0)


def test_submit_job_runs_and_returns_counts(running_backend):
    backend = running_backend(FakeRunner(counts={"00": 512, "11": 488}))

    job_id = backend.submit_job("OPENQASM 2.0; ...", num_shots=1000)
    _wait_until_finished(backend, job_id)

    assert backend.get_status(job_id) == JobStatus.DONE
    assert backend.get_result(job_id) == {"00": 512, "11": 488}


def test_get_result_before_done_raises(running_backend):
    backend = running_backend(FakeRunner(delay=0.2))

    job_id = backend.submit_job("qasm", num_shots=100)

    with pytest.raises(JobNotReadyError):
        backend.get_result(job_id)


def test_unknown_job_id_raises(running_backend):
    backend = running_backend(FakeRunner())

    with pytest.raises(JobNotFoundError):
        backend.get_status("does-not-exist")
    with pytest.raises(JobNotFoundError):
        backend.get_result("does-not-exist")


def test_failed_job_reports_error_without_killing_worker(running_backend):
    runner = FakeRunner(exc=ValueError("boom"))
    backend = running_backend(runner)

    failing_job = backend.submit_job("qasm", num_shots=100)
    _wait_until_finished(backend, failing_job)

    assert backend.get_status(failing_job) == JobStatus.ERROR
    with pytest.raises(RuntimeError):
        backend.get_result(failing_job)

    # The worker thread must survive a failed job and keep processing new ones.
    runner.exc = None
    ok_job = backend.submit_job("qasm", num_shots=100)
    _wait_until_finished(backend, ok_job)

    assert backend.get_status(ok_job) == JobStatus.DONE
    assert backend.get_result(ok_job) == {"00": 100}


def test_jobs_on_one_backend_run_serially(running_backend):
    runner = FakeRunner(delay=0.05)
    backend = running_backend(runner)

    ids = [backend.submit_job("qasm", num_shots=10) for _ in range(3)]
    for job_id in ids:
        _wait_until_finished(backend, job_id)

    assert len(runner.calls) == 3
    assert all(backend.get_status(j) == JobStatus.DONE for j in ids)


def test_start_is_idempotent(running_backend):
    backend = running_backend(FakeRunner())
    backend.start()  # second call should be a no-op, not a second thread
    job_id = backend.submit_job("qasm", num_shots=10)
    _wait_until_finished(backend, job_id)
    assert backend.get_status(job_id) == JobStatus.DONE
