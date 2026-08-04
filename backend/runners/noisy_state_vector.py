"""State-vector simulation with depolarizing gate noise."""

from __future__ import annotations

from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, depolarizing_error

from backend.runners.base import SINGLE_QUBIT_GATES, TWO_QUBIT_GATES, AerSimulatorRunner


class NoisyStateVectorSimulatorRunner(AerSimulatorRunner):
    """Depolarizing error is applied per-gate, independent of circuit
    timing, so it stays compatible with the pure-state (statevector)
    method - Aer samples the resulting stochastic Kraus channel per shot.
    """

    def __init__(self, single_qubit_error: float = 0.001, two_qubit_error: float = 0.01):
        noise_model = NoiseModel()
        noise_model.add_all_qubit_quantum_error(
            depolarizing_error(single_qubit_error, 1), SINGLE_QUBIT_GATES
        )
        noise_model.add_all_qubit_quantum_error(
            depolarizing_error(two_qubit_error, 2), TWO_QUBIT_GATES
        )
        super().__init__(AerSimulator(method="statevector", noise_model=noise_model))
