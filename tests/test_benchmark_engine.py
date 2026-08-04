import time
from typing import Dict, Optional

import pytest

from backend.benchmark_engine import (
    BenchmarkEngine,
    BenchmarkNotFoundError,
    BenchmarkNotReadyError,
    BenchmarkStatus,
    UnknownBackendError,
)
from backend.sim_backend import JobStatus, SimBackend
from backend.dal import InMemoryStore


class FakeRunner:
    """Same test double as test_sim_backend.py - kept local and self-contained."""

    def __init__(self, counts=None, delay: float = 0.0, exc: Optional[Exception] = None):
        self.counts = counts if counts is not None else {"0": 100}
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


class FakeSynthesizer:
    """Test double for Synthesizer - records every qmod it was asked to synthesize."""

    def __init__(self, qasm: str = "FAKE_QASM", exc: Optional[Exception] = None):
        self.qasm = qasm
        self.exc = exc
        self.calls = []

    def synthesize(self, qmod: str) -> str:
        self.calls.append(qmod)
        if self.exc:
            raise self.exc
        return self.qasm


class DelayedSynthesizer:
    """Synthesizer whose synthesize() call takes a fixed amount of time -
    used to demonstrate that multiple BenchmarkEngine workers actually run
    concurrently rather than one at a time."""

    def __init__(self, delay: float, qasm: str = "QASM"):
        self.delay = delay
        self.qasm = qasm

    def synthesize(self, qmod: str) -> str:
        time.sleep(self.delay)
        return self.qasm


def _wait_until_finished(
    engine: BenchmarkEngine, benchmark_id: str, timeout: float = 2.0
) -> BenchmarkStatus:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = engine.get_benchmark_status(benchmark_id)
        if status.is_finished:
            return status
        time.sleep(0.01)
    raise TimeoutError(f"benchmark {benchmark_id} did not finish within {timeout}s")


@pytest.fixture
def make_engine():
    """Builds a started BenchmarkEngine over started SimBackends, and tears both down."""
    created_backends = []
    created_engines = []

    def factory(
        runners: Dict[str, FakeRunner],
        synthesizer: Optional[FakeSynthesizer] = None,
        num_workers: int = 1,
    ):
        # One Store[Job] shared by every backend AND the engine - this is
        # what lets the engine read status/results without ever calling
        # back into SimBackend.
        job_store = InMemoryStore()

        backends = {}
        for name, runner in runners.items():
            backend = SimBackend(name, runner, job_store=job_store)
            backend.start()
            backends[name] = backend
            created_backends.append(backend)

        engine = BenchmarkEngine(
            backends, synthesizer or FakeSynthesizer(), job_store=job_store, num_workers=num_workers
        )
        engine.start()
        created_engines.append(engine)
        return engine

    yield factory

    for engine in created_engines:
        engine.stop(timeout=2.0)
    for backend in created_backends:
        backend.stop(timeout=2.0)


def test_benchmark_scores_all_backends(make_engine):
    engine = make_engine(
        {
            "b1": FakeRunner(counts={"11": 80, "00": 20}),
            "b2": FakeRunner(counts={"11": 60, "00": 40}),
        }
    )

    bid = engine.benchmark(qmod="QMOD", expected_result="11", backends=["b1", "b2"], num_shots=100)
    status = _wait_until_finished(engine, bid)

    assert status.synthesis_status == JobStatus.DONE
    assert status.backend_statuses == {"b1": JobStatus.DONE, "b2": JobStatus.DONE}
    assert status.completed == 2
    assert status.total == 2

    result = engine.get_benchmark_result(bid)
    assert result == {"b1": 0.8, "b2": 0.6}


def test_unknown_backend_rejected_immediately(make_engine):
    engine = make_engine({"b1": FakeRunner()})

    with pytest.raises(UnknownBackendError):
        engine.benchmark(qmod="QMOD", expected_result="0", backends=["does-not-exist"], num_shots=10)


def test_partial_failure_does_not_block_other_backends(make_engine):
    engine = make_engine(
        {
            "good": FakeRunner(counts={"11": 100}),
            "bad": FakeRunner(exc=RuntimeError("simulator exploded")),
        }
    )

    bid = engine.benchmark(qmod="QMOD", expected_result="11", backends=["good", "bad"], num_shots=100)
    status = _wait_until_finished(engine, bid)

    assert status.backend_statuses == {"good": JobStatus.DONE, "bad": JobStatus.ERROR}
    # The failing backend simply doesn't appear - it never produced a score.
    assert engine.get_benchmark_result(bid) == {"good": 1.0}


