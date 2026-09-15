import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import PlaybookEditorPage from "./page";
import { api } from "@/lib/api/client";
import type { PlaybookDetail } from "@/types/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "pb_1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api/client", () => ({
  api: { getPlaybook: vi.fn(), startRun: vi.fn(), updatePlaybook: vi.fn() },
}));

vi.mock("@/components/canvas/PlaybookCanvas", () => ({
  PlaybookCanvas: ({ nodes }: { nodes: { id: string }[] }) => (
    <div data-testid="canvas">{nodes.map((n) => n.id).join(",")}</div>
  ),
}));

const playbook: PlaybookDetail = {
  id: "pb_1", name: "Inbound Sales Triage", description: "", is_template: true,
  created_at: "", updated_at: "", schema_version: 1,
  definition_json: { steps: [{ step_id: "t1", type: "teammate" }] },
  canvas_json: {
    nodes: [
      { id: "start", type: "start", data: {} },
      { id: "t1", type: "teammate", data: { role: "Qualifier", objective: "O", tier: "speed" } },
      { id: "end", type: "end", data: {} },
    ],
    edges: [
      { source: "start", target: "t1" },
      { source: "t1", target: "end" },
    ],
  },
};

async function addATeammate() {
  fireEvent.click(screen.getByText("Add Teammate"));
  fireEvent.click(screen.getByText("Next")); // role
  fireEvent.click(screen.getByText("Next")); // objective
  fireEvent.click(screen.getByText("Next")); // tier
  fireEvent.click(screen.getByText("Save")); // knowledge & apps
}

describe("PlaybookEditorPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPlaybook).mockResolvedValue(playbook);
  });

  it("renders the saved playbook and enables Run (no unsaved-teammate blocking)", async () => {
    render(<PlaybookEditorPage />);
    expect(await screen.findByText("Inbound Sales Triage")).toBeInTheDocument();
    expect((screen.getByText("Run") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(/not saved yet/)).not.toBeInTheDocument();
  });

  it("persists an added teammate via updatePlaybook (survives reload / included in runs)", async () => {
    vi.mocked(api.updatePlaybook).mockImplementation(async (_id, input) => ({
      ...playbook,
      canvas_json: input.canvas_json as PlaybookDetail["canvas_json"],
    }));

    render(<PlaybookEditorPage />);
    await screen.findByText("Inbound Sales Triage");

    await addATeammate();

    await waitFor(() => expect(api.updatePlaybook).toHaveBeenCalledTimes(1));
    const savedCanvas = vi.mocked(api.updatePlaybook).mock.calls[0][1]
      .canvas_json as PlaybookDetail["canvas_json"];
    const added = savedCanvas.nodes.find((n) => n.type === "teammate" && n.id !== "t1");
    expect(added).toBeTruthy();
    // Drawer-collected fields are carried through (tier defaults to "balanced").
    expect((added!.data as Record<string, unknown>).tier).toBe("balanced");
    // Chain stays linear: exactly one edge into end.
    expect(savedCanvas.edges.filter((e) => e.target === "end")).toHaveLength(1);
  });
});
