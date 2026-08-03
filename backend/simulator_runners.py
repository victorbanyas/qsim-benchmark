"""Concrete `SimulatorRunner` implementations backed by Qiskit's AerSimulator.

Three flavors are provided, matching the three Part 1 backends:

- `StateVectorSimulatorRunner`: ideal state-vector simulation, no noise.
- `NoisyStateVectorSimulatorRunner`: state-vector simulation with
  depolarizing gate noise (a memoryless, gate-error style noise model).
- `NoisyDensityMatrixSimulatorRunner`: density-matrix simulation with
  thermal relaxation (T1/T2) noise - a different, time-dependent noise
  mechanism than the depolarizing one above, which is why it needs the
  density-matrix method (it can produce mixed states even from a pure
  input).

All three share the same execution path (parse QASM, transpile for the
target simulator, run, collect counts) via the `_AerSimulatorRunner` base
class, and differ only in how the underlying `AerSimulator` is configured.
"""

from __future__ import annotations

from typing import Dict

from qiskit import qasm2, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, depolarizing_error, thermal_relaxation_error

# Gate names covering the basis set AerSimulator transpiles onto by default.
_SINGLE_QUBIT_GATES = ["id", "u1", "u2", "u3", "rz", "sx", "x"]
_TWO_QUBIT_GATES = ["cx"]


class _AerSimulatorRunner:
    """Shared `SimulatorRunner` execution logic for an AerSimulator instance.

    Subclasses just build a (possibly noisy) `AerSimulator` in `__init__`;
    parsing QASM, transpiling, and extracting counts is identical across
    all of them, so it lives here once.
    """

    def __init__(self, simulator: AerSimulator):
        self._simulator = simulator

    def run(self, qasm: str, num_shots: int) -> Dict[str, int]:
        circuit = qasm2.loads(qasm)
        transpiled = transpile(circuit, self._simulator)
        result = self._simulator.run(transpiled, shots=num_shots).result()
        return dict(result.get_counts())


class StateVectorSimulatorRunner(_AerSimulatorRunner):
    """Ideal state-vector simulation - no noise."""

    def __init__(self):
        super().__init__(AerSimulator(method="statevector"))


class NoisyStateVectorSimulatorRunner(_AerSimulatorRunner):
    """State-vector simulation with depolarizing gate noise.

    Depolarizing error is applied per-gate, independent of circuit timing,
    so it's compatible with the pure-state (statevector) method as long as
    Aer samples the resulting stochastic Kraus channel per-shot.
    """

    def __init__(self, single_qubit_error: float = 0.001, two_qubit_error: float = 0.01):
        noise_model = NoiseModel()
        noise_model.add_all_qubit_quantum_error(
            depolarizing_error(single_qubit_error, 1), _SINGLE_QUBIT_GATES
        )
        noise_model.add_all_qubit_quantum_error(
            depolarizing_error(two_qubit_error, 2), _TWO_QUBIT_GATES
        )
        super().__init__(AerSimulator(method="statevector", noise_model=noise_model))


class NoisyDensityMatrixSimulatorRunner(_AerSimulatorRunner):
    """Density-matrix simulation with thermal relaxation (T1/T2) noise.

    Thermal relaxation error depends on how long each gate takes relative to
    T1/T2, and can leave qubits in a mixed state - so unlike the
    depolarizing noise above, it requires the density-matrix method rather
    than statevector. All times are in nanoseconds.
    """

    def __init__(
        self,
        t1: float = 50_000.0,
        t2: float = 70_000.0,
        single_qubit_gate_time: float = 50.0,
        two_qubit_gate_time: float = 300.0,
    ):
        noise_model = NoiseModel()
        error_1q = thermal_relaxation_error(t1, t2, single_qubit_gate_time)
        error_2q = thermal_relaxation_error(t1, t2, two_qubit_gate_time).tensor(
            thermal_relaxation_error(t1, t2, two_qubit_gate_time)
        )
        noise_model.add_all_qubit_quantum_error(error_1q, _SINGLE_QUBIT_GATES)
        noise_model.add_all_qubit_quantum_error(error_2q, _TWO_QUBIT_GATES)
        super().__init__(AerSimulator(method="density_matrix", noise_model=noise_model))
