"""Tests for the per-run asyncio.Queue-based SSE event bus."""

from __future__ import annotations

import asyncio

import pytest

from agent_gateway.orchestrator.events import EventBus


class TestEventBus:
    async def test_subscribe_receives_published_event(self):
        bus = EventBus()

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})

        asyncio.create_task(publisher())

        received = []
        async for event_type, data in bus.subscribe("run_1"):
            received.append((event_type, data))
            break

        assert received == [("step_started", {"step_index": 0})]

    async def test_subscribe_stops_iterating_after_terminal_event(self):
        bus = EventBus()
        await bus.publish("run_1", "step_started", {"step_index": 0})
        await bus.publish("run_1", "run_completed", {"status": "completed"})
        await bus.publish("run_1", "step_started", {"step_index": 99})  # must never be yielded

        events = [event async for event in bus.subscribe("run_1")]

        assert [e[0] for e in events] == ["step_started", "run_completed"]

    async def test_different_run_ids_are_isolated(self):
        bus = EventBus()
        await bus.publish("run_a", "run_completed", {})
        events = []
        async for event_type, data in bus.subscribe("run_a"):
            events.append(event_type)
        assert events == ["run_completed"]
