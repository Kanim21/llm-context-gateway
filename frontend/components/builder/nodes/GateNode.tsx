import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useBuilderStore } from "@/lib/store/builderStore";
import { HANDLE_BASE } from "./handles";

export function GateNode({ id, data, selected }: NodeProps) {
  const d = data as { label: string };
  const state = useBuilderStore((s) => s.runState[id] ?? "idle");
  const gateHandler = useBuilderStore((s) => s.gateHandler);
  const awaiting = state === "awaiting_approval";

  return (
    <div
      data-testid="gate-node"
      data-state={state}
      className={`w-64 rounded-xl border border-dashed bg-amber-950/25 p-3.5 transition-all ${
        awaiting ? "border-amber-400 shadow-[0_0_22px_rgba(251,191,36,0.35)] animate-pulse-ring" : "border-amber-800/60"
      } ${selected ? "outline outline-2 outline-emerald-400/70" : ""}`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE_BASE} />

      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300 ring-1 ring-inset ring-amber-500/40">
        ⚠ Approval Gate
      </span>
      <div className="mt-2 font-semibold text-zinc-100">{d.label || "Approval gate"}</div>

      {awaiting && (
        <div className="mt-3 flex gap-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              gateHandler?.("approve");
            }}
            className="flex-1 rounded-md bg-emerald-500 px-2 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-emerald-400"
          >
            Approve
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              gateHandler?.("reject");
            }}
            className="flex-1 rounded-md bg-red-500/90 px-2 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-red-500"
          >
            Reject
          </button>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className={HANDLE_BASE} />
    </div>
  );
}
