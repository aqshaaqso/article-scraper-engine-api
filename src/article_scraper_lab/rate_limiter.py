"""Serialize requests per domain and apply a polite delay."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager


class DomainRateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self._delay = delay_seconds
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last_finished: dict[str, float] = {}
        self._domain_delays: dict[str, float] = {}

    def set_min_delay(self, domain: str, delay_seconds: float | None) -> None:
        if delay_seconds is None or delay_seconds <= 0:
            return
        with self._guard:
            current = self._domain_delays.get(domain, self._delay)
            self._domain_delays[domain] = max(current, delay_seconds)

    @contextmanager
    def limit(self, domain: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(domain, threading.Lock())
        with lock:
            with self._guard:
                delay = self._domain_delays.get(domain, self._delay)
                wait = delay - (time.monotonic() - self._last_finished.get(domain, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                with self._guard:
                    self._last_finished[domain] = time.monotonic()
