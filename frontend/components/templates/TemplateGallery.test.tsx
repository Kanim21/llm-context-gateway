import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TemplateGallery } from "./TemplateGallery";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { listPlaybooks: vi.fn() },
}));

describe("TemplateGallery", () => {
  it("renders only playbooks flagged is_template, and calls onSelect on click", async () => {
    (api.listPlaybooks as any).mockResolvedValue([
      { id: "tpl_1", name: "Inbound Sales Triage", description: "d", is_template: true },
      { id: "pb_custom", name: "My Custom Playbook", description: "d", is_template: false },
    ]);
    const onSelect = vi.fn();
    render(<TemplateGallery onSelect={onSelect} />);

    await waitFor(() => expect(screen.getByText("Inbound Sales Triage")).toBeInTheDocument());
    expect(screen.queryByText("My Custom Playbook")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Inbound Sales Triage"));
    expect(onSelect).toHaveBeenCalledWith("tpl_1");
  });
});
