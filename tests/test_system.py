"""Tests for build_system(): does it actually wire backends and the engine
to the same Store[Job]? The strongest evidence isn't inspecting internals,
it's that a full benchmark can run end to end - if the store weren't
shared, get_benchmark_status()/get_benchmark_result() would never see the
jobs each backend's worker wrote, and the wait loop below would time out.
"""

from __future__ import annotations

import time
from typing import Dict

from backend.sim_backend import JobStatus, SimulatorRunner
from backend.dal import InMemoryStore
from backend.system import build_system
from backend.utils import wait_for_benchmark


class FakeRunner:
    def __init__(self, counts):
        self.counts = counts

    def run(self, qasm, num_shots) -> Dict[str, int]:
        return self.counts


class FakeSynthesizer:
    def __init__(self, qasm: str = "FAKE_QASM"):
        self.qasm = qasm
        self.calls = []

    def synthesize(self, qmod: str) -> str:
        self.calls.append(qmod)
        return self.qasm


def test_build_system_wires_a_shared_job_store_end_to_end():
    runners: Dict[str, SimulatorRunner] = {
        "b1": FakeRunner(counts={"11": 90, "00": 10}),
        "b2": FakeRunner(counts={"11": 70, "00": 30}),
    }
    backends, engine = build_system(runners=runners, synthesizer=FakeSynthesizer())

    for backend in backends.values():
        backend.start()
    engine.start()
    try:
        bid = engine.benchmark(
            qmod="QMOD", expected_result="11", backends=["b1", "b2"], num_shots=100
        )
        status = wait_for_benchmark(engine, bid, timeout=2.0)

        assert status.synthesis_status == JobStatus.DONE
        assert status.backend_statuses == {"b1": JobStatus.DONE, "b2": JobStatus.DONE}
        assert engine.get_benchmark_result(bid) == {"b1": 0.9, "b2": 0.7}
    finally:
        engine.stop(timeout=2.0)
        for backend in backends.values():
            backend.stop(timeout=2.0)


def test_build_system_defaults_to_the_three_standard_backends():
    backends, engine = build_system(synthesizer=FakeSynthesizer())

    assert set(backends) == {"statevector", "statevector_noisy", "density_matrix_noisy"}

    # Nothing was started - stop() on an unstarted engine/backend must be a
    # harmless no-op, not an error.
    engine.stop()
    for backend in backends.values():
        backend.stop()


def test_build_system_uses_the_explicitly_passed_job_store():
    job_store = InMemoryStore()
    backends, _engine = build_system(job_store=job_store, synthesizer=FakeSynthesizer())

    # White-box check of the one invariant build_system() exists to
    # guarantee: every backend was built with the exact store passed in,
    # not a fresh default one.
    assert all(backend._store is job_store for backend in backends.values())  # noqa: SLF001
