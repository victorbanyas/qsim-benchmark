"""Synthesizer: strategy interface that turns a qmod into QASM."""

from __future__ import annotations

from typing import Protocol


class Synthesizer(Protocol):
    def synthesize(self, qmod: str) -> str:
        """Synthesize `qmod` and return an OpenQASM string."""
        ...
