"""SimulatorRunner: strategy interface that executes one circuit and
returns measurement counts.
"""

from __future__ import annotations

from typing import Dict, Protocol


class SimulatorRunner(Protocol):
    def run(self, qasm: str, num_shots: int) -> Dict[str, int]:
        """Execute `qasm` for `num_shots` shots.

        Returns a histogram over measured bitstrings, e.g.
        {"00": 512, "11": 488}.
        """
        ...
