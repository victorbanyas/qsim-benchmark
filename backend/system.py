"""Builds a matched set of SimBackends and a BenchmarkEngine sharing one
Store[Job], ready to start.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from backend.benchmark_engine import BenchmarkEngine
from backend.models import Job
from backend.dal import InMemoryStore, Store
from backend.sim_backend import SimBackend, SimulatorRunner
from backend.runners import (
    NoisyDensityMatrixSimulatorRunner,
    NoisyStateVectorSimulatorRunner,
    StateVectorSimulatorRunner,
)
from backend.synthesizers import ClassiqSynthesizer, Synthesizer


def _default_runners() -> Dict[str, SimulatorRunner]:
    """The three standard simulator backends."""
    return {
        "statevector": StateVectorSimulatorRunner(),
        "statevector_noisy": NoisyStateVectorSimulatorRunner(),
        "density_matrix_noisy": NoisyDensityMatrixSimulatorRunner(),
    }


def build_system(
    runners: Optional[Dict[str, SimulatorRunner]] = None,
    synthesizer: Optional[Synthesizer] = None,
    job_store: Optional[Store[Job]] = None,
    num_synthesis_workers: int = 1,
) -> Tuple[Dict[str, SimBackend], BenchmarkEngine]:
    """`runners` defaults to the three standard backends; pass your own
    dict (e.g. of fakes) for testing. `job_store` defaults to a fresh
    InMemoryStore - pass a SqliteStore(path) for job records that survive
    a restart.

    Nothing is started: call .start() on every backend, then on the
    engine, before submitting anything, and .stop() on both when done.
    """
    runners = runners if runners is not None else _default_runners()
    job_store = job_store or InMemoryStore()

    backends = {
        name: SimBackend(name, runner, job_store=job_store)
        for name, runner in runners.items()
    }
    engine = BenchmarkEngine(
        backends,
        synthesizer or ClassiqSynthesizer(),
        job_store=job_store,
        num_workers=num_synthesis_workers,
    )
    return backends, engine
