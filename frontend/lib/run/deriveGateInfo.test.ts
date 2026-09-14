import { describe, it, expect } from "vitest";
import { deriveGateInfo } from "./deriveGateInfo";
import type { PlaybookDetail, RunEvent, RunSnapshot } from "@/types/api";

const playbook: PlaybookDetail = {
  id: "pb_1",
  name: "Gated",
  description: "",
  is_template: false,
  created_at: "",
  updated_at: "",
  schema_version: 1,
  definition_json: {
    steps: [
      { step_id: "t1", type: "teammate" },
      { step_id: "g1", type: "approval_gate", gate: { label: "Review before sending", allow_edit: true } },
      { step_id: "t2", type: "teammate" },
    ],
  },
  canvas_json: { nodes: [], edges: [] },
};

const pausedSnapshot: RunSnapshot = {
  id: "run_1",
  playbook_id: "pb_1",
  status: "paused",
  current_step_index: 1,
  input_json: { text: "hi" },
  steps: [
    { step_index: 0, step_id: "t1", status: "completed", output_json: { text: "Draft copy" } },
    { step_index: 1, step_id: "g1", status: "awaiting_approval", output_json: null },
    { step_index: 2, step_id: "t2", status: "pending", output_json: null },
  ],
};

describe("deriveGateInfo", () => {
  it("derives the gate from the snapshot when no SSE event was ever received", () => {
    // This is the reload case: the gate_paused event went to the page load
    // that was open when the gate fired, and is gone by the time we get here.
    const info = deriveGateInfo(pausedSnapshot, playbook, null);
    expect(info).toEqual({
      step_index: 1,
      step_id: "g1",
      label: "Review before sending",
      allow_edit: true,
      proposed_output: { text: "Draft copy" },
    });
  });

  it("prefers the live gate_paused event when one has arrived", () => {
    const event: RunEvent = {
      type: "gate_paused",
      data: {
        step_index: 1, step_id: "g1", label: "Live label",
        allow_edit: false, proposed_output: { text: "Live draft" },
      },
    };
    expect(deriveGateInfo(pausedSnapshot, playbook, event)).toEqual(event.data);
  });

  it("returns nothing for a run that isn't paused", () => {
    const running = { ...pausedSnapshot, status: "running" as const };
    expect(deriveGateInfo(running, playbook, null)).toBeNull();
  });

  it("returns nothing before the snapshot or playbook have loaded", () => {
    expect(deriveGateInfo(null, playbook, null)).toBeNull();
    expect(deriveGateInfo(pausedSnapshot, null, null)).toBeNull();
  });

  it("falls back to empty proposed output when no earlier step completed", () => {
    const noPrior: RunSnapshot = {
      ...pausedSnapshot,
      steps: [
        { step_index: 0, step_id: "g1", status: "awaiting_approval", output_json: null },
      ],
    };
    const gateFirst: PlaybookDetail = {
      ...playbook,
      definition_json: { steps: [playbook.definition_json.steps[1]] },
    };
    expect(deriveGateInfo(noPrior, gateFirst, null)?.proposed_output).toEqual({ text: "" });
  });
});
