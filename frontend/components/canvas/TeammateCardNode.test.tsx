import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TeammateCardNode } from "./TeammateCardNode";
import { useCanvasStore } from "@/lib/store/canvasStore";

// TeammateCardNode renders <Handle> elements, which require a ReactFlow
// store in context even when the node is rendered in isolation.
function renderNode(ui: ReactElement) {
  return render(<ReactFlowProvider>{ui}</ReactFlowProvider>);
}

describe("TeammateCardNode", () => {
  beforeEach(() => {
    useCanvasStore.getState().resetExecutionState();
  });

  it("renders idle by default", () => {
    renderNode(<TeammateCardNode id="t1" data={{ role: "Qualifier", objective: "Qualify leads" }} />);
    expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", "idle");
    expect(screen.getByText("Qualifier")).toBeInTheDocument();
  });

  it("reflects running state from the store", () => {
    useCanvasStore.getState().setExecutionState("t1", "running");
    renderNode(<TeammateCardNode id="t1" data={{ role: "Qualifier", objective: "Qualify leads" }} />);
    expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", "running");
  });

  it("reflects each of the 5 execution states", () => {
    const states: Array<"idle" | "running" | "done" | "awaiting_approval" | "failed"> =
      ["idle", "running", "done", "awaiting_approval", "failed"];
    for (const state of states) {
      useCanvasStore.getState().setExecutionState("t1", state);
      const { unmount } = renderNode(<TeammateCardNode id="t1" data={{ role: "R", objective: "O" }} />);
      expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", state);
      unmount();
    }
  });
});
