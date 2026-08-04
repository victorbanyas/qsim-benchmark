"""Density-matrix simulation with thermal relaxation (T1/T2) noise."""

from __future__ import annotations

from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, thermal_relaxation_error

from backend.runners.base import SINGLE_QUBIT_GATES, TWO_QUBIT_GATES, AerSimulatorRunner


class NoisyDensityMatrixSimulatorRunner(AerSimulatorRunner):
    """Thermal relaxation depends on how long each gate takes relative to
    T1/T2, and can leave qubits in a mixed state - unlike depolarizing
    noise, this requires the density-matrix method. Times are in ns.
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
        noise_model.add_all_qubit_quantum_error(error_1q, SINGLE_QUBIT_GATES)
        noise_model.add_all_qubit_quantum_error(error_2q, TWO_QUBIT_GATES)
        super().__init__(AerSimulator(method="density_matrix", noise_model=noise_model))
