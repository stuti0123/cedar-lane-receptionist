"""Keeps conversations in memory, but never forever: idle ones expire and the total is capped,
so a long-running server can't slowly fill its memory."""
import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class SessionStore(Generic[T]):
    def __init__(self, factory: Callable[[str], T], ttl_seconds: float = 3600, max_sessions: int = 500,
                 clock: Callable[[], float] = time.monotonic):
        self._factory, self._ttl, self._max, self._clock = factory, ttl_seconds, max_sessions, clock
        self._items: dict[str, tuple[float, T]] = {}  # id -> (last used, session)
        self._lock = threading.Lock()

    def get(self, session_id: str) -> T:
        """Return the existing conversation, or start a new one. Touching it keeps it alive."""
        with self._lock:
            now = self._clock()
            self._evict(now)
            entry = self._items.get(session_id)
            session = entry[1] if entry else self._factory(session_id)
            self._items[session_id] = (now, session)
            return session

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._items)

    def _evict(self, now: float) -> None:
        for sid in [s for s, (seen, _) in self._items.items() if now - seen > self._ttl]:
            del self._items[sid]
        while len(self._items) >= self._max:  # still full: drop the least recently used
            del self._items[min(self._items, key=lambda s: self._items[s][0])]
