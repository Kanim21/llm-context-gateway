import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PlaybookEditorPage from "./page";
import { api } from "@/lib/api/client";
import type { PlaybookDetail } from "@/types/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "pb_1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api/client", () => ({
  api: { getPlaybook: vi.fn(), startRun: vi.fn() },
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
    nodes: [{ id: "t1", type: "teammate", data: { role: "Qualifier" } }],
    edges: [],
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
    (api.getPlaybook as any).mockResolvedValue(playbook);
  });

  it("says nothing about unsaved teammates until one is added", async () => {
    render(<PlaybookEditorPage />);
    expect(await screen.findByText("Inbound Sales Triage")).toBeInTheDocument();
    expect(screen.queryByText(/not saved yet/)).not.toBeInTheDocument();
    expect((screen.getByText("Run") as HTMLButtonElement).disabled).toBe(false);
  });

  it("says added teammates are not saved, and blocks Run so it can't skip them", async () => {
    render(<PlaybookEditorPage />);
    await screen.findByText("Inbound Sales Triage");

    await addATeammate();

    expect(screen.getByText(/not saved yet/)).toBeInTheDocument();
    expect((screen.getByText("Run") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("Run"));
    expect(api.startRun).not.toHaveBeenCalled();
  });
});
