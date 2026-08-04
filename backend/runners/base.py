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
        # Classiq's OpenQASM 2 export (and other real-world QASM2, generally)
        # assumes IBM's extended qelib1.inc gate names - e.g. `cp` for
        # controlled-phase - which Qiskit's strict-by-default parser doesn't
        # recognize from `include "qelib1.inc"` alone. LEGACY_CUSTOM_*
        # is Qiskit's own compatibility set for exactly this gap.
        circuit = qasm2.loads(
            qasm,
            custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
            custom_classical=qasm2.LEGACY_CUSTOM_CLASSICAL,
        )
        # Classiq exports the bare arithmetic unitary with no measurements -
        # measurement is left to the caller. Add one only if the circuit
        # doesn't already carry its own (e.g. a QASM source that already
        # measures itself, like the earlier Qiskit-built fixture, is left
        # untouched rather than double-measured).
        if circuit.num_clbits == 0:
            circuit.measure_all()
        transpiled = transpile(circuit, self._simulator)
        result = self._simulator.run(transpiled, shots=num_shots).result()
        return dict(result.get_counts())
