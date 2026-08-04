"""In-memory Store[T] implementation."""

from __future__ import annotations

import copy
import threading
from typing import Callable, Dict, Generic, Optional, TypeVar

T = TypeVar("T")


class InMemoryStore(Generic[T]):
    """A dict guarded by a lock. Every read/write goes through a deepcopy,
    so callers can never mutate a "persisted" value by holding a reference.
    Doesn't survive a process restart - see SqliteStore for that.
    """

    def __init__(self) -> None:
        self._data: Dict[str, T] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[T]:
        with self._lock:
            value = self._data.get(key)
            return copy.deepcopy(value) if value is not None else None

    def put(self, key: str, value: T) -> None:
        with self._lock:
            self._data[key] = copy.deepcopy(value)

    def update(self, key: str, mutate: Callable[[T], T]) -> T:
        with self._lock:
            current = self._data.get(key)
            if current is None:
                raise KeyError(key)
            new_value = mutate(copy.deepcopy(current))
            self._data[key] = copy.deepcopy(new_value)
            return copy.deepcopy(new_value)
