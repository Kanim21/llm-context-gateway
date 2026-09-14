"""In-process, per-run pub/sub for SSE. One asyncio.Queue per run_id.

Known v1 limitation: there is exactly one queue per run, so each event is
delivered to exactly one subscriber. Two simultaneous viewers of the same
run (two tabs, or a dev-mode double-mount) will steal events from each
other rather than each seeing the full stream. The run page is built to
render from the run snapshot rather than depend on live events for
correctness, so this degrades the live glow, not the ability to act on a
run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

TERMINAL_EVENTS: set[str] = {"run_completed", "run_failed", "gate_paused"}


@dataclass
class EventBus:
    _queues: dict[str, asyncio.Queue] = field(default_factory=dict)

    def _queue_for(self, run_id: str) -> asyncio.Queue:
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
        return self._queues[run_id]

    def queue_count(self) -> int:
        """How many run queues are currently held in memory (for tests)."""
        return len(self._queues)

    def has_queue(self, run_id: str) -> bool:
        return run_id in self._queues

    async def publish(self, run_id: str, event_type: str, data: dict) -> None:
        await self._queue_for(run_id).put((event_type, data))

    async def subscribe(self, run_id: str):
        queue = self._queue_for(run_id)
        try:
            while True:
                event_type, data = await queue.get()
                yield event_type, data
                if event_type in TERMINAL_EVENTS:
                    return
        finally:
            # Drop the queue once this stream ends, otherwise every run a
            # process ever saw (and every buffered token) stays in memory
            # for the lifetime of the process.
            self._queues.pop(run_id, None)
