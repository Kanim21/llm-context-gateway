// Hand-authored to match agent_gateway/orchestrator/routes.py's 7 endpoints.
// Regenerate from the live backend once it's running:
//   npx openapi-typescript http://localhost:8080/openapi.json -o types/api.ts

export type Tier = "speed" | "balanced" | "brain";

export interface TeammateConfig {
  role: string;
  objective: string;
  tier: Tier;
  connected_apps: string[];
  knowledge: string[];
}

export interface GateConfig {
  label: string;
  allow_edit: boolean;
}

export interface PlaybookStep {
  step_id: string;
  type: "teammate" | "approval_gate";
  teammate?: TeammateConfig;
  gate?: GateConfig;
}

export interface PlaybookSummary {
  id: string;
  name: string;
  description: string;
  is_template: boolean;
  created_at: string;
  updated_at: string;
}

export interface PlaybookDetail extends PlaybookSummary {
  schema_version: number;
  definition_json: { steps: PlaybookStep[] };
  canvas_json: { nodes: unknown[]; edges: unknown[] };
}

export type RunStatus = "running" | "paused" | "completed" | "failed" | "rejected";

export interface StepSnapshot {
  step_index: number;
  step_id: string;
  status: "pending" | "running" | "completed" | "awaiting_approval" | "failed" | "rejected";
  // A failed step records { error: "..." } instead of { text: "..." }.
  output_json: { text?: string; error?: string } | null;
}

export interface RunSnapshot {
  id: string;
  playbook_id: string;
  status: RunStatus;
  current_step_index: number;
  input_json: { text: string };
  steps: StepSnapshot[];
}

export type RunEvent =
  | { type: "step_started"; data: { step_index: number; step_id: string } }
  | { type: "token"; data: { step_index: number; token: string } }
  | { type: "step_completed"; data: { step_index: number; output: { text: string } } }
  | { type: "gate_paused"; data: { step_index: number; step_id: string; label: string; allow_edit: boolean; proposed_output: { text: string } } }
  | { type: "run_completed"; data: Record<string, never> }
  | { type: "run_failed"; data: { step_index: number; error: string } };

export interface GateDecision {
  decision: "approve" | "edit" | "reject";
  edited_output?: { text: string };
}
