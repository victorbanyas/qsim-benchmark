"""Working example: run a real Classiq-synthesized model through the full
benchmarking system (build_system()) and print the results.

Model: x |= 6, y |= 2, z ^= x > y - a comparison predicate written into z via 
in-place XOR, the "marking" pattern from
https://docs.classiq.io/user-guide/modeling/quantum-numbers-arithmetics#quantum-arithmetics,
here with fixed inputs (not superposition) so the circuit still has one
deterministic outcome to benchmark against, the same requirement
classiq_model/arithmetic_example.py's model satisfies.

Run this once you've `pip install classiq`d:

    python classiq_model/benchmark_example.py

authenticate() below is safe to call on every run: if this device is
already registered, it just refreshes the token silently (no browser
prompt); it only opens one the very first time.

Job records are persisted to classiq_model/benchmark_example.db via
SqliteStore, rather than the in-memory default build_system() would
otherwise use - so they survive past this process exiting.
"""

import keyring
from keyrings.alt.file import PlaintextKeyring

keyring.set_keyring(PlaintextKeyring())

import sys
from pathlib import Path

from classiq import Output, QBit, QNum, allocate, authenticate, create_model
from classiq.qmod import qfunc

# Running this file directly (`python classiq_model/benchmark_example.py`)
# puts classiq_model/ on sys.path, not the repo root - so `backend` (a
# sibling directory) isn't importable without this
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.dal import SqliteStore  # noqa: E402
from backend.runners import StateVectorSimulatorRunner  # noqa: E402
from backend.synthesizers import ClassiqSynthesizer  # noqa: E402
from backend.system import build_system  # noqa: E402
from backend.utils import wait_for_benchmark  # noqa: E402

DB_PATH = Path(__file__).resolve().parent / "benchmark_example.db"


@qfunc
def main(x: Output[QNum], y: Output[QNum], z: Output[QBit]) -> None:
    allocate(z)
    x |= 6
    y |= 2
    z ^= x > y


def run_benchmark_example() -> None:
    qmod = create_model(main)

    # Synthesize once up front to determine the expected bitstring the same
    # way classiq_model/arithmetic_example.py does - by running the real
    # QASM through the noiseless backend - rather than guessing Classiq's
    # qubit layout. benchmark() below synthesizes `qmod` again internally
    # (that's its job: synthesize once per benchmark, then fan out), so
    # this costs one extra Classiq call in exchange for a script that just
    # works without manually pasting in a bitstring.
    qasm = ClassiqSynthesizer().synthesize(qmod)
    counts = StateVectorSimulatorRunner().run(qasm, num_shots=100)
    if len(counts) != 1:
        raise RuntimeError(
            f"expected a single deterministic outcome, got {counts!r} - "
            "double check the model has no superposition"
        )
    expected_result = next(iter(counts))
    print(f"Expected bitstring: {expected_result!r}")

    job_store = SqliteStore(DB_PATH)
    backends, engine = build_system(job_store=job_store)
    for backend in backends.values():
        backend.start()
    engine.start()

    try:
        benchmark_id = engine.benchmark(
            qmod=qmod,
            expected_result=expected_result,
            backends=list(backends),
            num_shots=1000,
        )

        status = wait_for_benchmark(engine, benchmark_id, timeout=180.0, poll_interval=0.5)
        print(f"Synthesis status: {status.synthesis_status.value}")
        print(f"Backend statuses: {status.backend_statuses}")

        if status.synthesis_status.value == "ERROR":
            raise RuntimeError("synthesis failed - see the benchmark record for details")

        result = engine.get_benchmark_result(benchmark_id)
        print("Benchmark scores:")
        for backend_name, score in result.items():
            print(f"  {backend_name}: {score:.2%}")
    finally:
        engine.stop(timeout=5.0)
        for backend in backends.values():
            backend.stop(timeout=5.0)
        job_store.close()


if __name__ == "__main__":
    authenticate()
    run_benchmark_example()
