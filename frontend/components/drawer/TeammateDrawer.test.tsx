import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TeammateDrawer } from "./TeammateDrawer";

describe("TeammateDrawer", () => {
  it("walks through all 4 steps and calls onSave with the full config", () => {
    const onSave = vi.fn();
    render(<TeammateDrawer onSave={onSave} />);

    // Step 1: Role
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Sales Qualifier" } });
    fireEvent.click(screen.getByText("Next"));

    // Step 2: Objective
    fireEvent.change(screen.getByLabelText("Objective"), { target: { value: "Qualify inbound leads" } });
    fireEvent.click(screen.getByText("Next"));

    // Step 3: Tier
    fireEvent.click(screen.getByLabelText("Brain"));
    fireEvent.click(screen.getByText("Next"));

    // Step 4: Knowledge & Connected Apps
    fireEvent.change(screen.getByLabelText("Knowledge"), { target: { value: "ICP doc" } });
    fireEvent.change(screen.getByLabelText("Connected Apps"), { target: { value: "gmail" } });
    fireEvent.click(screen.getByText("Save"));

    expect(onSave).toHaveBeenCalledWith({
      role: "Sales Qualifier",
      objective: "Qualify inbound leads",
      tier: "brain",
      knowledge: ["ICP doc"],
      connected_apps: ["gmail"],
    });
  });

  it("Back returns to the previous step without losing entered data", () => {
    const onSave = vi.fn();
    render(<TeammateDrawer onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Router" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.click(screen.getByText("Back"));
    expect(screen.getByLabelText("Role")).toHaveValue("Router");
  });
});
