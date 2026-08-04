"""All custom exceptions raised by the backend, in one place."""

from __future__ import annotations


class JobNotFoundError(KeyError):
    """Raised when a job id was never submitted to this backend."""


class JobNotReadyError(RuntimeError):
    """Raised by get_result() when the job hasn't finished (or failed) yet."""


class BenchmarkNotFoundError(KeyError):
    """Raised when a benchmark id was never created."""


class UnknownBackendError(KeyError):
    """Raised when benchmark() is asked to run on a backend name not in the registry."""


class BenchmarkNotReadyError(RuntimeError):
    """Raised when a call needs synthesis to have finished and it hasn't yet."""
