"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { RunEvent, RunSnapshot } from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";
const EVENT_TYPES: RunEvent["type"][] = [
  "step_started", "token", "step_completed", "gate_paused", "run_completed", "run_rejected", "run_failed",
];

export function useRunEvents(runId: string) {
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    api.getRunSnapshot(runId).then((initialSnapshot) => {
      if (cancelled) return;
      setSnapshot(initialSnapshot);
      if (initialSnapshot.status !== "running" && initialSnapshot.status !== "paused") {
        return;
      }
      source = new EventSource(`${BASE_URL}/v1/playbooks/runs/${runId}/events`);
      for (const type of EVENT_TYPES) {
        source.addEventListener(type, (event) => {
          const data = JSON.parse((event as MessageEvent).data);
          setEvents((prev) => [...prev, { type, data } as RunEvent]);
        });
      }
    });

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [runId]);

  return { snapshot, events, latestEvent: events[events.length - 1] ?? null };
}
