"""Store[T]: key-value persistence interface for one record type."""

from __future__ import annotations

from typing import Callable, Optional, Protocol, TypeVar

T = TypeVar("T")


class Store(Protocol[T]):
    """get()/update() return independent copies, not live references - a
    caller can't mutate a stored value just by holding onto what it got
    back.
    """

    def get(self, key: str) -> Optional[T]:
        """Return a copy of the stored value, or None if `key` doesn't exist."""
        ...

    def put(self, key: str, value: T) -> None:
        """Insert or overwrite the value stored at `key`."""
        ...

    def update(self, key: str, mutate: Callable[[T], T]) -> T:
        """Atomically read the current value, apply `mutate`, store and
        return the result. Raises KeyError if `key` doesn't exist yet.
        """
        ...
