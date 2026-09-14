import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GateReviewPanel } from "./GateReviewPanel";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { submitGateDecision: vi.fn() },
}));

describe("GateReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.submitGateDecision as any).mockResolvedValue({ id: "run_1", status: "completed" });
  });

  it("Approve calls submitGateDecision with decision approve", async () => {
    const onDecided = vi.fn();
    render(<GateReviewPanel runId="run_1" label="Review before sending" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={onDecided} />);
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith("run_1", { decision: "approve" }));
    expect(onDecided).toHaveBeenCalled();
  });

  it("Reject calls submitGateDecision with decision reject", async () => {
    render(<GateReviewPanel runId="run_1" label="Review" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={vi.fn()} />);
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith("run_1", { decision: "reject" }));
  });

  it("only submits once when Approve is double-clicked", async () => {
    let resolveDecision: (value: unknown) => void = () => {};
    (api.submitGateDecision as any).mockImplementation(
      () => new Promise((resolve) => { resolveDecision = resolve; }),
    );
    render(<GateReviewPanel runId="run_1" label="Review" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={vi.fn()} />);
    const approve = screen.getByText("Approve");
    fireEvent.click(approve);
    fireEvent.click(approve);
    await waitFor(() => expect((screen.getByText("Reject") as HTMLButtonElement).disabled).toBe(true));
    expect(api.submitGateDecision).toHaveBeenCalledTimes(1);
    resolveDecision({ id: "run_1", status: "completed" });
  });

  it("Edit submits the edited text", async () => {
    render(<GateReviewPanel runId="run_1" label="Review" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Edit output"), { target: { value: "Edited draft" } });
    fireEvent.click(screen.getByText("Save Edit"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith(
      "run_1", { decision: "edit", edited_output: { text: "Edited draft" } },
    ));
  });
});
