"""SqliteStore[T]: a Store[T] backed by a local SQLite file - real, on-disk
durability without running a database server.
"""

from __future__ import annotations

import pickle
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Generic, Optional, TypeVar, Union

T = TypeVar("T")


class SqliteStore(Generic[T]):
    """Records are stored as (key, value) rows with `value` pickled, so
    dataclasses round-trip with no custom (de)serialization code. Every
    operation is guarded by a lock, same as InMemoryStore, since a sqlite3
    connection isn't safe to share across threads otherwise.
    """

    def __init__(self, path: Union[str, Path], table: str = "store") -> None:
        self._table = table
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} "
                "(key TEXT PRIMARY KEY, value BLOB NOT NULL)"
            )
            self._conn.commit()

    def get(self, key: str) -> Optional[T]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT value FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
        return pickle.loads(row[0]) if row is not None else None

    def put(self, key: str, value: T) -> None:
        blob = pickle.dumps(value)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {self._table} (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, blob),
            )
            self._conn.commit()

    def update(self, key: str, mutate: Callable[[T], T]) -> T:
        with self._lock:
            row = self._conn.execute(
                f"SELECT value FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(key)
            new_value = mutate(pickle.loads(row[0]))
            self._conn.execute(
                f"UPDATE {self._table} SET value = ? WHERE key = ?",
                (pickle.dumps(new_value), key),
            )
            self._conn.commit()
            return new_value

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()
