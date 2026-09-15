"""Tests for the per-run asyncio.Queue-based SSE event bus (live-only)."""

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

        async def publisher():
            # Publish only once a subscriber is attached: the bus is live-only
            # and does not buffer events for a run nobody is watching yet.
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})
            await bus.publish("run_1", "run_completed", {"status": "completed"})
            await bus.publish("run_1", "step_started", {"step_index": 99})  # must never be yielded

        asyncio.create_task(publisher())

        events = [event async for event in bus.subscribe("run_1")]

        assert [e[0] for e in events] == ["step_started", "run_completed"]

    async def test_different_run_ids_are_isolated(self):
        bus = EventBus()

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_a", "run_completed", {})

        asyncio.create_task(publisher())

        events = []
        async for event_type, data in bus.subscribe("run_a"):
            events.append(event_type)
        assert events == ["run_completed"]

    async def test_publish_with_no_subscriber_is_dropped_and_leaks_nothing(self):
        """The leak fix: a run that publishes to completion while nobody is
        watching must leave no queue behind. Previously publish created a
        queue that was only ever freed by a subscriber that might never come."""
        bus = EventBus()
        await bus.publish("run_1", "step_started", {"step_index": 0})
        await bus.publish("run_1", "run_completed", {})
        assert not bus.has_queue("run_1")
        assert bus.queue_count() == 0

    async def test_queue_is_released_when_the_subscriber_goes_away_early(self):
        bus = EventBus()
        stream = bus.subscribe("run_1")

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})

        asyncio.create_task(publisher())

        async for _ in stream:
            break
        await stream.aclose()
        assert bus.queue_count() == 0

    async def test_run_rejected_is_a_terminal_event(self):
        bus = EventBus()

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "run_rejected", {"step_index": 1})
            await bus.publish("run_1", "step_started", {"step_index": 99})  # never yielded

        asyncio.create_task(publisher())

        events = [e[0] async for e in bus.subscribe("run_1")]
        assert events == ["run_rejected"]
        assert bus.queue_count() == 0
