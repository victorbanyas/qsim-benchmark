"""Ideal state-vector simulation - no noise."""

from __future__ import annotations

from qiskit_aer import AerSimulator

from backend.runners.base import AerSimulatorRunner


class StateVectorSimulatorRunner(AerSimulatorRunner):
    def __init__(self):
        super().__init__(AerSimulator(method="statevector"))
