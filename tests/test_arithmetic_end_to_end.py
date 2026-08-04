"""End-to-end check: run a real arithmetic circuit through all three Part 1 backends.

Circuit under test: a 3-qubit ripple-carry adder (Qiskit's built-in
`CDKMRippleCarryAdder`, kind="full") computing 3 + 5. Inputs are prepared
with X gates; qubit layout (index 0 first) is:

    cin[0], a[0..2], b[0..2], cout[0]

The adder leaves `a` unchanged and sets `b := (a + b) mod 8`, `cout` := the
carry bit. For a=3, b=5: 3 + 5 = 8 = 0b1000, so b ends at 0 and cout = 1.
Qiskit's counts keys read qubit 7 (cout) first through qubit 0 (cin) last,
giving a single fixed expected bitstring, "10000110", on every noise-free
shot - confirmed empirically (see EXPECTED_BITSTRING below).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

import pytest

from backend.sim_backend import JobStatus, SimBackend
from backend.runners import (
    NoisyDensityMatrixSimulatorRunner,
    NoisyStateVectorSimulatorRunner,
    StateVectorSimulatorRunner,
)

QASM_PATH = Path(__file__).parent / "resources" / "arithmetic.qasm"
EXPECTED_BITSTRING = "10000110"  # cout=1, b=000, a=011, cin=0  ->  3 + 5 = 8
NUM_SHOTS = 2000


def _wait_until_finished(backend: SimBackend, job_id: str, timeout: float = 30.0) -> JobStatus:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = backend.get_status(job_id)
        if status in (JobStatus.DONE, JobStatus.ERROR):
            return status
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def _run_on_backend(backend: SimBackend, qasm: str) -> Dict[str, int]:
    backend.start()
    try:
        job_id = backend.submit_job(qasm, num_shots=NUM_SHOTS)
        status = _wait_until_finished(backend, job_id)
        assert status == JobStatus.DONE, f"{backend.name} job ended in {status}"
        return backend.get_result(job_id)
    finally:
        backend.stop(timeout=5.0)


def _success_rate(counts: Dict[str, int], expected: str) -> float:
    return counts.get(expected, 0) / sum(counts.values())


@pytest.fixture(scope="module")
def qasm() -> str:
    return QASM_PATH.read_text()


def test_statevector_backend_is_exact(qasm):
    backend = SimBackend("statevector", StateVectorSimulatorRunner())
    counts = _run_on_backend(backend, qasm)

    # No noise source in this configuration - every shot must land on the
    # arithmetically correct outcome.
    assert counts == {EXPECTED_BITSTRING: NUM_SHOTS}


def test_noisy_statevector_backend_mostly_succeeds(qasm):
    backend = SimBackend("statevector_noisy", NoisyStateVectorSimulatorRunner())
    counts = _run_on_backend(backend, qasm)

    # Depolarizing noise on ~10 two-qubit gates degrades the success rate,
    # but the correct answer must still be the dominant outcome. The
    # threshold is well below the ~0.65-0.70 typically observed, to leave
    # comfortable margin against run-to-run sampling noise.
    assert _success_rate(counts, EXPECTED_BITSTRING) > 0.4
    assert counts[EXPECTED_BITSTRING] == max(counts.values())


def test_noisy_density_matrix_backend_mostly_succeeds(qasm):
    backend = SimBackend("density_matrix_noisy", NoisyDensityMatrixSimulatorRunner())
    counts = _run_on_backend(backend, qasm)

    assert _success_rate(counts, EXPECTED_BITSTRING) > 0.4
    assert counts[EXPECTED_BITSTRING] == max(counts.values())


def test_noise_measurably_degrades_success_rate(qasm):
    """The noiseless backend should outperform both noisy configurations."""
    clean = _run_on_backend(SimBackend("clean", StateVectorSimulatorRunner()), qasm)
    noisy_sv = _run_on_backend(SimBackend("noisy_sv", NoisyStateVectorSimulatorRunner()), qasm)
    noisy_dm = _run_on_backend(
        SimBackend("noisy_dm", NoisyDensityMatrixSimulatorRunner()), qasm
    )

    clean_rate = _success_rate(clean, EXPECTED_BITSTRING)
    noisy_sv_rate = _success_rate(noisy_sv, EXPECTED_BITSTRING)
    noisy_dm_rate = _success_rate(noisy_dm, EXPECTED_BITSTRING)

    assert clean_rate == 1.0
    assert noisy_sv_rate < clean_rate
    assert noisy_dm_rate < clean_rate
