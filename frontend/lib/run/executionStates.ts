import type { ExecutionState } from "@/lib/store/canvasStore";
import type { PlaybookDetail, RunEvent, RunSnapshot } from "@/types/api";

const STATUS_TO_EXECUTION_STATE: Record<RunSnapshot["steps"][number]["status"], ExecutionState> = {
  pending: "idle",
  running: "running",
  completed: "done",
  awaiting_approval: "awaiting_approval",
  failed: "failed",
  rejected: "failed",
};

/** The canvas glow for every step, as of the snapshot the page loaded with. */
export function executionStatesFromSnapshot(snapshot: RunSnapshot): Record<string, ExecutionState> {
  const states: Record<string, ExecutionState> = {};
  for (const step of snapshot.steps) {
    states[step.step_id] = STATUS_TO_EXECUTION_STATE[step.status];
  }
  return states;
}

/**
 * The glow change (if any) implied by one live event. step_completed and
 * run_failed only carry a step_index, so the playbook definition is needed to
 * get back to the step_id the canvas keys on.
 */
export function executionStateFromEvent(
  event: RunEvent,
  playbook: PlaybookDetail,
): { stepId: string; state: ExecutionState } | null {
  const stepIdAt = (index: number) => playbook.definition_json.steps[index]?.step_id;

  switch (event.type) {
    case "step_started":
      return { stepId: event.data.step_id, state: "running" };
    case "gate_paused":
      return { stepId: event.data.step_id, state: "awaiting_approval" };
    case "step_completed": {
      const stepId = stepIdAt(event.data.step_index);
      return stepId ? { stepId, state: "done" } : null;
    }
    case "run_failed": {
      const stepId = stepIdAt(event.data.step_index);
      return stepId ? { stepId, state: "failed" } : null;
    }
    default:
      return null;
  }
}
