"""Synthesizer implementation backed by the Classiq SDK."""

from __future__ import annotations

from classiq import TargetLanguage, export, synthesize
from classiq.synthesis import SerializedModel


class ClassiqSynthesizer:
    """Synthesizes a qmod via Classiq's cloud engine and exports OpenQASM 2.0."""

    def synthesize(self, qmod: str) -> str:
        qprog = synthesize(SerializedModel(qmod))
        return export(qprog, target_language=TargetLanguage.QASM2)
