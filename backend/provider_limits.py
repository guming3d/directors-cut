"""Per-provider caps on concurrent video generation tasks.

Each provider limits how many tasks one account may run at once (see the
*_MAX_CONCURRENT_TASKS settings); extra submissions are rejected with HTTP 429.
A slot is held while a task is submitted and rendering, so parallel scenes and
jobs queue here instead of failing. The caps are per process: other processes
sharing the account (cloud replicas, a local backend) can still collide, which
each client handles by retrying 429 responses.
"""

from __future__ import annotations

import asyncio
import weakref

from .config import (
    JIMENG_MAX_CONCURRENT_TASKS,
    KLING_MAX_CONCURRENT_TASKS,
    SEEDANCE_MAX_CONCURRENT_TASKS,
    SORA_MAX_CONCURRENT_TASKS,
    normalize_video_provider,
)

PROVIDER_CONCURRENCY: dict[str, int] = {
    "sora2": SORA_MAX_CONCURRENT_TASKS,
    "kling": KLING_MAX_CONCURRENT_TASKS,
    "jimeng": JIMENG_MAX_CONCURRENT_TASKS,
    "seedance": SEEDANCE_MAX_CONCURRENT_TASKS,
}

# asyncio primitives belong to one event loop, so keep one set per loop.
_SLOTS_BY_LOOP: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)


def provider_slot(provider: str) -> asyncio.Semaphore:
    """Semaphore to hold while a task for ``provider`` is submitted and rendering."""

    name = normalize_video_provider(provider)
    loop = asyncio.get_running_loop()
    slots = _SLOTS_BY_LOOP.setdefault(loop, {})
    if name not in slots:
        slots[name] = asyncio.Semaphore(PROVIDER_CONCURRENCY[name])
    return slots[name]


__all__ = ["PROVIDER_CONCURRENCY", "provider_slot"]
