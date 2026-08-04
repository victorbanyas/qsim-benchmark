"""Shared AerSimulator execution logic for SimulatorRunner implementations."""

from __future__ import annotations

from typing import Dict

from qiskit import qasm2, transpile
from qiskit_aer import AerSimulator

# Gate names covering the basis set AerSimulator transpiles onto by default.
SINGLE_QUBIT_GATES = ["id", "u1", "u2", "u3", "rz", "sx", "x"]
TWO_QUBIT_GATES = ["cx"]


class AerSimulatorRunner:
    """Parses QASM, transpiles for the target simulator, runs, and collects
    counts. Subclasses just build a (possibly noisy) AerSimulator in
    __init__; this execution path is identical for all of them.
    """

    def __init__(self, simulator: AerSimulator):
        self._simulator = simulator

    def run(self, qasm: str, num_shots: int) -> Dict[str, int]:
        circuit = qasm2.loads(qasm)
        transpiled = transpile(circuit, self._simulator)
        result = self._simulator.run(transpiled, shots=num_shots).result()
        return dict(result.get_counts())
