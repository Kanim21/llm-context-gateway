import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import RunPage from "./page";
import { api } from "@/lib/api/client";
import { useCanvasStore } from "@/lib/store/canvasStore";
import type { PlaybookDetail, RunSnapshot } from "@/types/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "pb_1", runId: "run_1" }),
}));

vi.mock("@/lib/api/client", () => ({
  api: { getRunSnapshot: vi.fn(), getPlaybook: vi.fn(), submitGateDecision: vi.fn() },
}));

// React Flow needs a real layout engine; the canvas itself is covered by its
// own tests, so stub it down to the node ids the page hands it.
vi.mock("@/components/canvas/PlaybookCanvas", () => ({
  PlaybookCanvas: ({ nodes }: { nodes: { id: string }[] }) => (
    <div data-testid="canvas">{nodes.map((n) => n.id).join(",")}</div>
  ),
}));

class SilentEventSource {
  addEventListener() {}
  close() {}
}

const playbook: PlaybookDetail = {
  id: "pb_1", name: "Gated", description: "", is_template: false,
  created_at: "", updated_at: "", schema_version: 1,
  definition_json: {
    steps: [
      { step_id: "t1", type: "teammate" },
      { step_id: "g1", type: "approval_gate", gate: { label: "Review before sending", allow_edit: true } },
      { step_id: "t2", type: "teammate" },
    ],
  },
  canvas_json: {
    nodes: [
      { id: "t1", type: "teammate", data: { role: "Writer" } },
      { id: "g1", type: "approval_gate", data: { label: "Review before sending" } },
      { id: "t2", type: "teammate", data: { role: "Sender" } },
    ],
    edges: [{ source: "t1", target: "g1" }, { source: "g1", target: "t2" }],
  },
};

const pausedSnapshot: RunSnapshot = {
  id: "run_1", playbook_id: "pb_1", status: "paused", current_step_index: 1,
  input_json: { text: "hi" },
  steps: [
    { step_index: 0, step_id: "t1", status: "completed", output_json: { text: "Draft copy" } },
    { step_index: 1, step_id: "g1", status: "awaiting_approval", output_json: null },
    { step_index: 2, step_id: "t2", status: "pending", output_json: null },
  ],
};

describe("RunPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("EventSource", SilentEventSource);
    useCanvasStore.getState().resetExecutionState();
    (api.getPlaybook as any).mockResolvedValue(playbook);
  });

  it("shows the gate review panel for an already-paused run with no live event", async () => {
    // The reload case: gate_paused fired before this page load existed, so no
    // SSE event will ever arrive for it.
    (api.getRunSnapshot as any).mockResolvedValue(pausedSnapshot);

    render(<RunPage />);

    expect(await screen.findByText("Review before sending")).toBeInTheDocument();
    expect(screen.getByText("Approve")).toBeInTheDocument();
    // allow_edit came from the playbook definition, proposed output from the
    // preceding completed step.
    expect(screen.getByLabelText("Edit output")).toHaveValue("Draft copy");
  });

  it("renders the playbook canvas and seeds the execution glow from the snapshot", async () => {
    (api.getRunSnapshot as any).mockResolvedValue({
      ...pausedSnapshot,
      status: "running",
      steps: [
        { step_index: 0, step_id: "t1", status: "completed", output_json: { text: "Draft copy" } },
        { step_index: 1, step_id: "g1", status: "pending", output_json: null },
        { step_index: 2, step_id: "t2", status: "running", output_json: null },
      ],
    });

    render(<RunPage />);

    await waitFor(() => expect(screen.getByTestId("canvas")).toHaveTextContent("t1,g1,t2"));
    await waitFor(() =>
      expect(useCanvasStore.getState().executionStateByStepId).toEqual({
        t1: "done", g1: "idle", t2: "running",
      }),
    );
  });
});
