"""Windowed counters, mirroring rules_atproto's IncrementWindow.

The atproto plugin backs these with memcached because Bluesky runs many rules
workers. This MVP runs a single worker (OSPREY_RULES_SINK_NUM_WORKERS=1), so
an in-process store keeps the stack to one fewer container.

That is a real limitation, not an oversight:

  * counts are per-worker-process -- scale past one worker and every rate limit
    silently becomes N times looser
  * counts reset when the worker restarts

Both are fine for testing guardrail logic and wrong for production. Swapping in
memcached means replacing `_STORE` below with a HashClient; the UDF signature
is deliberately identical to the atproto one so the rules do not change.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from osprey.engine.executor.execution_context import ExecutionContext
from osprey.engine.stdlib.udfs.categories import UdfCategories
from osprey.engine.udf.arguments import ArgumentsBase
from osprey.engine.udf.base import UDFBase

# key -> list of event timestamps (monotonic seconds)
_STORE: Dict[str, List[float]] = defaultdict(list)
_LOCK = threading.Lock()

# Stop unbounded growth from keys that are never touched again (every session id
# is unique, so without this the dict grows forever).
_MAX_KEYS = 50_000


def _prune(bucket: List[float], cutoff: float) -> List[float]:
    """Drop timestamps older than the window. Lists are tiny, so a rebuild is
    cheaper than in-place deletion."""
    return [ts for ts in bucket if ts >= cutoff]


def _evict_if_needed(now: float) -> None:
    """Drop keys whose newest entry is over an hour old."""
    if len(_STORE) <= _MAX_KEYS:
        return
    stale_cutoff = now - 3600
    for key in [k for k, v in _STORE.items() if not v or max(v) < stale_cutoff]:
        _STORE.pop(key, None)


class IncrementWindowArguments(ArgumentsBase):
    key: str
    window_seconds: float
    when_all: List[bool]
    """Only increment when every condition holds. Matches the atproto UDF, and
    lets a rule express 'count this, but only for bookings'."""


class IncrementWindow(UDFBase[IncrementWindowArguments, int]):
    """Increment the counter for `key` and return the count within the window.

    Increments on *attempts*, including ones other rules go on to deny. That is
    intentional: an agent stuck retrying a rejected booking is precisely the
    behaviour the rate limit exists to catch.
    """

    category = UdfCategories.ENGINE

    def execute(self, execution_context: ExecutionContext, arguments: IncrementWindowArguments) -> int:
        now = time.monotonic()
        cutoff = now - arguments.window_seconds

        with _LOCK:
            bucket = _prune(_STORE[arguments.key], cutoff)
            if all(arguments.when_all):
                bucket.append(now)
            _STORE[arguments.key] = bucket
            _evict_if_needed(now)
            return len(bucket)


class GetWindowCountArguments(ArgumentsBase):
    key: str
    window_seconds: float


class GetWindowCount(UDFBase[GetWindowCountArguments, int]):
    """Read a window counter without incrementing it."""

    category = UdfCategories.ENGINE

    def execute(self, execution_context: ExecutionContext, arguments: GetWindowCountArguments) -> int:
        now = time.monotonic()
        cutoff = now - arguments.window_seconds

        with _LOCK:
            bucket = _prune(_STORE.get(arguments.key, []), cutoff)
            _STORE[arguments.key] = bucket
            return len(bucket)


def reset_windows() -> Tuple[int, int]:
    """Clear all counters. Used by the test suite between scenarios so a rate
    limit tripped in one test does not leak into the next."""
    with _LOCK:
        keys = len(_STORE)
        events = sum(len(v) for v in _STORE.values())
        _STORE.clear()
        return keys, events
