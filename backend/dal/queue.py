"""Queue[T]: message-queue interface standing in for a real broker
(Kafka, SQS, RabbitMQ...).
"""

from __future__ import annotations

import queue as _queue
from typing import Generic, Optional, Protocol, TypeVar

T = TypeVar("T")


class Queue(Protocol[T]):
    """publish() is fire-and-forget. consume() blocks until work is
    available and returns None once the queue is closed and drained - the
    signal a worker loop uses to stop:
    `while (item := q.consume()) is not None: ...`. close() must reach
    every concurrent consumer, not just the first one to see it.
    """

    def publish(self, item: T) -> None: ...

    def consume(self) -> Optional[T]: ...

    def close(self) -> None:
        """Signal shutdown; every consume() call, blocked or future,
        eventually returns None."""
        ...


class InMemoryQueue(Generic[T]):
    """Wraps stdlib queue.Queue. Stands in for a Kafka topic / consumer
    group.
    """

    _SENTINEL = object()

    def __init__(self) -> None:
        self._queue: "_queue.Queue[object]" = _queue.Queue()

    def publish(self, item: T) -> None:
        self._queue.put(item)

    def consume(self) -> Optional[T]:
        item = self._queue.get()
        if item is self._SENTINEL:
            # Re-publish so the next consumer sees it too - turns one
            # sentinel into a shutdown signal for the whole worker pool.
            self._queue.put(self._SENTINEL)
            return None
        return item  # type: ignore[return-value]

    def close(self) -> None:
        self._queue.put(self._SENTINEL)
