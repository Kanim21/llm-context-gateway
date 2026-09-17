"""In-process, per-run pub/sub for SSE. Live-only: one asyncio.Queue per
*attached subscriber*, created on subscribe and dropped when it leaves.

The bus does not buffer or replay. A run that finishes before anyone
watches, or that nobody ever watches, leaves no queue behind -- that is
what prevents the per-run queue leak (publish never creates a queue). A
late or already-finished subscriber is served a single synthetic frame
from the run snapshot by routes.stream_run_events, not from a buffer.

Known v1 limitation: one queue per run, so two simultaneous viewers of the
same run (two tabs, or a dev-mode double-mount) steal each other's live
events. Correctness comes from the run snapshot, so this degrades the live
glow only, not the ability to act on a run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

TERMINAL_EVENTS: set[str] = {"run_completed", "run_failed", "run_rejected", "gate_paused"}


@dataclass
class EventBus:
    _queues: dict[str, asyncio.Queue] = field(default_factory=dict)

    def queue_count(self) -> int:
        """How many run queues are currently held in memory (for tests)."""
        return len(self._queues)

    def has_queue(self, run_id: str) -> bool:
        return run_id in self._queues

    async def publish(self, run_id: str, event_type: str, data: dict) -> None:
        # Live-only: deliver to the attached subscriber if there is one, and
        # drop otherwise. Never create a queue here -- a queue created by
        # publish for a run nobody subscribes to is exactly the leak.
        queue = self._queues.get(run_id)
        if queue is not None:
            await queue.put((event_type, data))

    async def subscribe(self, run_id: str):
        # The subscriber owns the queue for its lifetime.
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[run_id] = queue
        try:
            while True:
                event_type, data = await queue.get()
                yield event_type, data
                if event_type in TERMINAL_EVENTS:
                    return
        finally:
            # Only drop our own queue -- a second subscriber may have replaced
            # the dict entry, and its own finally will drop that one.
            if self._queues.get(run_id) is queue:
                self._queues.pop(run_id, None)
