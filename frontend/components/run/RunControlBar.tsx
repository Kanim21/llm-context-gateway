import type { RunStatus } from "@/types/api";

const LABEL_BY_STATUS: Record<RunStatus, string> = {
  running: "Running", paused: "Waiting for approval", completed: "Completed",
  failed: "Failed", rejected: "Rejected",
};

const DOT_BY_STATUS: Record<RunStatus, string> = {
  running: "bg-blue-400 animate-pulse",
  paused: "bg-amber-400 animate-pulse",
  completed: "bg-emerald-500",
  failed: "bg-red-500",
  rejected: "bg-zinc-500",
};

const PILL_BY_STATUS: Record<RunStatus, string> = {
  running: "bg-blue-500/10 text-blue-300 ring-blue-500/30",
  paused: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  completed: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  failed: "bg-red-500/10 text-red-300 ring-red-500/30",
  rejected: "bg-zinc-500/10 text-zinc-400 ring-zinc-500/30",
};

export function RunControlBar({ status }: { status: RunStatus }) {
  return (
    <div className="flex items-center border-b border-zinc-800 bg-zinc-950/80 px-4 py-2.5 backdrop-blur">
      <div
        data-testid="run-status-pill"
        className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${PILL_BY_STATUS[status]}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${DOT_BY_STATUS[status]}`} />
        {LABEL_BY_STATUS[status]}
      </div>
    </div>
  );
}
