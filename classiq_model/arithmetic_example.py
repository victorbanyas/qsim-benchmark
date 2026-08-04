"""
Model: x |= 3, y |= 5, z |= x + y - all three are deterministic (no
superposition), so the synthesized circuit should produce a single expected
outcome (x=3, y=5, z=8) on every shot in the noiseless case.

Run this once, after installing and authenticating the Classiq SDK:

    pip install classiq
    python -c "from classiq import authenticate; authenticate()"
    python classiq_model/arithmetic_example.py

It synthesizes the model, exports OpenQASM 2.0, writes it to
tests/resources/arithmetic.qasm (overwriting the Qiskit-adder placeholder
used there today), and prints the bitstring you should paste into
EXPECTED_BITSTRING in tests/test_arithmetic_end_to_end.py.

Why a placeholder exists at all: producing this file requires a live,
authenticated call to Classiq's cloud synthesis service. That's not
something that can be done from an offline/sandboxed environment - hence
the interim Qiskit-only circuit already committed, which exercises the same
SimBackend / SimulatorRunner pipeline this file's output will run through.
"""

import sys
from pathlib import Path

from classiq import Output, QNum, TargetLanguage, export, synthesize, authenticate
from classiq.qmod import qfunc

REPO_ROOT = Path(__file__).resolve().parent.parent
QASM_OUT_PATH = REPO_ROOT / "tests" / "resources" / "arithmetic.qasm"


@qfunc
def main(x: Output[QNum], y: Output[QNum], z: Output[QNum]) -> None:
    x |= 3
    y |= 5
    z |= x + y


def synthesize_arithmetic_model() -> None:
    qprog = synthesize(main)
    qasm_str = export(qprog, target_language=TargetLanguage.QASM2)

    QASM_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    QASM_OUT_PATH.write_text(qasm_str)
    print(f"Wrote {QASM_OUT_PATH}")

    # Determine the expected bitstring the same way the test suite will see
    # it - by running the exported QASM through our own noiseless runner -
    # rather than guessing Classiq's internal qubit/bit ordering.
    sys.path.insert(0, str(REPO_ROOT))
    from backend.runners import StateVectorSimulatorRunner

    counts = StateVectorSimulatorRunner().run(qasm_str, num_shots=1000)
    if len(counts) != 1:
        print(
            "WARNING: expected a single deterministic outcome but got "
            f"{len(counts)} distinct bitstrings: {counts}. Double check the "
            "model has no superposition, or update the test to handle "
            "multiple valid outcomes (see the getting-started tutorial's "
            "H(x) variant for why that happens)."
        )
    top_bitstring = max(counts, key=lambda bitstring: counts[bitstring])
    print(f"Expected bitstring for EXPECTED_BITSTRING: {top_bitstring!r}")
    print(f"Counts: {counts}")


if __name__ == "__main__":
    #authenticate()
    synthesize_arithmetic_model()
