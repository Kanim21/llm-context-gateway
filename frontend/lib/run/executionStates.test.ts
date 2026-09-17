import { describe, it, expect } from "vitest";
import { executionStateFromEvent, executionStatesFromSnapshot } from "./executionStates";
import type { PlaybookDetail, RunSnapshot } from "@/types/api";

const playbook: PlaybookDetail = {
  id: "pb_1", name: "P", description: "", is_template: false,
  created_at: "", updated_at: "", schema_version: 1,
  definition_json: {
    steps: [
      { step_id: "t1", type: "teammate" },
      { step_id: "g1", type: "approval_gate", gate: { label: "Review", allow_edit: true } },
    ],
  },
  canvas_json: { nodes: [], edges: [] },
};

const snapshot: RunSnapshot = {
  id: "run_1", playbook_id: "pb_1", status: "running", current_step_index: 1,
  input_json: { text: "hi" },
  steps: [
    { step_index: 0, step_id: "t1", status: "completed", output_json: { text: "out" } },
    { step_index: 1, step_id: "g1", status: "running", output_json: null },
    { step_index: 2, step_id: "t2", status: "pending", output_json: null },
    { step_index: 3, step_id: "t3", status: "rejected", output_json: null },
  ],
};

describe("executionStatesFromSnapshot", () => {
  it("maps every step status onto a canvas execution state", () => {
    expect(executionStatesFromSnapshot(snapshot)).toEqual({
      t1: "done", g1: "running", t2: "idle", t3: "failed",
    });
  });
});

describe("executionStateFromEvent", () => {
  it("uses the step_id carried by step_started and gate_paused", () => {
    expect(executionStateFromEvent(
      { type: "step_started", data: { step_index: 0, step_id: "t1" } }, playbook,
    )).toEqual({ stepId: "t1", state: "running" });

    expect(executionStateFromEvent(
      { type: "gate_paused", data: { step_index: 1, step_id: "g1", label: "Review", allow_edit: true, proposed_output: { text: "" } } },
      playbook,
    )).toEqual({ stepId: "g1", state: "awaiting_approval" });
  });

  it("resolves step_index to a step_id for the events that omit it", () => {
    expect(executionStateFromEvent(
      { type: "step_completed", data: { step_index: 0, output: { text: "out" } } }, playbook,
    )).toEqual({ stepId: "t1", state: "done" });

    expect(executionStateFromEvent(
      { type: "run_failed", data: { step_index: 1, error: "boom" } }, playbook,
    )).toEqual({ stepId: "g1", state: "failed" });
  });

  it("ignores events that say nothing about a step's state", () => {
    expect(executionStateFromEvent(
      { type: "token", data: { step_index: 0, token: "a" } }, playbook,
    )).toBeNull();
    expect(executionStateFromEvent({ type: "run_completed", data: {} }, playbook)).toBeNull();
    expect(executionStateFromEvent(
      { type: "step_completed", data: { step_index: 99, output: { text: "" } } }, playbook,
    )).toBeNull();
  });
});
