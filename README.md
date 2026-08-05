# qsim-benchmark

A benchmarking application for quantum hardware: a simulated multi-backend job
system and a benchmarking engine on top of it.

## Setup & running

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m pytest
```

Run it as `python -m pytest` rather than bare `pytest` — `-m` guarantees the
repo root is importable (so `from backend...` resolves) and doesn't depend
on the `pytest` console script itself being on your PATH, which is a common
source of a "command not found" error even right after `pip install`.
(`pyproject.toml` also sets `pythonpath`, so a bare `pytest` will resolve
imports correctly too if it's on your PATH.)

No Classiq credentials are needed to run the test suite — the arithmetic
tests use an already-committed QASM fixture (see Notes, below). Only
`classiq_model/arithmetic_example.py`, which regenerates that fixture from a
real Classiq model, needs Classiq installed and authenticated.

## 1. Purpose of the system

The assignment is to build a benchmarking application for quantum hardware,
in two layers.

**Part 1** is a job system that mimics a cloud quantum hardware provider: you
submit a QASM circuit and a shot count, and get back a job id you can poll
for status and, once finished, a measurement-counts histogram. Three
backends are required, each simulating a different kind of hardware: an
ideal (noiseless) simulator, a noisy simulator, and a differently-noisy
simulator that needs a different simulation method.

**Part 2** is a benchmarking engine built on top of Part 1: given a quantum
model, an expected outcome, a shot count, and a list of backends, it
synthesizes the model into QASM once, runs it on every requested backend,
and scores each backend by the percentage of shots whose measured outcome
matched the expected result. It has to synthesize once and execute many
times, survive a single backend failing without blocking the others (with a
way to retry just that backend), expose real progress rather than a single
"running" flag, and support several benchmarks running at once.

## 2. The three simulators

All three are built on Qiskit's `AerSimulator` and live under
`backend/runners/` (one file per runner, plus `base.py` for the shared
execution path — parse QASM, transpile, run, collect counts — and
`runner.py` for the `SimulatorRunner` interface itself) — they differ only
in how the `AerSimulator` itself is configured.

- **`StateVectorSimulatorRunner`** — `AerSimulator(method="statevector")`,
  no noise model at all. A state vector is the circuit's exact quantum
  state; with nothing perturbing it, this is the ideal, noiseless baseline —
  every shot should reproduce the arithmetically correct outcome.

- **`NoisyStateVectorSimulatorRunner`** — same state-vector method, with a
  `NoiseModel` applying `depolarizing_error` per gate (0.1% on single-qubit
  gates, 1% on two-qubit gates): after each gate, there's a small
  probability the qubit gets randomized instead of doing what the gate
  intended. It's a memoryless, per-gate error — it only cares that a gate
  happened, not how long it took — so Aer can simulate it by sampling a
  stochastic Kraus channel per shot (i.e. randomly rolling, per shot,
  whether the error fires) while staying in the pure-state formalism.
  That's what keeps it compatible with state-vector simulation rather than
  requiring density-matrix.

- **`NoisyDensityMatrixSimulatorRunner`** — `AerSimulator(method="density_matrix")`,
  with a `NoiseModel` applying `thermal_relaxation_error` (T1 = 50,000ns,
  T2 = 70,000ns, gate durations of 50ns for single-qubit gates and 300ns for
  two-qubit gates). A density matrix generalizes the state vector to also
  represent mixed states (statistical uncertainty about which pure state
  you're in, not just quantum superposition). Thermal relaxation is a
  genuinely different, time-dependent noise mechanism — decay accumulates
  with how long a gate takes, not just with the gate count — and it can
  leave a qubit in exactly such a mixed state. That mixedness is what the
  state-vector method can't represent, which is why this backend needs the
  density-matrix method — satisfying
  the requirement that the two noisy backends use two different noise types
  and, correspondingly, two different simulation methods.

## 3. Design & Architecture

### 3.1 The vision of the complete distributed system

A production version of this system would look like a small set of
cooperating services around one persistent datastore:

- A **jobs table** and a **benchmarks table** in a real database (e.g.
  Postgres), holding the same fields as the `Job` and `Benchmark` records
  described below.
- One **message broker** (e.g. Kafka) with one topic per backend, plus a
  topic for new benchmarks. `submit_job` and `benchmark` would be pure
  producers: write the initial record to the DB, publish a message, return
  immediately.
- Each simulated backend as its **own worker service** (or pool of workers
  in a consumer group) consuming its topic, executing one job at a time (or
  in parallel across workers), and writing every status transition straight
  back to the jobs table.
- The **benchmarking engine** as another service: it consumes the
  benchmarks topic, synthesizes once, and fans out by publishing one message
  per backend onto each backend's own topic — it never talks to a backend
  service directly, only through the shared table and the queues.
- **Durability** falls out of this for free: because state lives in the DB
  and work lives in durably-committed queue offsets, a crashed or restarted
  worker just resumes — in-flight jobs are still sitting in the table
  (or still un-acked on the queue) rather than lost in some process's
  memory.
- **Multiple concurrent benchmarks** are naturally supported, since nothing
  above is keyed by "the one benchmark currently running" — every record and
  every queue message carries its own id.

### 3.2 The actual implementation

None of the above is actually stood up — there's no real Postgres or Kafka
here — but the code is written against two small generic interfaces so that
swapping in real ones wouldn't touch `SimBackend` or `BenchmarkEngine` at
all:

- **`backend/dal/store.py`** defines the `Store[T]` protocol
  (`get`/`put`/`update`), implemented by `in_memory_store.py`'s
  `InMemoryStore` (a dict behind a lock; every read/write goes through
  `copy.deepcopy`, so a caller can never mutate a "persisted" value by
  holding onto a reference) and `sqlite_store.py`'s `SqliteStore` (the same
  protocol backed by a local SQLite file via `sqlite3` + `pickle`, so
  records actually survive a process restart without a real database
  server). `update()` is the atomic read-modify-write primitive in both.
  Which one a `SimBackend`/`BenchmarkEngine` uses is just a constructor
  argument — see `build_system()` below.
- **`backend/dal/queue.py`** defines the `Queue[T]` protocol
  (`publish`/`consume`/`close`) and `InMemoryQueue`, wrapping
  `queue.Queue`. `close()`/`consume()` implement a *broadcast* shutdown:
  the consumer that reads the shutdown sentinel puts it back before
  returning, so every consumer behind it — not just the first one to wake
  up — also sees it and stops. That matters once there's more than one
  worker reading the same queue (see `BenchmarkEngine`, next).
- Both `SimBackend` and `BenchmarkEngine` take their `Store`/`Queue`
  instances as constructor arguments, defaulting to private in-memory ones
  so each still works standalone in isolation (as in most of the tests). To
  get the "shared jobs table" behavior described above, one `Store[Job]` is
  constructed once and passed to every `SimBackend` *and* to
  `BenchmarkEngine`. Because `BenchmarkEngine` reads a `Job`'s status
  straight out of that same store — the identical record each backend's
  worker already flushed its status into — there's no `_refresh` step, no
  polling, and no RPC back into a `SimBackend` to ask "are you done yet."
  `BenchmarkEngine` also takes a `num_workers` argument (default `1`): that
  many threads pull from the same benchmark queue and run the (slow,
  blocking) synthesis step concurrently, so several benchmarks submitted
  around the same time don't queue up behind one another's synthesis call.
  This needed no change to how a benchmark is processed — `queue.Queue`
  already hands each queued id to exactly one consumer, and every state
  change already goes through `Store.update()`'s lock — the only real
  change was the broadcast-shutdown fix above, so `stop()` can still join
  every worker in the pool instead of hanging on all but one of them.
- `SimulatorRunner` (`backend/runners/runner.py`) and `Synthesizer`
  (`backend/synthesizers/synthesizer.py`) are kept as their own small
  `Protocol`s, each split from its implementation(s) the same way — a
  concrete runner is picked with `backend/runners/state_vector.py` etc.,
  and the real, Classiq-backed synthesizer lives in
  `backend/synthesizers/classiq_synthesizer.py` — so `SimBackend` and
  `BenchmarkEngine` are testable with fakes that never touch Qiskit or
  Classiq (see Testing, below).
- **`backend/system.py`** adds `build_system()`: a small factory that
  builds the three standard backends and a `BenchmarkEngine` together,
  sharing one `Store[Job]`, instead of leaving that wiring to be done by
  hand at every call site. `BenchmarkEngine`'s constructor already refuses
  to default that store — passing a mismatched one wouldn't raise
  anything, every status/result read would just look like "job not found,"
  which is a much worse failure mode than a loud error. `build_system()`
  goes one step further than just failing loudly on a mismatch: it removes
  the chance of a mismatch happening at all. It's also where a `SqliteStore`
  would get plugged in for a durable system (`build_system(job_store=SqliteStore("jobs.db"))`).

  That one shared `Store[Job]` is the standard *shared-database*
  integration pattern — it's exactly what lets `BenchmarkEngine` read a
  job's status without polling or calling back into `SimBackend`. It's
  worth naming the pattern's usual cost, too: in a strict
  "each service owns its own data" design, a table more than one component
  reads directly is considered coupling — `BenchmarkEngine` now depends on
  `Job`'s shape, which conceptually belongs to `SimBackend`, so the two
  can't change that record independently without coordinating. That
  tradeoff is acceptable here specifically because `SimBackend` and
  `BenchmarkEngine` aren't independently-deployed services with their own
  release cycles — they're two layers of one system, and `Job` is a
  stable, intentionally shared contract between them, not either side's
  private internal state being reached into.

### 3.3 Models

- **`Job`** (`backend/models/job.py`) — one Part 1 job, and the actual row
  in the shared "jobs table": `id`, `qasm`, `num_shots`, `backend_name`,
  `status` (`QUEUED` / `RUNNING` / `DONE` / `ERROR`), `counts`, `error`, and
  three timestamps. A `SimBackend`'s worker writes every transition here
  directly.
- **`Benchmark`** (`backend/models/benchmark.py`) — one benchmark run: the
  submitted `qmod`, `expected_result`, `num_shots`, the list of requested
  `backend_names`, the synthesis outcome (`synthesis_status`, `qasm` once
  produced, `synthesis_error` if it failed), and one `BackendRun` per
  requested backend.
- **`BackendRun`** — deliberately minimal: just `job_id` and
  `submit_error` (set only if `submit_job()` itself raised before a job
  ever existed). It doesn't cache status, counts, or a score — those are
  always read fresh from the `Job` in the shared store, so there's nothing
  that can go stale. It also doesn't repeat the backend's name, since that's
  already the dict key in `Benchmark.backend_runs`.
- **`BenchmarkStatus`** — what `get_benchmark_status()` returns:
  `synthesis_status` plus a `{backend_name: JobStatus}` map, with
  `completed` / `total` / `is_finished` as computed properties. This is the
  observable-progress piece the assignment calls for — a caller can see
  "2 of 3 backends done" rather than a single opaque "running" flag.

### 3.4 Known tradeoffs and things deliberately left out

A few things were considered and intentionally not built, either because
they'd add complexity this exercise doesn't need, or because they depend
on something not verifiable without more time. Worth naming explicitly
rather than leaving implicit:

- **No worker pool per backend.** Each `SimBackend` still runs jobs on a
  single worker thread, one job at a time, even though `BenchmarkEngine`'s
  synthesis step now supports a pool (above). This is deliberate: a
  backend is dedicated to one `SimulatorRunner` instance, and the design is
  meant to stay generic across whatever `SimulatorRunner` is plugged in —
  there's no guarantee a given simulator (e.g. a shared `AerSimulator`
  instance) tolerates concurrent `.run()` calls from multiple threads
  without checking first. It also happens to be a reasonably faithful
  model of real quantum hardware: a physical QPU is one device that runs
  one circuit at a time, which is why real providers show you a queue
  position rather than running jobs in parallel. If throughput ever needed
  to scale, the intended lever is horizontal, not multi-threading a single
  backend: run multiple `SimBackend` instances — each with its own
  `SimulatorRunner` instance — behind the same job store, the same way
  multiple physical QPUs would sit behind one job queue.
- **No per-job timeouts.** A worker stuck inside `SimulatorRunner.run()`
  would currently block that backend's queue indefinitely. Python can't
  safely, preemptively cancel a running thread, so a timeout can only stop
  *waiting* for the call (e.g. `concurrent.futures.Future.result(timeout=...)`),
  not stop the call itself — the abandoned computation keeps running in
  the background on its own thread until it finishes on its own, freeing
  up the job queue but not the CPU it's still using. Actually reclaiming
  that resource would mean running the simulation in a separate process
  instead of a thread, so it could be forcibly terminated — at the cost of
  the IPC overhead of shipping the QASM in and the result back out across
  a process boundary.
- **No per-key locking in `InMemoryStore`/`SqliteStore`.** Both guard every
  operation with a single lock covering the whole store, so a write to one
  job serializes against reads/writes to every other job, even though
  they're logically unrelated. That's a real throughput ceiling under
  contention that a real database wouldn't have (row-level locking, or
  optimistic concurrency), but at this scale it isn't worth the added
  complexity of sharding locks per key.

## 4. Testing

### 4.1 Sanity tests

`tests/test_sim_backend.py` (6 tests) and `tests/test_benchmark_engine.py`
(9 tests, including the synthesis worker pool) exercise the queueing,
concurrency, error-handling, retry, and not-found paths of `SimBackend` and
`BenchmarkEngine` using fake `SimulatorRunner`/`Synthesizer` test doubles,
so they run fast and don't touch Qiskit, Classiq, or any network.
`tests/test_storage.py` (5 tests) does the same for `SqliteStore`,
including the actual durability claim: writing through one instance,
closing it, and confirming a brand new instance pointed at the same file
sees the same data. `tests/test_system.py` (3 tests) covers `build_system()`
wiring a shared store correctly end to end.

### 4.2 Arithmetic test

Both remaining test files run a real, Classiq-synthesized circuit through
real simulators: the compiled output of `x |= 3; y |= 5; z |= x + y`
(`classiq_model/arithmetic_example.py`), committed as
`tests/resources/arithmetic.qasm`. Classiq's compiler laid the 9 qubits out
as `x = q[0:2]`, `y = q[2:5]`, `z = q[5:9]` (each register
least-significant-qubit-first) and implemented the addition with a
QFT-based adder rather than a ripple-carry one. Reading Qiskit's bitstrings
highest-qubit-first, the expected measurement `"100010111"` decodes as
`z = 1000` (8), `y = 101` (5), `x = 11` (3) — confirmed empirically as the
single outcome on every noise-free shot.

- **`tests/test_arithmetic_end_to_end.py`** runs it directly through each of
  the three real `SimBackend`s.
- **`tests/test_benchmark_engine_arithmetic_end_to_end.py`** runs the same
  circuit through `BenchmarkEngine`, fanning out to all three backends from
  a single `benchmark()` call, and checks both status reporting and scores.

#### 4.2.1 Why the results make sense

- **Noiseless state vector**: every shot lands exactly on `"100010111"` —
  `counts == {EXPECTED_BITSTRING: NUM_SHOTS}`, a benchmark score of `1.0`.
  The circuit is fully deterministic (no superposition) and there's no
  noise source, so there's nothing that could produce any other outcome.
- **Noisy state vector (depolarizing)**: success rate is empirically around
  0.70–0.73; the tests assert `> 0.4` with margin for run-to-run sampling
  noise, and additionally assert the correct bitstring is still the most
  common outcome. Transpiled onto the noisy simulator's basis gates, this
  circuit comes out to roughly 40 two-qubit gates and 60 single-qubit gates
  — more than an order of magnitude more gates than a hand-written
  ripple-carry adder for the same numbers would need, since a QFT-based
  adder trades gate count for avoiding the ripple-carry's linear-depth
  carry chain. Even so, at a 1% two-qubit / 0.1% single-qubit error rate,
  the chance of *some* gate erroring across the whole circuit is
  meaningful, but the correct outcome still stays dominant rather than
  getting washed out into a uniform distribution over the 2⁹ possible
  bitstrings.
- **Noisy density matrix (thermal relaxation)**: success rate is similarly
  around 0.73–0.77, same `> 0.4` threshold. The mechanism is different —
  decay accumulates with how long each gate takes relative to T1/T2, and
  two-qubit gates take 6x longer than single-qubit ones here — but the
  qualitative result is the same: noise measurably erodes fidelity without
  destroying the signal.
- **Both noisy backends score below the noiseless one**, confirmed directly
  by `test_noise_measurably_degrades_success_rate`, which is the sanity
  check that the noise models are actually doing something rather than
  being silently ignored.
- The `BenchmarkEngine`-level results match the backend-level rates, since
  both use the identical scoring formula (`counts[expected] / total`) and
  the identical committed circuit — the benchmark engine is really just
  orchestrating the same three backend runs plus one synthesis step.

### 4.3 Live example

`classiq_model/benchmark_example.py` is a runnable example, not a test: run
it with `python classiq_model/benchmark_example.py` and it prints scores to
stdout. Unlike the tests above, it synthesizes its model against Classiq's
cloud service on every run rather than using a pre-synthesized QASM fixture.

## 5. Notes

**Why the test suite runs against a committed QASM fixture instead of
calling Classiq during test runs.** `classiq_model/arithmetic_example.py`
is the real Classiq model (`x |= 3; y |= 5; z |= x + y`); running it (with
valid Classiq credentials) synthesizes it for real and writes the result to
`tests/resources/arithmetic.qasm`, which is what's committed and what the
arithmetic tests actually run against. What the tests deliberately don't
do is call Classiq live on every run - that's on purpose, not a workaround.

`BenchmarkEngine` and `SimBackend` only ever depend on the
`Synthesizer`/`SimulatorRunner` protocols, never on Classiq or Qiskit
specifics directly - that separation is the whole point of those
interfaces. Testing the actual logic (queueing, status transitions,
retries, scoring, fan-out) shouldn't require live Classiq authentication,
network access, or the platform being up; those are `ClassiqSynthesizer`'s
concern (`backend/synthesizers/classiq_synthesizer.py`), a thin adapter
that any real usage of this system would inject exactly the way the tests
inject `_FixtureSynthesizer` instead. Committing the fixture keeps the
arithmetic tests fast, deterministic, and runnable offline, while still
exercising the *real* output of Classiq's compiler rather than a hand-built
stand-in: `EXPECTED_BITSTRING = "100010111"` is exactly what
`synthesize_arithmetic_model()` produced from the real model, not guessed
or adjusted to make a test pass. Regenerating it, if Classiq's compiler
output ever changes, is just re-running that script with valid credentials
and re-committing the file.

Two integration details worth calling out, both fixed generically in
`backend/runners/base.py` rather than special-cased for this one fixture,
since they apply to any real Classiq-synthesized QASM:

- Classiq's OpenQASM 2 export uses IBM's extended gate-naming convention
  (e.g. `cp` for controlled-phase) that Qiskit's strict-by-default
  `qasm2.loads()` doesn't recognize from `include "qelib1.inc"` alone -
  fixed by passing Qiskit's own `LEGACY_CUSTOM_INSTRUCTIONS` /
  `LEGACY_CUSTOM_CLASSICAL` compatibility sets into `qasm2.loads()`.
- Classiq exports the bare arithmetic unitary with no measurement
  operations - measurement is left to the caller rather than baked into
  the circuit. `AerSimulatorRunner.run()` now calls `circuit.measure_all()`
  itself whenever the parsed circuit has no classical bits of its own, so
  it works whether or not the source QASM measures itself.

One more: `ClassiqSynthesizer` exports explicitly via
`TargetLanguage.QASM2`, since Classiq defaults to OpenQASM 3 but Qiskit's
`qasm2.loads` (used by every `SimulatorRunner`) expects OpenQASM 2.
