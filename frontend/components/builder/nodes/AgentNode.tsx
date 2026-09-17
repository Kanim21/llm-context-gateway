import { Handle, Position, type NodeProps } from "@xyflow/react";
import { modelById } from "@/lib/models/modelRegistry";
import { useBuilderStore, type NodeRunState } from "@/lib/store/builderStore";
import { HANDLE_BASE } from "./handles";

const RING_BY_STATE: Record<NodeRunState, string> = {
  idle: "border-zinc-800",
  running: "border-blue-500 shadow-[0_0_22px_rgba(59,130,246,0.35)] animate-pulse-ring",
  done: "border-emerald-600/70 shadow-[0_0_18px_rgba(16,185,129,0.25)]",
  awaiting_approval: "border-amber-500",
  failed: "border-red-500 shadow-[0_0_18px_rgba(239,68,68,0.3)]",
};

function pct(raw: number, optimized: number): number {
  return raw > 0 ? Math.round(((raw - optimized) / raw) * 100) : 0;
}

export function AgentNode({ id, data, selected }: NodeProps) {
  const d = data as {
    role: string;
    objective: string;
    model: string;
    toolOutputMasking?: boolean;
    promptCacheHashing?: boolean;
    tokenBudgetCap?: number;
  };
  const state = useBuilderStore((s) => s.runState[id] ?? "idle");
  const savings = useBuilderStore((s) => s.savingsByNode[id]);
  const model = modelById(d.model);

  const savedPct = savings ? pct(savings.raw, savings.optimized) : 0;
  const budget = d.tokenBudgetCap ?? 8000;
  const used = savings?.optimized ?? 0;
  const usedFrac = budget > 0 ? Math.min(1, used / budget) : 0;

  let badge = { label: "Idle", cls: "bg-zinc-800 text-zinc-400" };
  if (state === "running") badge = { label: "Running", cls: "bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/30" };
  else if (state === "failed") badge = { label: "Failed", cls: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30" };
  else if (state === "done")
    badge = savings
      ? { label: `Saved ${savedPct}% tokens`, cls: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/40" }
      : { label: "Done", cls: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30" };

  return (
    <div
      data-testid="agent-node"
      data-state={state}
      className={`w-64 rounded-xl border bg-zinc-900 p-3.5 transition-all ${RING_BY_STATE[state]} ${
        selected ? "outline outline-2 outline-emerald-400/70" : "hover:border-zinc-600"
      }`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE_BASE} />

      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-500/15 text-sm ring-1 ring-inset ring-indigo-500/30" aria-hidden>
            🧠
          </span>
          <span className="truncate font-semibold text-zinc-100">{d.role || "Agent"}</span>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${model.pillClass}`} title={`${model.providerLabel} · ${model.label}`}>
          {model.label}
        </span>
      </div>

      <p className="line-clamp-2 min-h-[2.2rem] text-xs text-zinc-400">
        {d.objective?.trim() || "No task set — open the inspector to configure."}
      </p>

      <div className="mt-2.5 flex flex-wrap items-center gap-1">
        {d.toolOutputMasking && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-medium text-zinc-400">tool-mask</span>
        )}
        {d.promptCacheHashing && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-medium text-zinc-400">cache-hash</span>
        )}
        <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-medium text-zinc-400">
          cap {(budget / 1000).toFixed(0)}k
        </span>
      </div>

      {(state === "running" || state === "done") && savings && (
        <div className="mt-2.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
            <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${usedFrac * 100}%` }} />
          </div>
          <p className="mt-1 text-[10px] text-zinc-500">
            {(used / 1000).toFixed(1)}k / {(budget / 1000).toFixed(0)}k budget used
          </p>
        </div>
      )}

      <div className="mt-3 border-t border-zinc-800 pt-2">
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${badge.cls}`}>{badge.label}</span>
      </div>

      <Handle type="source" position={Position.Bottom} className={HANDLE_BASE} />
    </div>
  );
}
