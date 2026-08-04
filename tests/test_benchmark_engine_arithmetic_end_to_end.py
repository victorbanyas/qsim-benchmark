"""End-to-end check: run the Part 1 arithmetic circuit through BenchmarkEngine
with all three real simulator backends.

Same circuit as tests/test_arithmetic_end_to_end.py (the 3-qubit ripple-carry
adder computing 3 + 5, expected bitstring "10000110") and the same caveat:
it's a stand-in for the real Classiq-synthesized "x |= 3; y |= 5; z |= x + y"
model (see classiq_model/arithmetic_example.py), because synthesizing
requires live, authenticated access to Classiq's cloud API that isn't
available in this sandbox.

Here that means standing in for the *Synthesizer* rather than calling
SimBackend directly: `_FixtureSynthesizer` returns the already-known QASM
for any qmod it's asked to synthesize, letting BenchmarkEngine's own
synthesize -> fan out -> score pipeline run for real against real Aer
simulators. Everything downstream of "what does synthesize(qmod) return" -
SimBackend, the three SimulatorRunners, scoring - is exercised exactly as
it would be with a real Synthesizer.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.benchmark_engine import BenchmarkEngine, BenchmarkStatus
from backend.sim_backend import JobStatus, SimBackend
from backend.runners import (
    NoisyDensityMatrixSimulatorRunner,
    NoisyStateVectorSimulatorRunner,
    StateVectorSimulatorRunner,
)
from backend.dal import InMemoryStore

QASM_PATH = Path(__file__).parent / "resources" / "arithmetic.qasm"
EXPECTED_BITSTRING = "10000110"  # cout=1, b=000, a=011, cin=0  ->  3 + 5 = 8
NUM_SHOTS = 2000


class _FixtureSynthesizer:
    """Stand-in Synthesizer: returns the committed QASM fixture regardless of
    the qmod it's given. See module docstring for why."""

    def __init__(self, qasm: str):
        self._qasm = qasm

    def synthesize(self, qmod: str) -> str:
        return self._qasm


def _wait_until_finished(
    engine: BenchmarkEngine, benchmark_id: str, timeout: float = 30.0
) -> BenchmarkStatus:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = engine.get_benchmark_status(benchmark_id)
        if status.is_finished:
            return status
        time.sleep(0.02)
    raise TimeoutError(f"benchmark {benchmark_id} did not finish within {timeout}s")


@pytest.fixture
def engine():
    qasm = QASM_PATH.read_text()
    # One Store[Job] shared by every backend AND the engine - this is what
    # lets the engine read status/results without ever calling back into
    # SimBackend (see backend/benchmark_engine.py's module docstring).
    job_store = InMemoryStore()
    backends = {
        "statevector": SimBackend("statevector", StateVectorSimulatorRunner(), job_store=job_store),
        "statevector_noisy": SimBackend(
            "statevector_noisy", NoisyStateVectorSimulatorRunner(), job_store=job_store
        ),
        "density_matrix_noisy": SimBackend(
            "density_matrix_noisy", NoisyDensityMatrixSimulatorRunner(), job_store=job_store
        ),
    }
    for backend in backends.values():
        backend.start()

    benchmark_engine = BenchmarkEngine(backends, _FixtureSynthesizer(qasm), job_store=job_store)
    benchmark_engine.start()

    yield benchmark_engine

    benchmark_engine.stop(timeout=5.0)
    for backend in backends.values():
        backend.stop(timeout=5.0)


def test_arithmetic_benchmark_across_all_three_backends(engine):
    benchmark_id = engine.benchmark(
        qmod="(3 + 5 arithmetic qmod)",
        expected_result=EXPECTED_BITSTRING,
        backends=["statevector", "statevector_noisy", "density_matrix_noisy"],
        num_shots=NUM_SHOTS,
    )

    status = _wait_until_finished(engine, benchmark_id)

    # Synthesis and every backend job must have completed cleanly - nothing
    # in this run is expected to fail.
    assert status.synthesis_status == JobStatus.DONE
    assert status.backend_statuses == {
        "statevector": JobStatus.DONE,
        "statevector_noisy": JobStatus.DONE,
        "density_matrix_noisy": JobStatus.DONE,
    }
    assert status.completed == status.total == 3

    result = engine.get_benchmark_result(benchmark_id)
    assert set(result) == {"statevector", "statevector_noisy", "density_matrix_noisy"}

    # No noise source at all in this configuration - every shot must be correct.
    assert result["statevector"] == 1.0

    # Noise degrades the success rate but the circuit isn't destroyed by it -
    # the score should land strictly between "never right" and "always right".
    assert 0.0 < result["statevector_noisy"] < 1.0
    assert 0.0 < result["density_matrix_noisy"] < 1.0
