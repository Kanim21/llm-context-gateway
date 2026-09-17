import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useRunEvents } from "./useRunEvents";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { getRunSnapshot: vi.fn() },
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
}

describe("useRunEvents", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("does not open an EventSource when the run is already completed", async () => {
    (api.getRunSnapshot as any).mockResolvedValue({ id: "run_1", status: "completed", steps: [] });
    renderHook(() => useRunEvents("run_1"));
    await waitFor(() => expect(api.getRunSnapshot).toHaveBeenCalled());
    expect(FakeEventSource.instances.length).toBe(0);
  });

  it("opens an EventSource and accumulates events when the run is still running", async () => {
    (api.getRunSnapshot as any).mockResolvedValue({ id: "run_1", status: "running", steps: [] });
    const { result } = renderHook(() => useRunEvents("run_1"));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/v1/playbooks/runs/run_1/events");

    source.emit("step_started", { step_index: 0, step_id: "t1" });
    await waitFor(() => expect(result.current.events.length).toBe(1));
    expect(result.current.events[0]).toEqual({ type: "step_started", data: { step_index: 0, step_id: "t1" } });
  });
});
