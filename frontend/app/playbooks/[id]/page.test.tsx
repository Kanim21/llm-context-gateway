import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import PlaybookEditorPage from "./page";
import { api } from "@/lib/api/client";
import { useBuilderStore } from "@/lib/store/builderStore";
import type { PlaybookDetail } from "@/types/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "pb_1" }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    getPlaybook: vi.fn(),
    updatePlaybook: vi.fn(),
    startRun: vi.fn(),
  },
}));

// React Flow doesn't render in jsdom; stub the canvas to just reflect the
// store's node ids so we can assert the graph loaded.
vi.mock("@/components/builder/BuilderCanvas", () => ({
  BuilderCanvas: () => {
    const nodes = useBuilderStore((s) => s.nodes);
    return <div data-testid="canvas">{nodes.map((n) => n.id).join(",")}</div>;
  },
}));

// The run hook opens an EventSource + hits the network; stub it.
vi.mock("@/lib/run/useBuilderRun", () => ({
  useBuilderRun: () => ({
    active: false, status: null, lines: [], rawTotal: 0, optimizedTotal: 0,
    reductionPct: null, usdSavedPer1M: 0, gate: null, approve: vi.fn(), reject: vi.fn(),
  }),
}));

const playbook: PlaybookDetail = {
  id: "pb_1", name: "Inbound Sales Triage", description: "", is_template: true,
  created_at: "", updated_at: "", schema_version: 1,
  definition_json: { steps: [{ step_id: "t1", type: "teammate" }] },
  canvas_json: {
    nodes: [
      { id: "start", type: "start", data: {} },
      { id: "t1", type: "teammate", data: { role: "Qualifier", objective: "Qualify the lead", tier: "balanced" } },
      { id: "end", type: "end", data: {} },
    ],
    edges: [
      { source: "start", target: "t1" },
      { source: "t1", target: "end" },
    ],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  useBuilderStore.setState({ nodes: [], edges: [], selectedId: null, runState: {}, savingsByNode: {}, gateHandler: null });
  vi.mocked(api.getPlaybook).mockResolvedValue(playbook);
  vi.mocked(api.updatePlaybook).mockResolvedValue(playbook);
  vi.mocked(api.startRun).mockResolvedValue({
    id: "run_1", playbook_id: "pb_1", status: "running", current_step_index: 0,
    input_json: { text: "" }, steps: [],
  });
});

describe("PlaybookEditorPage (drag-and-drop builder)", () => {
  it("loads the playbook onto the canvas and exposes an enabled Run Playbook control", async () => {
    render(<PlaybookEditorPage />);
    expect(await screen.findByText("Inbound Sales Triage")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("canvas").textContent).toContain("t1"));
    const run = screen.getByText("Run Playbook") as HTMLButtonElement;
    expect(run.disabled).toBe(false); // seeded chain is valid
  });

  it("persists + starts a run when the valid chain's Run Playbook is clicked", async () => {
    render(<PlaybookEditorPage />);
    await screen.findByText("Inbound Sales Triage");
    await waitFor(() => expect(screen.getByTestId("canvas").textContent).toContain("t1"));

    fireEvent.click(screen.getByText("Run Playbook"));

    await waitFor(() => expect(api.updatePlaybook).toHaveBeenCalled());
    const canvasArg = vi.mocked(api.updatePlaybook).mock.calls[0][1].canvas_json as PlaybookDetail["canvas_json"];
    expect(canvasArg.nodes.some((n) => n.type === "teammate" && n.id === "t1")).toBe(true);
    await waitFor(() => expect(api.startRun).toHaveBeenCalledWith("pb_1", ""));
  });

  it("debounced-persists an edited node once the chain stays valid", async () => {
    vi.useFakeTimers();
    try {
      render(<PlaybookEditorPage />);
      // getPlaybook resolves on a microtask; flush it under fake timers.
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });

      act(() => useBuilderStore.getState().updateNodeData("t1", { role: "Senior Qualifier" }));
      vi.mocked(api.updatePlaybook).mockClear();
      act(() => { vi.advanceTimersByTime(900); });

      expect(api.updatePlaybook).toHaveBeenCalledTimes(1);
      const arg = vi.mocked(api.updatePlaybook).mock.calls[0][1].canvas_json as PlaybookDetail["canvas_json"];
      const t1 = arg.nodes.find((n) => n.id === "t1");
      expect((t1!.data as Record<string, unknown>).role).toBe("Senior Qualifier");
    } finally {
      vi.useRealTimers();
    }
  });
});
