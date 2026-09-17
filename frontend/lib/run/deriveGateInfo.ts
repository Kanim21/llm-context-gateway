import type { PlaybookDetail, RunEvent, RunSnapshot } from "@/types/api";

export interface GateInfo {
  step_index: number;
  step_id: string;
  label: string;
  allow_edit: boolean;
  proposed_output: { text: string };
}

/**
 * Works out whether the run is sitting at an approval gate, and what to show
 * for it.
 *
 * The live `gate_paused` event is used when it's there, but it is NOT the
 * source of truth: the event bus hands each event to one subscriber and then
 * ends the stream, so anyone who reloads the page after the gate fired -- the
 * normal lifecycle of a review that waits for a human -- never sees it. The
 * run snapshot plus the playbook definition carry everything needed, so the
 * gate stays reviewable across reloads.
 */
export function deriveGateInfo(
  snapshot: RunSnapshot | null,
  playbook: PlaybookDetail | null,
  latestEvent: RunEvent | null,
): GateInfo | null {
  if (latestEvent?.type === "gate_paused") return latestEvent.data;
  if (!snapshot || snapshot.status !== "paused" || !playbook) return null;

  const gateStep = snapshot.steps.find((s) => s.status === "awaiting_approval");
  if (!gateStep) return null;

  const gateDef = playbook.definition_json.steps.find((s) => s.step_id === gateStep.step_id);
  if (!gateDef?.gate) return null;

  const priorCompleted = [...snapshot.steps]
    .reverse()
    .find((s) => s.step_index < gateStep.step_index && s.status === "completed");

  return {
    step_index: gateStep.step_index,
    step_id: gateStep.step_id,
    label: gateDef.gate.label,
    allow_edit: gateDef.gate.allow_edit,
    proposed_output: { text: priorCompleted?.output_json?.text ?? "" },
  };
}
