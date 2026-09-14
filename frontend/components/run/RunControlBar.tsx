import type { RunStatus } from "@/types/api";

const LABEL_BY_STATUS: Record<RunStatus, string> = {
  running: "Running", paused: "Waiting for approval", completed: "Completed",
  failed: "Failed", rejected: "Rejected",
};

export function RunControlBar({ status }: { status: RunStatus }) {
  return <div data-testid="run-status-pill">{LABEL_BY_STATUS[status]}</div>;
}
