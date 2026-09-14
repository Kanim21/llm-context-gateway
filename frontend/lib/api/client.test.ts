import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "./client";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("listPlaybooks calls GET /v1/playbooks", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => [] });
    await api.listPlaybooks();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("startRun posts input text to the run endpoint", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => ({ id: "run_1" }) });
    const result = await api.startRun("pb_1", "hello");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks/pb_1/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: { text: "hello" } }),
      }),
    );
    expect(result.id).toBe("run_1");
  });

  it("submitGateDecision posts to the gate endpoint", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => ({ id: "run_1", status: "completed" }) });
    await api.submitGateDecision("run_1", { decision: "approve", step_index: 1 });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks/runs/run_1/gate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "approve", step_index: 1 }),
      }),
    );
  });

  it("throws when the response is not ok", async () => {
    (fetch as any).mockResolvedValue({ ok: false, status: 422, json: async () => ({ errors: [] }) });
    await expect(api.getPlaybook("missing")).rejects.toThrow();
  });
});