def test_retry_backend_reuses_qasm_without_resynthesizing(make_engine):
    flaky_runner = FakeRunner(exc=RuntimeError("transient failure"))
    synthesizer = FakeSynthesizer(qasm="THE_QASM")
    engine = make_engine({"flaky": flaky_runner}, synthesizer=synthesizer)

    bid = engine.benchmark(qmod="QMOD", expected_result="0", backends=["flaky"], num_shots=50)
    _wait_until_finished(engine, bid)
    assert engine.get_benchmark_status(bid).backend_statuses["flaky"] == JobStatus.ERROR
    assert len(synthesizer.calls) == 1

    # Fix the runner and retry - only that one backend step should re-run.
    flaky_runner.exc = None
    flaky_runner.counts = {"0": 50}
    engine.retry_backend(bid, "flaky")

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if engine.get_benchmark_status(bid).backend_statuses["flaky"] == JobStatus.DONE:
            break
        time.sleep(0.01)

    assert engine.get_benchmark_status(bid).backend_statuses["flaky"] == JobStatus.DONE
    assert engine.get_benchmark_result(bid) == {"flaky": 1.0}
    # The whole point: retrying never touches synthesis again.
    assert len(synthesizer.calls) == 1
    assert flaky_runner.calls[-1] == ("THE_QASM", 50)


def test_synthesis_failure_is_reported_and_no_backends_run(make_engine):
    runner = FakeRunner()
    synthesizer = FakeSynthesizer(exc=ValueError("bad qmod"))
    engine = make_engine({"b1": runner}, synthesizer=synthesizer)

    bid = engine.benchmark(qmod="BROKEN", expected_result="0", backends=["b1"], num_shots=10)
    status = _wait_until_finished(engine, bid)

    assert status.synthesis_status == JobStatus.ERROR
    assert status.backend_statuses == {}
    assert runner.calls == []  # never got a chance to run
    with pytest.raises(RuntimeError):
        engine.get_benchmark_result(bid)


def test_result_not_ready_before_synthesis_completes(make_engine):
    runner = FakeRunner()
    engine = make_engine({"b1": runner})
    # Stop the worker so nothing processes the queued benchmark yet.
    engine.stop(timeout=2.0)

    bid = engine.benchmark(qmod="QMOD", expected_result="0", backends=["b1"], num_shots=10)

    status = engine.get_benchmark_status(bid)
    assert status.synthesis_status == JobStatus.QUEUED
    with pytest.raises(BenchmarkNotReadyError):
        engine.get_benchmark_result(bid)


def test_unknown_benchmark_id_raises(make_engine):
    engine = make_engine({"b1": FakeRunner()})

    with pytest.raises(BenchmarkNotFoundError):
        engine.get_benchmark_status("does-not-exist")
    with pytest.raises(BenchmarkNotFoundError):
        engine.get_benchmark_result("does-not-exist")


def test_num_workers_processes_benchmarks_concurrently(make_engine):
    engine = make_engine(
        {"b1": FakeRunner(counts={"0": 10})},
        synthesizer=DelayedSynthesizer(delay=0.3),
        num_workers=3,
    )

    started = time.time()
    ids = [
        engine.benchmark(qmod=f"QMOD-{i}", expected_result="0", backends=["b1"], num_shots=10)
        for i in range(3)
    ]
    for bid in ids:
        _wait_until_finished(engine, bid)
    elapsed = time.time() - started

    # Three 0.3s synthesis calls run one at a time would take ~0.9s; with 3
    # workers they should overlap and finish well under that - generous
    # margin against scheduling jitter, same style as the noise thresholds
    # in the arithmetic tests.
    assert elapsed < 0.7

    for bid in ids:
        assert engine.get_benchmark_result(bid) == {"b1": 1.0}


def test_stop_joins_every_worker_in_the_pool(make_engine):
    """Regression test for the shutdown-broadcast fix in InMemoryQueue:
    with a single sentinel and no re-broadcast, only one of several workers
    would ever wake up from consume(), and stop() would hang joining the
    rest until its timeout - silently leaving threads alive rather than
    failing loudly.
    """
    engine = make_engine({"b1": FakeRunner()}, num_workers=4)
    workers = list(engine._workers)  # noqa: SLF001 - checking the exact property this test exists for
    assert len(workers) == 4

    engine.stop(timeout=2.0)

    assert all(not worker.is_alive() for worker in workers)
