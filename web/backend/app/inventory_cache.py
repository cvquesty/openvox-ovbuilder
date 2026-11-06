"""In-process TTL cache for live vSphere inventory.

Each key is loaded at most once at a time (concurrent callers wait for the
in-flight load instead of opening their own vCenter session). Results are
fresh for ``ttl`` seconds. When a reload fails, the last good value is served
as *stale* for up to ``stale_ttl`` seconds so the build form keeps working
through a vCenter blip. After a failure, further loads are suppressed for
``error_backoff`` seconds so an unreachable vCenter is not hammered.

The cache is per process; with several uvicorn workers each keeps its own.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, Optional


class InventoryUnavailable(RuntimeError):
    """Live load failed and no usable cached value exists."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = max(0.0, retry_after)


@dataclass
class CacheResult:
    value: Any
    source: str  # "live" | "cache" | "stale"
    age: float


@dataclass
class _Entry:
    value: Any = None
    loaded_at: Optional[float] = None
    error: Optional[str] = None
    failed_at: Optional[float] = None
    loads: int = 0


class InventoryCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._entries: Dict[Hashable, _Entry] = {}
        self._locks: Dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: Hashable) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()

    def get(
        self,
        key: Hashable,
        loader: Callable[[], Any],
        *,
        ttl: float,
        stale_ttl: float,
        error_backoff: float,
        refresh: bool = False,
    ) -> CacheResult:
        lock = self._lock_for(key)
        seen = getattr(self._entries.get(key), "loads", 0)
        with lock:
            entry = self._entries.setdefault(key, _Entry())
            now = self._clock()

            if entry.loaded_at is not None:
                age = now - entry.loaded_at
                # A load that finished while we waited on the lock satisfies
                # a refresh request too.
                if age < ttl and (not refresh or entry.loads > seen):
                    return CacheResult(entry.value, "cache", age)

            if entry.failed_at is not None and now - entry.failed_at < error_backoff:
                return self._fallback(
                    entry,
                    now,
                    stale_ttl=stale_ttl,
                    retry_after=error_backoff - (now - entry.failed_at),
                )

            try:
                value = loader()
            except Exception as exc:
                entry.error = str(exc).strip() or exc.__class__.__name__
                entry.failed_at = self._clock()
                return self._fallback(
                    entry,
                    entry.failed_at,
                    stale_ttl=stale_ttl,
                    retry_after=error_backoff,
                )

            entry.value = value
            entry.loaded_at = self._clock()
            entry.loads += 1
            entry.error = None
            entry.failed_at = None
            return CacheResult(value, "live", 0.0)

    @staticmethod
    def _fallback(
        entry: _Entry, now: float, *, stale_ttl: float, retry_after: float
    ) -> CacheResult:
        if entry.loaded_at is not None:
            age = now - entry.loaded_at
            if age < stale_ttl:
                return CacheResult(entry.value, "stale", age)
        raise InventoryUnavailable(entry.error or "vSphere inventory unavailable", retry_after)
