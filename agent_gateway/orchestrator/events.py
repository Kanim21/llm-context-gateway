"""In-process, per-run pub/sub for SSE. One asyncio.Queue per run_id."""

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

    async def publish(self, run_id: str, event_type: str, data: dict) -> None:
        await self._queue_for(run_id).put((event_type, data))

    async def subscribe(self, run_id: str):
        queue = self._queue_for(run_id)
        while True:
            event_type, data = await queue.get()
            yield event_type, data
            if event_type in TERMINAL_EVENTS:
                return
